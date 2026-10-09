/**
 * 主循环：heartbeat（5s）+ claim 长轮询（wait=20）+ 执行，断线指数退避 1s→30s。
 *
 * - heartbeat 与 claim 并行两个循环（claim 长轮询阻塞期间心跳不能停）
 * - 网络错误后退避 1s→2s→4s→…→30s，恢复后重置
 * - 设备 token 被撤销（401）→ fail-loud 停循环
 * - shutdown：停止 claim 新任务；当前 invocation 走协作式中止；循环退出后由调用方回收 Provider
 */
import type { ApiClient, ClaimedInvocation } from './apiClient.js'
import { AbortLoopError, ApiError, DeviceRevokedError, NetworkError } from './apiClient.js'
import { deviceCapabilities, RUNTIME_VERSION } from './config.js'
import { runInvocation, type RunnerDeps } from './invocationRunner.js'
import { manifestDigest } from './manifestVerifier.js'
import { replayPendingOutbox } from './resultOutbox.js'
import { logError, logInfo } from './log.js'

export interface PollLoopOptions {
  api: ApiClient
  runnerDeps: Omit<RunnerDeps, 'api' | 'shutdownSignal' | 'onEvent'>
  heartbeatIntervalMs?: number
  claimWaitSeconds?: number
  backoffBaseMs?: number
  backoffMaxMs?: number
  onEvent?: (message: string) => void
  /** 测试钩子：每次心跳尝试的时间戳（退避序列断言用） */
  onHeartbeatAttempt?: (timestamp: number) => void
  onInvocationStart?: (inv: ClaimedInvocation) => void
  onInvocationEnd?: (inv: ClaimedInvocation) => void
  capabilities?: () => Record<string, unknown>
  canClaim?: () => boolean
  onConnection?: (state: 'online' | 'offline' | 'revoked') => void
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve()
  return new Promise((resolve) => {
    const done = () => { signal.removeEventListener('abort', abort); resolve() }
    const timer = setTimeout(done, ms)
    const abort = () => {
      clearTimeout(timer)
      done()
    }
    signal.addEventListener('abort', abort, { once: true })
  })
}

export class PollLoop {
  private readonly opts: Required<Pick<PollLoopOptions, 'heartbeatIntervalMs' | 'claimWaitSeconds' | 'backoffBaseMs' | 'backoffMaxMs'>> & PollLoopOptions
  private readonly controller = new AbortController()
  private readonly claimController = new AbortController()
  private readonly runnerShutdown = new AbortController()
  private stopped = false
  private runningInvocation = false
  private accepting = true

  constructor(options: PollLoopOptions) {
    this.opts = {
      heartbeatIntervalMs: 5_000,
      claimWaitSeconds: 20,
      backoffBaseMs: 1_000,
      backoffMaxMs: 30_000,
      ...options,
    }
  }

  get isStopped(): boolean {
    return this.stopped
  }

  /** 请求停止：停止 claim 新任务；当前 invocation 协作式中止 */
  shutdown(): void {
    if (this.stopped) return
    this.stopped = true
    this.runnerShutdown.abort()
    this.controller.abort()
    this.claimController.abort()
  }

  /** Stop admission without cancelling an accepted invocation or its result delivery. */
  stopClaiming(): void {
    this.accepting = false
    this.claimController.abort()
  }

  /** Heartbeats remain alive until the accepted invocation has drained. */
  async drain(): Promise<void> {
    this.stopClaiming()
    if (this.claimPromise) await this.claimPromise
    this.stopped = true
    this.controller.abort()
  }

  private claimPromise: Promise<void> | null = null

  async run(): Promise<void> {
    // v2 结果 outbox 启动重投（§5.3/R14：pending 条目用原 claim 身份重投；仅 2xx 持久
    // ACK 才删除条目，确定性 4xx 标记 gave_up 保留待对账、跳过重投）。有界尝试，仍失败
    // 的条目保留磁盘，不阻塞主循环启动。
    const outbox = this.opts.runnerDeps.resultOutbox
    if (outbox) {
      try {
        await replayPendingOutbox(this.opts.api, outbox, {
          retryBaseMs: this.opts.backoffBaseMs,
          retryMaxMs: this.opts.backoffMaxMs,
          maxAttempts: 5,
          shutdownSignal: this.controller.signal,
          emit: (msg) => this.emit(msg),
        })
      } catch (err) {
        logError(`result outbox 启动重投异常（不阻塞主循环）: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
    this.claimPromise = this.claimLoop()
    await Promise.all([this.heartbeatLoop(), this.claimPromise])
  }

  private emit(msg: string): void {
    if (this.opts.onEvent) this.opts.onEvent(msg)
    else logInfo(msg)
  }

  private async heartbeatLoop(): Promise<void> {
    const { api, heartbeatIntervalMs, backoffBaseMs, backoffMaxMs } = this.opts
    let backoff = backoffBaseMs
    while (!this.stopped) {
      this.opts.onHeartbeatAttempt?.(Date.now())
      try {
        const hb = await api.heartbeat({
          runtime_version: RUNTIME_VERSION,
          capabilities: this.opts.capabilities?.() ?? deviceCapabilities(),
          manifest_digest: manifestDigest(),
        }, this.controller.signal)
        backoff = backoffBaseMs
        this.opts.onConnection?.('online')
        if (!hb.selected) {
          this.emit('心跳成功（提示：当前设备未被 Web 端选定，任务不会下发到本设备）')
        }
      } catch (err) {
        if (err instanceof AbortLoopError || this.stopped) break
        if (err instanceof DeviceRevokedError) {
          this.opts.onConnection?.('revoked')
          logError('设备 token 已失效或被撤销，主循环停止。请重新 pair。')
          this.shutdown()
          break
        }
        const detail = err instanceof Error ? err.message : String(err)
        this.opts.onConnection?.('offline')
        this.emit(`心跳失败（${detail}），${backoff}ms 后重试`)
        await sleep(backoff, this.controller.signal)
        backoff = Math.min(backoff * 2, backoffMaxMs)
        continue
      }
      await sleep(heartbeatIntervalMs, this.controller.signal)
    }
  }

  private async claimLoop(): Promise<void> {
    const { api, claimWaitSeconds, backoffBaseMs, backoffMaxMs } = this.opts
    let backoff = backoffBaseMs
    while (!this.stopped && this.accepting) {
      if (this.opts.canClaim && !this.opts.canClaim()) {
        await sleep(this.opts.heartbeatIntervalMs, this.claimController.signal)
        continue
      }
      let inv: ClaimedInvocation | null = null
      try {
        inv = await api.claim(claimWaitSeconds, this.claimController.signal)
        backoff = backoffBaseMs
      } catch (err) {
        if (err instanceof AbortLoopError || this.stopped || !this.accepting) break
        if (err instanceof DeviceRevokedError) {
          this.opts.onConnection?.('revoked')
          logError('设备 token 已失效或被撤销，主循环停止。请重新 pair。')
          this.shutdown()
          break
        }
        if (err instanceof ApiError || err instanceof NetworkError) {
          const detail = err.message
          this.emit(`领取任务失败（${detail}），${backoff}ms 后重试`)
          await sleep(backoff, this.claimController.signal)
          backoff = Math.min(backoff * 2, backoffMaxMs)
          continue
        }
        throw err
      }
      if (!inv) continue
      // A successful claim response is already accepted even if stop raced with its return.
      if (this.stopped) break
      this.runningInvocation = true
      this.emit(`领取 invocation ${inv.invocation_id} tool=${inv.tool_name}`)
      this.opts.onInvocationStart?.(inv)
      try {
        await runInvocation(inv, {
          ...this.opts.runnerDeps,
          api,
          shutdownSignal: this.runnerShutdown.signal,
          onEvent: this.opts.onEvent,
        })
      } catch (err) {
        // runner 内部已兜底，此处防御性捕获，保证主循环不死
        logError(`invocation ${inv.invocation_id} 执行器异常: ${err instanceof Error ? err.message : String(err)}`)
      } finally {
        this.runningInvocation = false
        this.opts.onInvocationEnd?.(inv)
      }
    }
  }

  /** 测试观测用：当前是否有 invocation 在执行 */
  get hasRunningInvocation(): boolean {
    return this.runningInvocation
  }
}
