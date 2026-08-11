/**
 * 单 invocation 执行器：started → desktopCheck → MCP call（进度转发 + 取消轮询）→ result。
 *
 * 关键策略（实施规格 §3/§4/§6）：
 * - 执行中每 2s 或每个业务进度回传 progress；ack.cancel=true → 协作式中止（AbortController）
 * - 写动作断线：本地继续完成原子动作，恢复后回传终态；无法证明效果 → EXECUTION_UNKNOWN
 * - 终态 result 幂等，断网指数退避重试直到成功（或 shutdown 强停时 fail-loud 放弃）
 * - claim_token 仅内存持有，不落盘、不打印
 */
import type { ApiClient, ClaimedInvocation, InvocationResultPayload } from './apiClient.js'
import { ApiError, DeviceRevokedError, NetworkError } from './apiClient.js'
import { isToolAllowed, isWriteTool } from './manifestVerifier.js'
import type { ProviderManager } from './providerManager.js'
import { ProviderCrashError } from './providerManager.js'
import { logError, logInfo } from './log.js'

export interface RunnerDeps {
  api: ApiClient
  provider: ProviderManager
  /** 桌面可交互检测（默认由调用方注入真实实现；测试注入 mock） */
  desktopCheck: () => Promise<boolean>
  /** 进度续租间隔（默认 2000ms，规格 §3） */
  progressIntervalMs?: number
  /** 终态/started 网络重试基数（默认 1000ms） */
  retryBaseMs?: number
  retryMaxMs?: number
  /** shutdown 信号：触发协作式中止 + 终态回传限时 */
  shutdownSignal?: AbortSignal
  onEvent?: (message: string) => void
}

interface FinalResult {
  success: boolean
  code?: string
  message?: string
  effect?: string
  data?: Record<string, unknown>
  retryable?: boolean
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 指数退避等待，shutdown 时立即返回 false */
async function backoffSleep(ms: number, shutdownSignal?: AbortSignal): Promise<boolean> {
  if (shutdownSignal?.aborted) return false
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(true), ms)
    shutdownSignal?.addEventListener('abort', () => {
      clearTimeout(timer)
      resolve(false)
    }, { once: true })
  })
}

/** 进度转发器：业务进度立即发 + 每 progressIntervalMs 续租；串行化发送，cancel 透传 */
class ProgressForwarder {
  private last: { stage?: string; current?: number; total?: number; message?: string } = { stage: 'running' }
  private lastSentAt = 0
  private chain: Promise<void> = Promise.resolve()
  private timer: ReturnType<typeof setInterval> | null = null
  private cloudOffline = false
  cancelled = false

  constructor(
    private readonly api: ApiClient,
    private readonly invocationId: string,
    private readonly claimToken: string,
    private readonly intervalMs: number,
    private readonly onCancel: () => void,
    private readonly emit: (msg: string) => void,
  ) {}

  start(): void {
    this.timer = setInterval(() => {
      if (Date.now() - this.lastSentAt >= this.intervalMs) {
        this.push({})
      }
    }, this.intervalMs)
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  }

  /** 更新最近进度并入队一次发送（串行链保证不并发、不乱序） */
  push(update: { stage?: string; current?: number; total?: number; message?: string }): void {
    this.last = { ...this.last, ...update }
    this.chain = this.chain.then(() => this.doSend())
  }

  /** 等待所有已入队进度发送完成（终态前 flush） */
  async flush(): Promise<void> {
    await this.chain
  }

  private async doSend(): Promise<void> {
    try {
      const ack = await this.api.progress(this.invocationId, {
        claim_token: this.claimToken,
        stage: this.last.stage,
        current: this.last.current,
        total: this.last.total,
        message: this.last.message,
      })
      this.lastSentAt = Date.now()
      if (this.cloudOffline) {
        this.cloudOffline = false
        this.emit('云端连接已恢复（进度回传）')
      }
      if (ack.cancel && !this.cancelled) {
        this.cancelled = true
        this.emit('收到云端取消请求，协作式中止')
        this.onCancel()
      }
    } catch (err) {
      if (err instanceof NetworkError || err instanceof DeviceRevokedError) {
        // 断线策略：进度丢失可容忍，继续本地执行，终态会重试回传
        if (!this.cloudOffline) {
          this.cloudOffline = true
          this.emit('云端连接中断（进度暂无法回传，本地继续执行）')
        }
        return
      }
      if (err instanceof ApiError && err.status === 404) {
        // 租约过期/状态不允许：云端已不再接受进度，停止续租（终态大概率也被拒，但仍尝试）
        this.emit('云端拒绝进度回传（租约可能过期），停止续租')
        this.stop()
        return
      }
      logError(`进度回传失败: ${err instanceof Error ? err.message : String(err)}`)
    }
  }
}

export async function runInvocation(inv: ClaimedInvocation, deps: RunnerDeps): Promise<void> {
  const emit = deps.onEvent ?? logInfo
  const retryBase = deps.retryBaseMs ?? 1_000
  const retryMax = deps.retryMaxMs ?? 30_000
  const writeTool = isWriteTool(inv.tool_name)

  /** 终态回传：断网指数退避重试直到成功；shutdown 强停或云端明确拒绝时 fail-loud 放弃 */
  const sendFinalResult = async (final: FinalResult): Promise<void> => {
    const payload: InvocationResultPayload = { claim_token: inv.claim_token, ...final }
    let backoff = retryBase
    for (;;) {
      try {
        await deps.api.result(inv.invocation_id, payload)
        emit(`invocation ${inv.invocation_id} 终态已回传 success=${final.success} code=${final.code ?? '-'} effect=${final.effect ?? '-'}`)
        return
      } catch (err) {
        if (err instanceof ApiError) {
          logError(`invocation ${inv.invocation_id} 终态被云端拒绝（HTTP ${err.status}），放弃重试: ${err.message}`)
          return
        }
        if (err instanceof DeviceRevokedError) {
          logError(`invocation ${inv.invocation_id} 终态回传失败：设备已撤销，结果丢失（fail-loud）`)
          return
        }
        emit(`终态回传失败（网络），${backoff}ms 后重试`)
        const proceed = await backoffSleep(backoff, deps.shutdownSignal)
        if (!proceed) {
          logError(`invocation ${inv.invocation_id} 终态未能回传（Runtime 关闭中），结果丢失（fail-loud）`)
          return
        }
        backoff = Math.min(backoff * 2, retryMax)
      }
    }
  }

  // 1. manifest 校验：tool_name 不在受信清单 → 直接回 TOOL_NOT_ALLOWED（规格 §4 决策）
  if (!isToolAllowed(inv.tool_name)) {
    emit(`invocation ${inv.invocation_id} tool=${inv.tool_name} 不在受信 manifest，拒绝执行`)
    await sendFinalResult({
      success: false,
      code: 'TOOL_NOT_ALLOWED',
      message: `工具 ${inv.tool_name} 不在本地受信 manifest 清单内`,
      effect: 'none',
      retryable: false,
    })
    return
  }

  // 2. started（幂等，网络重试；失败则放弃执行——写动作未开始无副作用，租约过期云端回收）
  {
    let backoff = retryBase
    for (;;) {
      try {
        await deps.api.started(inv.invocation_id, inv.claim_token)
        break
      } catch (err) {
        if (err instanceof ApiError) {
          logError(`invocation ${inv.invocation_id} started 被云端拒绝（HTTP ${err.status}），放弃执行: ${err.message}`)
          return
        }
        if (err instanceof DeviceRevokedError) {
          logError(`invocation ${inv.invocation_id} started 失败：设备已撤销`)
          return
        }
        emit(`started 失败（网络），${backoff}ms 后重试`)
        const proceed = await backoffSleep(backoff, deps.shutdownSignal)
        if (!proceed) return
        backoff = Math.min(backoff * 2, retryMax)
      }
    }
  }
  emit(`invocation ${inv.invocation_id} 已开始执行 tool=${inv.tool_name}`)

  // 3. 锁屏检测（写动作前置；规格 §6）
  if (writeTool) {
    const interactive = await deps.desktopCheck()
    if (!interactive) {
      await sendFinalResult({
        success: false,
        code: 'DESKTOP_NOT_INTERACTIVE',
        message: 'Windows 桌面锁屏或不可交互，请解锁后重试',
        effect: 'none',
        retryable: true,
      })
      return
    }
  }

  // 4. MCP 调用（进度转发 + 取消 + shutdown 中止）
  const abortController = new AbortController()
  const onShutdownAbort = () => abortController.abort()
  deps.shutdownSignal?.addEventListener('abort', onShutdownAbort, { once: true })

  const forwarder = new ProgressForwarder(
    deps.api,
    inv.invocation_id,
    inv.claim_token,
    deps.progressIntervalMs ?? 2_000,
    () => abortController.abort(),
    emit,
  )
  forwarder.start()

  let settled: Record<string, unknown> | null = null
  let callError: Error | null = null
  try {
    settled = await deps.provider.callTool(inv.tool_name, inv.arguments, {
      signal: abortController.signal,
      onProgress: (p) => {
        const stage = extractStage(p.message)
        forwarder.push({
          stage,
          current: typeof p.progress === 'number' ? Math.round(p.progress) : undefined,
          total: typeof p.total === 'number' ? Math.round(p.total) : undefined,
          message: p.message,
        })
      },
    })
  } catch (err) {
    callError = err instanceof Error ? err : new Error(String(err))
  } finally {
    forwarder.stop()
    deps.shutdownSignal?.removeEventListener('abort', onShutdownAbort)
  }
  await forwarder.flush()

  // 5. 终态映射
  if (forwarder.cancelled) {
    // 云端取消优先：协作式中止后回 cancelled 终态（保留已产生的局部 effect）
    await sendFinalResult({
      success: false,
      code: 'CANCELLED',
      message: '云端请求取消，已协作式中止',
      effect: typeof settled?.['effect'] === 'string' ? (settled['effect'] as string) : 'none',
      retryable: false,
    })
    return
  }
  if (deps.shutdownSignal?.aborted) {
    await sendFinalResult({
      success: false,
      code: 'CANCELLED',
      message: 'Runtime 关闭，任务已中止',
      effect: typeof settled?.['effect'] === 'string' ? (settled['effect'] as string) : 'none',
      retryable: true,
    })
    return
  }
  if (callError) {
    if (callError instanceof ProviderCrashError) {
      // Provider 崩溃：写动作无法证明效果 → EXECUTION_UNKNOWN；只读 → INTERNAL_ERROR（规格 §4）
      if (writeTool) {
        await sendFinalResult({
          success: false,
          code: 'EXECUTION_UNKNOWN',
          message: `Provider 执行中崩溃，无法确认动作效果: ${callError.message}`,
          effect: 'unknown',
          retryable: false,
        })
      } else {
        await sendFinalResult({
          success: false,
          code: 'INTERNAL_ERROR',
          message: `Provider 执行中崩溃: ${callError.message}`,
          effect: 'none',
          retryable: true,
        })
      }
      return
    }
    await sendFinalResult({
      success: false,
      code: 'INTERNAL_ERROR',
      message: `Provider 调用失败: ${callError.message}`,
      effect: writeTool ? 'unknown' : 'none',
      retryable: !writeTool,
    })
    return
  }

  const r = settled ?? {}
  const data: Record<string, unknown> = { ...(r['data'] as Record<string, unknown> | undefined) }
  if (typeof r['run_id'] === 'string') data['run_id'] = r['run_id']
  await sendFinalResult({
    success: Boolean(r['success']),
    code: typeof r['code'] === 'string' ? r['code'] : undefined,
    message: typeof r['message'] === 'string' ? r['message'] : undefined,
    effect: typeof r['effect'] === 'string' ? r['effect'] : undefined,
    data: Object.keys(data).length > 0 ? data : undefined,
    retryable: typeof r['retryable'] === 'boolean' ? r['retryable'] : undefined,
  })
}

/** 从 Provider 进度文案提取 stage（boss 约定 "stage message" 首词为 stage） */
function extractStage(message?: string): string | undefined {
  if (!message) return undefined
  const first = message.trim().split(/\s+/)[0]
  return first || undefined
}
