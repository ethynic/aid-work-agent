/**
 * MCP stdio Provider 生命周期管理。
 *
 * - spawn `node <entry> mcp --stdio`（不用 shell，entry 固定为本地 config，禁止云端下发）
 * - 单飞：进程级同时只执行一个 tool call（云端协议一设备一任务，本地仍兜底）
 * - 崩溃处理：MCP 进程退出/stdio 断开 → 进行中的调用抛 ProviderCrashError，
 *   随后 ensureStarted() 自动 respawn 供下一个 invocation
 * - 回收：shutdown() 关 stdin（SDK close 内部 2s 后 SIGTERM），限时 5s 未退出再 SIGKILL
 */
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { logError, logInfo, logProviderChunk } from './log.js'

export class ProviderCrashError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ProviderCrashError'
  }
}

export class ProviderBusyError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ProviderBusyError'
  }
}

export interface ProviderProgress {
  progress?: number
  total?: number
  message?: string
}

export interface ProviderCallOptions {
  onProgress?: (p: ProviderProgress) => void
  signal?: AbortSignal
  /** 单次调用超时（默认 10 分钟；进度通知不重置） */
  timeoutMs?: number
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

export class ProviderManager {
  private readonly entry: string
  private readonly shutdownTimeoutMs: number
  private client: Client | null = null
  private transport: StdioClientTransport | null = null
  private crashed = false
  private busy = false
  private shuttingDown = false

  constructor(entry: string, opts: { shutdownTimeoutMs?: number } = {}) {
    this.entry = entry
    this.shutdownTimeoutMs = opts.shutdownTimeoutMs ?? 5_000
  }

  get childPid(): number | null {
    return this.transport?.pid ?? null
  }

  get isRunning(): boolean {
    return this.client !== null && !this.crashed
  }

  /** 启动 Provider（已运行则复用；崩溃后自动 respawn） */
  async ensureStarted(): Promise<void> {
    if (this.shuttingDown) throw new ProviderCrashError('ProviderManager 正在关闭，不再启动 Provider')
    if (this.client && !this.crashed) return
    await this.disposeDead()

    const transport = new StdioClientTransport({
      command: process.execPath,
      args: [this.entry, 'mcp', '--stdio'],
      // pipe 而非 inherit：[boss-mcp] 工具调用行经 logProviderChunk 转发（控制台 + 文件落盘），
      // 脱离 start_runtime.bat 重定向启动时排障证据不再丢失（2026-09-01）
      stderr: 'pipe',
    })
    const client = new Client(
      { name: 'agent-tool-runtime', version: '0.1.0' },
      { capabilities: {} },
    )
    this.crashed = false
    // 注意：不能设置 transport.onclose（会被 Protocol.connect 覆盖），用 client.onclose 观测断开
    client.onclose = () => {
      this.crashed = true
      logInfo('Provider 进程已退出/stdio 断开')
    }
    try {
      await client.connect(transport)
    } catch (err) {
      // connect 失败时 transport 可能已 spawn 子进程（如进程起来了但握手失败/超时），
      // 此时 this.transport 尚未赋值，disposeDead 回收不到，必须就地关闭防止进程泄漏
      try {
        await transport.close()
      } catch {
        // 忽略关闭错误
      }
      const detail = err instanceof Error ? err.message : String(err)
      throw new ProviderCrashError(`Provider 启动失败（entry=${this.entry}）: ${detail}`)
    }
    this.client = client
    this.transport = transport
    // provider stderr（[boss-mcp] 启动/工具行）转发：控制台 + 日志文件（PassThrough 缓冲，
    // spawn 早于 connect 的行 attach 后仍可收到）
    transport.stderr?.on('data', (chunk: Buffer) => logProviderChunk(chunk.toString()))
    logInfo(`Provider 已启动 pid=${transport.pid ?? '?'} entry=${this.entry}`)
  }

  /**
   * 调用 Provider 工具，返回 structuredContent（boss OperationResult 形态）。
   * Provider 崩溃 → ProviderCrashError；并发占用 → ProviderBusyError。
   */
  async callTool(name: string, args: Record<string, unknown>, opts: ProviderCallOptions = {}): Promise<Record<string, unknown>> {
    if (this.busy) {
      throw new ProviderBusyError(`Provider 正在执行其他调用（单飞兜底）`)
    }
    this.busy = true
    try {
      await this.ensureStarted()
      const client = this.client!
      let result
      try {
        result = await client.callTool({ name, arguments: args }, undefined, {
          onprogress: opts.onProgress
            ? (p) => opts.onProgress!({ progress: p.progress, total: p.total, message: p.message })
            : undefined,
          signal: opts.signal,
          timeout: opts.timeoutMs ?? 10 * 60 * 1000,
          resetTimeoutOnProgress: true,
        })
      } catch (err) {
        if (this.crashed) {
          throw new ProviderCrashError(`Provider 执行中崩溃: ${err instanceof Error ? err.message : String(err)}`)
        }
        throw err
      }
      const structured = (result as { structuredContent?: Record<string, unknown> }).structuredContent
      if (structured && typeof structured === 'object') return structured
      // 兜底：解析 text content 中的 JSON
      const content = (result as { content?: Array<{ type: string; text?: string }> }).content
      const text = content?.find((c) => c.type === 'text')?.text
      if (text) {
        try {
          return JSON.parse(text) as Record<string, unknown>
        } catch {
          // fallthrough
        }
      }
      return { success: false, code: 'INTERNAL_ERROR', message: 'Provider 返回无法解析', effect: 'unknown' }
    } finally {
      this.busy = false
    }
  }

  private async disposeDead(): Promise<void> {
    if (!this.client && !this.transport) return
    const pid = this.transport?.pid ?? null
    try {
      await this.client?.close()
    } catch {
      // 忽略关闭错误
    }
    this.client = null
    this.transport = null
    if (pid !== null) await this.ensureChildReaped(pid, this.shutdownTimeoutMs)
  }

  /** 关闭并限时回收子进程（stdin 关闭 → SDK 2s SIGTERM → 本层限时 SIGKILL） */
  async shutdown(): Promise<void> {
    this.shuttingDown = true
    await this.disposeDead()
  }

  private async ensureChildReaped(pid: number, timeoutMs: number): Promise<void> {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline && isProcessAlive(pid)) {
      await sleep(50)
    }
    if (isProcessAlive(pid)) {
      logError(`Provider 子进程 pid=${pid} ${timeoutMs}ms 内未退出，SIGKILL`)
      try {
        process.kill(pid, 'SIGKILL')
      } catch {
        // 已退出
      }
      // 再等一次确保死掉
      const killDeadline = Date.now() + 2_000
      while (Date.now() < killDeadline && isProcessAlive(pid)) {
        await sleep(50)
      }
      if (isProcessAlive(pid)) {
        logError(`Provider 子进程 pid=${pid} SIGKILL 后仍存活（异常，需人工排查）`)
      }
    }
  }
}
