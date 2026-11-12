/**
 * 主循环：heartbeat（5s）+ claim 长轮询（wait=20）+ 执行，断线指数退避 1s→30s。
 *
 * - heartbeat 与 claim 并行两个循环（claim 长轮询阻塞期间心跳不能停）
 * - 网络错误后退避 1s→2s→4s→…→30s，恢复后重置
 * - 设备 token 被撤销（401）→ fail-loud 停循环
 * - shutdown：停止 claim 新任务；当前 invocation 走协作式中止；循环退出后由调用方回收 Provider
 * - 并行接单（契约 2.20）：执行通道与领取参数同受 parallel_claim 协商控制——服务端支持
 *   且本设备有并行工具时才走解耦模式（串行占用期间领取带 parallel_only，并行作业后台
 *   执行不阻塞领取循环；串行 settle 时在途长轮询脱挂，串行队列时效不受影响）；否则完全
 *   沿用原串行循环。drain/shutdown 等待全部在途（串行 + 并行 + 脱挂轮询）调用完成
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
  /** 本设备是否上报了并行工具（声明驱动）：决定领取与执行是否走解耦模式。
   *  无并行工具的设备（BOSS-only 等）即使服务端支持也完全沿用原串行循环（不空转）。 */
  private readonly hasParallelTools: boolean
  /** 串行占用：当前有串行 invocation 领取后未到终态（执行或停止中）。
   *  仅影响领取参数（并行模式）；并行作业完成与否不改变该状态。 */
  private serialOccupied = false
  /** 当前串行 runner（脱挂 race 用）：与 serialOccupied 同置同清 */
  private serialRunner: Promise<void> | null = null
  /** 在途 runner（串行 + 并行）：tracked promise → invocation_id；drain/shutdown 等待全部结束 */
  private readonly inFlight = new Map<Promise<void>, string>()
  /** 脱挂的 parallel_only 长轮询（契约 2.20 修复轮）：串行 settle 时不等待在途轮询——
   *  promise 脱挂后返回的并行作业照常后台执行；drain/shutdown 等待全部结束 */
  private readonly detachedClaims = new Set<Promise<void>>()

  constructor(options: PollLoopOptions) {
    this.opts = {
      heartbeatIntervalMs: 5_000,
      claimWaitSeconds: 20,
      backoffBaseMs: 1_000,
      backoffMaxMs: 30_000,
      ...options,
    }
    this.hasParallelTools = Object.values(options.parallelTools ?? {})
      .some(tools => Array.isArray(tools) && (tools as readonly string[]).length > 0)
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
    // 等待全部在途（串行 + 并行）runner 与脱挂轮询结束——脱挂轮询可能派生新 runner，
    // 结束后再等一轮 inFlight。租约续期/终态回传依赖心跳循环存活
    await Promise.all([...this.inFlight.keys()])
    await Promise.all([...this.detachedClaims])
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
   * 领取循环（契约 2.20 状态机，修复轮后执行通道与领取参数同受协商门禁）：
   * - 解耦模式 = 服务端支持 parallel_claim 且本设备有并行工具。除此之外完全沿用原串行
   *   循环：空参数领取 → 领到即串行执行完 → 下一轮（旧服务端/BOSS-only 设备不空转）。
   * - 解耦模式下：无串行占用 → 空参数领取（FIFO 不变）；串行占用 → 领取带 parallel_only
   *   （服务端只派并行可领作业，串行原地等待）。领到并行作业 → 后台执行，继续领取。
   * - 串行 runner settle 时若在途 parallel_only 长轮询未返回：脱挂（不等待、不中止），
   *   立即继续循环发空参数领取——串行队列时效不受在途长轮询拖累。脱挂轮询最终返回时，
   *   并行作业走统一 handleClaimed 后台执行（不丢作业、不产生卡租约）。
   */
  private async claimLoop(): Promise<void> {
    const { api, claimWaitSeconds, backoffBaseMs, backoffMaxMs } = this.opts
    let backoff = backoffBaseMs
    while (!this.stopped && this.accepting) {
      if (this.opts.canClaim && !this.opts.canClaim()) {
        await sleep(this.opts.heartbeatIntervalMs, this.claimController.signal)
        continue
      }
      const decoupled = this.hasParallelTools && this.serverParallelClaim
      const parallelOnly = decoupled && this.serialOccupied
      let inv: ClaimedInvocation | null = null
      try {
        inv = await this.awaitClaim(api.claim(claimWaitSeconds, parallelOnly, this.claimController.signal), parallelOnly)
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
      const runner = this.handleClaimed(inv)
      if (runner && !decoupled) {
        // 原串行循环：阻塞领取循环至串行完成——继续领取只会立刻领到下一条串行作业。
        // 服务端支持标记随心跳到达可能晚于首次领取：有并行工具的设备在解耦模式激活时
        // 中断等待、恢复解耦领取（新服务端场景并行拾取不被首个串行拖住）；无并行工具
        // 的设备恒阻塞至完成（原形态，无空转领取）
        if (this.hasParallelTools) await Promise.race([runner, this.decoupleSignal()])
        else await runner
      }
    }
  }

  /** 等待解耦模式激活（hasParallelTools && serverParallelClaim）；永不激活时本信号
   *  不结束，race 由串行 runner 一侧胜出 */
  private async decoupleSignal(): Promise<void> {
    while (!this.stopped && this.accepting && !(this.hasParallelTools && this.serverParallelClaim)) {
      await sleep(25, this.claimController.signal)
    }
  }

  /**
   * 领取返回等待：解耦模式下 parallel_only 长轮询在途期间串行 runner 可能先 settle——
   * race 领取返回与串行结束，串行先到则把该轮询脱挂并返回 null（循环立即发空参数领取，
   * 拿到 FIFO 串行队头）。非 parallel_only 领取原样等待。
   */
  private async awaitClaim(pending: Promise<ClaimedInvocation | null>, parallelOnly: boolean): Promise<ClaimedInvocation | null> {
    if (!parallelOnly || !this.serialRunner) return await pending
    const settled = await Promise.race([
      pending.then(
        inv => ({ kind: 'claim' as const, inv }),
        error => ({ kind: 'claim-error' as const, error }),
      ),
      this.serialRunner.then(() => ({ kind: 'serial' as const })),
    ])
    if (settled.kind === 'serial') {
      this.detachClaim(pending)
      return null
    }
    if (settled.kind === 'claim-error') throw settled.error
    return settled.inv
  }

  /**
   * 脱挂在途 parallel_only 长轮询（不 abort，只脱挂）：返回并行作业走统一 handleClaimed
   * 后台执行（parallel_only 过滤下只能是并行作业）；返回 null 丢弃；网络错误只记录
   * （主循环已有新轮询在途）；DeviceRevoked 沿主循环 fail-loud 语义停机。promise 入
   * detachedClaims 账，drain/shutdown 等待其结束。
   */
  private detachClaim(pending: Promise<ClaimedInvocation | null>): void {
    const detached = pending.then(inv => {
      // A successful claim response is already accepted even if stop raced with its return.
      if (inv && !this.stopped && this.accepting) this.handleClaimed(inv)
    }, err => {
      if (err instanceof DeviceRevokedError) {
        this.opts.onConnection?.('revoked')
        logError('设备 token 已失效或被撤销，主循环停止。请重新 pair。')
        this.shutdown()
        return
      }
      if (err instanceof AbortLoopError || this.stopped || !this.accepting) return
      logError(`脱挂领取返回异常: ${err instanceof Error ? err.message : String(err)}`)
    }).finally(() => { this.detachedClaims.delete(detached) })
    this.detachedClaims.add(detached)
  }

  /**
   * 统一领取处理（attached 循环与脱挂轮询共用）：执行通道与领取参数同受协商门禁——
   * 并行 = serverParallelClaim && 声明并行；旧服务端（无标记）下声明并行的作业同样按
   * 串行执行（原行为）。串行作业置占用并返回 runner promise（原串行循环阻塞等待用）。
   */
  private handleClaimed(inv: ClaimedInvocation): Promise<void> | null {
    const parallel = this.serverParallelClaim && this.isParallelInvocation(inv)
    this.emit(`领取 invocation ${inv.invocation_id} tool=${inv.tool_name}${parallel ? '（并行）' : ''}`)
    this.opts.onInvocationStart?.(inv)
    if (parallel) {
      this.startRunner(inv, { parallel: true, releaseSerial: false })
      return null
    }
    this.serialOccupied = true
    const runner = this.startRunner(inv, { parallel: false, releaseSerial: true })
    this.serialRunner = runner
    return runner
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
      if (opts.releaseSerial) {
        this.serialOccupied = false
        this.serialRunner = null
      }
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
