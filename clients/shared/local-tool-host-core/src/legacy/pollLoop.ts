/**
 * 主循环：heartbeat（5s）+ claim 长轮询（wait=20）+ 执行，断线指数退避 1s→30s。
 *
 * - heartbeat 与 claim 并行两个循环（claim 长轮询阻塞期间心跳不能停）
 * - 网络错误后退避 1s→2s→4s→…→30s，恢复后重置
 * - 设备 token 被撤销（401）→ fail-loud 停循环
 * - shutdown：停止 claim 新任务；当前 invocation 走协作式中止；循环退出后由调用方回收 Provider
 * - 并行接单（契约 2.20）：串行占用期间领取带 parallel_only（服务端支持时），并行作业
 *   后台执行不阻塞领取循环；drain/shutdown 等待全部在途（串行 + 并行）调用完成
 */
import type { ApiClient, ClaimedInvocation } from './apiClient.js'
import { AbortLoopError, ApiError, DeviceRevokedError, NetworkError } from './apiClient.js'
import { deviceCapabilities, RUNTIME_VERSION } from './config.js'
import { runInvocation, type RunnerDeps } from './invocationRunner.js'
import { manifestDigest } from './manifestVerifier.js'
import { replayPendingOutbox } from './resultOutbox.js'
import { pruneCompletedJournals } from './completionProof.js'
import { dirname } from 'node:path'
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
  /** 契约 2.20：本设备心跳已上报的并行工具映射（provider_key → tool 名单），与能力上报
   *  同源（runtimeHost 装配传入）。用于判定领到的作业是否可后台并行执行；缺省/空 =
   *  全部按串行执行（现状，无声明插件路径行为不变）。 */
  parallelTools?: Record<string, readonly string[]>
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
  private replayPromise: Promise<void> | null = null
  private nextEvidenceCleanupAt = 0
  private evidenceCleanupCursor = ''
  private accepting = true
  /** 心跳读到服务端 parallel_claim 支持标记（契约 2.20）；旧服务端恒 false → 行为与现状一致 */
  private serverParallelClaim = false
  /** 串行占用：当前有串行 invocation 领取后未到终态（执行或停止中）。
   *  仅影响领取参数（并行模式）；并行作业完成与否不改变该状态。 */
  private serialOccupied = false
  /** 在途 runner（串行 + 并行）：tracked promise → invocation_id；drain/shutdown 等待全部结束 */
  private readonly inFlight = new Map<Promise<void>, string>()

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

  /** Heartbeats remain alive until every accepted invocation (serial + parallel) has drained. */
  async drain(): Promise<void> {
    this.stopClaiming()
    if (this.claimPromise) await this.claimPromise
    // 等待全部在途（串行 + 并行）runner 结束后再停心跳——租约续期/终态回传依赖心跳循环存活
    await Promise.all([...this.inFlight.keys()])
    this.stopped = true
    this.controller.abort()
  }

  private claimPromise: Promise<void> | null = null

  async run(): Promise<void> {
    // Begin heartbeats/admission immediately. Old receipts are drained in bounded,
    // single-flight passes after successful heartbeats, using their original claims.
    this.claimPromise = this.claimLoop()
    try {
      await Promise.all([this.heartbeatLoop(), this.claimPromise])
    } finally {
      await this.replayPromise
    }
  }

  private replayResults(): void {
    const outbox = this.opts.runnerDeps.resultOutbox
    if (!outbox || this.replayPromise || this.stopped) return
    this.replayPromise = (async () => {
      try {
        await replayPendingOutbox(this.opts.api, outbox, {
          retryBaseMs: this.opts.backoffBaseMs,
          retryMaxMs: this.opts.backoffMaxMs,
          maxAttempts: 1,
          maxEntries: 8,
          canReplay: id => !this.isInvocationActive(id),
          shutdownSignal: this.controller.signal,
          emit: () => {},
        })
        if (!this.stopped && Date.now() >= this.nextEvidenceCleanupAt) {
          this.nextEvidenceCleanupAt = Date.now() + 60 * 60 * 1000
          const cleanup = pruneCompletedJournals(dirname(outbox.dir), Date.now(), this.evidenceCleanupCursor)
          this.evidenceCleanupCursor = cleanup.cursor
          if (cleanup.failed) logError(`已闭合journal清理失败 ${cleanup.failed} 条，保留其余证据`)
        }
      } catch (err) {
        logError(`result outbox 重投异常（保留原回执）: ${err instanceof Error ? err.message : String(err)}`)
      }
    })().finally(() => { this.replayPromise = null })
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
        // 契约 2.20 支持协商：服务端心跳声明支持 parallel_only 才启用并行领取参数；
        // 旧服务端（无标记）恒 false，领取行为与现状完全一致
        this.serverParallelClaim = hb.parallel_claim === true
        this.replayResults()
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

  /**
   * 领取循环（契约 2.20 状态机）：
   * - 无串行占用 → 空参数领取（串行/并行皆可，服务端 FIFO 不变）。
   * - 串行占用且服务端支持 → 领取请求带 parallel_only=true（服务端只派并行可领作业，
   *   队列中的串行作业原地等待）；串行占用解除后恢复空参数。
   * - 领到并行作业 → 后台执行（tracked），立即继续下一轮领取；并行完成不解除串行占用。
   * - 领到串行作业：服务端支持时同样 tracked 执行、领取循环继续长轮询（仅并行可领，
   *   串行互斥由服务端 parallel_only 过滤保证）；服务端不支持时沿现状——阻塞领取循环
   *   至串行完成（无 parallel_only 参数，继续领取只会立刻领到下一条串行作业）。
   */
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
        const parallelOnly = this.serialOccupied && this.serverParallelClaim
        inv = await api.claim(claimWaitSeconds, parallelOnly, this.claimController.signal)
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
      const parallel = this.isParallelInvocation(inv)
      this.emit(`领取 invocation ${inv.invocation_id} tool=${inv.tool_name}${parallel ? '（并行）' : ''}`)
      this.opts.onInvocationStart?.(inv)
      if (parallel) {
        this.startRunner(inv, { parallel: true, releaseSerial: false })
        continue
      }
      this.serialOccupied = true
      const runner = this.startRunner(inv, { parallel: false, releaseSerial: true })
      if (!this.serverParallelClaim) {
        // 服务端支持标记随心跳到达（可能晚于本次领取）：等待串行完成，但服务端一旦声明
        // 支持即停止等待、恢复带 parallel_only 的领取；旧服务端永不声明 → 等待至完成（现状）
        await Promise.race([runner, this.parallelSupportSignal()])
      }
    }
  }

  /** 等待心跳读到服务端 parallel_claim 支持标记（串行占用阻塞等待的中断条件） */
  private async parallelSupportSignal(): Promise<void> {
    while (!this.stopped && this.accepting && !this.serverParallelClaim) {
      await sleep(25, this.claimController.signal)
    }
  }

  /** 判定作业是否可并行执行：用本设备已上报的 parallel_tools（provider 回退语义与
   *  runner 路由一致：响应 provider 缺省按 boss-recruiting）。 */
  private isParallelInvocation(inv: ClaimedInvocation): boolean {
    const tools = this.opts.parallelTools?.[inv.provider ?? 'boss-recruiting']
    return Array.isArray(tools) && (tools as readonly string[]).includes(inv.tool_name)
  }

  /** 启动 tracked runner（串行 + 并行统一入账）：promise 永不 reject（runner 内部已兜底），
   *  settle 时清理在途记录并按需解除串行占用。 */
  private startRunner(inv: ClaimedInvocation, opts: { parallel: boolean; releaseSerial: boolean }): Promise<void> {
    const runner = (async () => {
      try {
        await runInvocation(inv, {
          ...this.opts.runnerDeps,
          api: this.opts.api,
          shutdownSignal: this.runnerShutdown.signal,
          onEvent: this.opts.onEvent,
          ...(opts.parallel ? { parallel: true } : {}),
        })
      } catch (err) {
        // runner 内部已兜底，此处防御性捕获，保证主循环不死
        logError(`invocation ${inv.invocation_id} 执行器异常: ${err instanceof Error ? err.message : String(err)}`)
      }
    })()
    const tracked = runner.finally(() => {
      this.inFlight.delete(tracked)
      if (opts.releaseSerial) this.serialOccupied = false
      this.opts.onInvocationEnd?.(inv)
    })
    this.inFlight.set(tracked, inv.invocation_id)
    return tracked
  }

  private isInvocationActive(id: string): boolean {
    for (const active of this.inFlight.values()) {
      if (active === id) return true
    }
    return false
  }

  /** 测试观测用：当前是否有 invocation 在执行（串行或并行） */
  get hasRunningInvocation(): boolean {
    return this.inFlight.size > 0
  }
}
