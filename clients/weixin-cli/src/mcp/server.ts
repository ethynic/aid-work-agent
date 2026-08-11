/**
 * aid-weixin MCP server（stdio，设计 §3.2/§8 / 上位规范 §3.3）。
 *
 * - stdout 只承载 MCP 协议：本进程在 mcp 模式下禁止任何 console.log，日志只写 stderr
 *   且脱敏（不输出参数值/正文/剪贴板内容，只记 tool/run_id/code/effect/时长）。
 * - 单飞 + 跨进程互斥：进程级只允许一个 tool call 在执行；命名管道
 *   \\.\pipe\AidWorkAgent.AidWeixin.<scope> 防止两个 aid-weixin 进程并行控制微信。
 *   占用中（任一）立即返回 BUSY 结构化结果（不是 MCP 协议错误）。
 * - 取消释放锁：Host 取消（AbortSignal）或 operation 结束时，finally 释放命名管道。
 * - 写动作零重试：本层与 operation 层都没有任何自动重试逻辑。
 * - Host 关闭 stdin 时进程退出（不留孤儿）。
 */
import { randomUUID } from 'node:crypto'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { TOOL_DEFS } from './toolDefs.js'
import { providerVersion } from './manifest.js'
import { getOperationEntry } from '../operations/registry.js'
import { failResult, type OperationResult } from '../operations/types.js'
import { NamedMutex, resolveMutexScope } from '../platform/namedMutex.js'
import { redactSensitive } from '../security/redaction.js'

/**
 * server instructions（上位规范 §5.4 / 设计 §8：前 512 字符内含关键前提、副作用和写动作上限）
 */
const INSTRUCTIONS =
  '微信操作 Provider（当前版本仅提供只读环境探测 weixin_probe）。前提：仅 Windows（win32-x64）；' +
  '需要已登录且未锁屏的微信 Windows 客户端（Weixin.exe）与交互桌面会话。' +
  '副作用预告：后续版本的自动化操作会占用前台窗口与剪贴板，操作期间请勿移动鼠标、勿操作键盘；' +
  '写动作（发消息、关注公众号）单次单目标且有硬上限，结果 effect=unknown 时系统不会自动重试，请人工确认。' +
  '结果约定：structuredContent 含 success/code/message/effect/data/retryable/run_id，' +
  'effect ∈ none|applied|partial|unknown。'

/** stderr 日志（脱敏）：不含参数值/正文，只记操作与结果元数据 */
function logLine(msg: string): void {
  process.stderr.write(`[aid-weixin-mcp] ${redactSensitive(msg)}\n`)
}

/**
 * 单飞 + 跨进程互斥执行器（导出以便单测）：
 * 进程内已有操作在跑 → BUSY；命名管道被其它进程占用 → BUSY；否则占用执行，finally 释放。
 */
export async function runExclusive<T extends OperationResult>(
  running: { current: string | null },
  mutex: NamedMutex,
  toolName: string,
  fn: () => Promise<T>,
): Promise<T | OperationResult> {
  if (running.current !== null) {
    return failResult(
      randomUUID(),
      'BUSY',
      `另一个操作「${running.current}」正在执行中（共享前台/剪贴板，单飞），请等其完成后重试`,
      'none',
    )
  }
  running.current = toolName
  let acquired = false
  try {
    try {
      acquired = await mutex.acquire()
    } catch (err) {
      // 互斥占用本身失败（非 EADDRINUSE 的系统错误）：返回结构化 INTERNAL_ERROR，
      // 不让原始异常穿透 handler 变成 Host 侧的非结构化 isError
      return failResult(
        randomUUID(),
        'INTERNAL_ERROR',
        `跨进程互斥占用失败：${err instanceof Error ? err.message : String(err)}`,
        'none',
      )
    }
    if (!acquired) {
      return failResult(
        randomUUID(),
        'BUSY',
        '另一个 aid-weixin 进程正在操作微信（跨进程互斥），请等其完成后重试',
        'none',
      )
    }
    return await fn()
  } finally {
    if (acquired) await mutex.release()
    running.current = null
  }
}

export async function startMcpServer(): Promise<void> {
  const server = new McpServer(
    { name: 'aid-weixin', version: providerVersion() },
    { instructions: INSTRUCTIONS },
  )
  /** 进程级单飞锁：写动作与读动作都单飞 */
  const running: { current: string | null } = { current: null }
  /** 跨进程互斥（每个 tool call 占用/释放一次，进程死亡由 OS 自动回收） */
  const mutex = new NamedMutex(resolveMutexScope())

  for (const def of TOOL_DEFS) {
    // 启动期一致性校验：toolDef 未注册 operation 时此处直接 throw，进程启动失败（fail-loud）
    const { operation } = getOperationEntry(def.name)
    server.registerTool(
      def.name,
      {
        title: def.title,
        description: def.description,
        inputSchema: def.zodShape,
        annotations: def.annotations,
      },
      async (args, extra) => {
        const startedAt = Date.now()
        const progressToken = extra._meta?.progressToken
        const result = await runExclusive(running, mutex, def.name, () =>
          operation.execute(args ?? {}, {
            signal: extra.signal,
            progress: (p) => {
              // progress 是可选增强：Host 不传 progressToken 则跳过，最终结果不受影响
              if (progressToken === undefined || progressToken === null) return
              void extra
                .sendNotification({
                  method: 'notifications/progress',
                  params: {
                    progressToken,
                    progress: p.current ?? 0,
                    ...(p.total !== undefined ? { total: p.total } : {}),
                    message: `${p.stage} ${p.message}`.trim(),
                  },
                })
                .catch(() => {})
            },
          }),
        )
        logLine(
          `tool=${def.name} run_id=${result.run_id} success=${result.success} code=${result.code} ` +
            `effect=${result.effect} elapsed_ms=${Date.now() - startedAt}`,
        )
        return {
          content: [{ type: 'text' as const, text: JSON.stringify(result) }],
          structuredContent: result as unknown as Record<string, unknown>,
        }
      },
    )
  }

  const transport = new StdioServerTransport()
  // Host 关闭 stdin（transport close）时退出进程，不留孤儿
  transport.onclose = () => {
    logLine('stdin closed, exiting')
    process.exit(0)
  }
  await server.connect(transport)
  logLine(`aid-weixin MCP server started (tools=${TOOL_DEFS.length}, mutex_scope=${mutex.scope})`)
}
