/**
 * 主循环：heartbeat（5s）+ claim 长轮询（wait=20）+ 执行，断线指数退避 1s→30s。
 *
 * - heartbeat 与 claim 并行两个循环（claim 长轮询阻塞期间心跳不能停）
 * - 网络错误后退避 1s→2s→4s→…→30s，恢复后重置
 * - 设备 token 被撤销（401）→ fail-loud 停循环
 * - shutdown：停止 claim 新任务；当前 invocation 走协作式中止；循环退出后由调用方回收 Provider
 * - 并行接单（契约 2.20）：执行通道与领取参数同受 parallel_claim 协商控制——服务端支持
 *   且本设备有并行工具时才走解耦模式：主循环恒发空参数领取（FIFO 不变），串行占用期间
 *   只等待串行 settle；并行作业的等待与重试由专职并行领取通道（lane）承担——任一时刻
 *   至多一个在途 parallel_only 长轮询，跨串行作业复用，失败走 lane 私有退避，不触碰主
 *   循环退避；否则完全沿用原串行循环。drain/shutdown 等待全部在途（串行 + 并行）调用完成
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
  /** 在途 runner（串行 + 并行）：tracked promise → invocation_id；drain/shutdown 等待全部结束 */
  private readonly inFlight = new Map<Promise<void>, string>()
  /** 并行领取通道 promise（契约 2.20 R3）：hasParallelTools 时随 run 启动，与主循环
   *  共用 claimController（stopClaiming/shutdown 一并 abort）；drain 等待其退出 */
  private lanePromise: Promise<void> | null = null

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
    // 并行领取通道随后退出（stopClaiming 已 abort 其在途请求与休眠）；其交付的 runner
    // 已同步入账，最后统一等待全部在途。租约续期/终态回传依赖心跳循环存活
    if (this.lanePromise) await this.lanePromise
    await Promise.all([...this.inFlight.keys()])
    this.stopped = true
    this.controller.abort()
  }

  private claimPromise: Promise<void> | null = null

  async run(): Promise<void> {
    // Begin heartbeats/admission immediately. Old receipts are drained in bounded,
    // single-flight passes after successful heartbeats, using their original claims.
    this.claimPromise = this.claimLoop()
    // 并行领取通道仅在有并行工具的设备启动（BOSS-only 设备连休眠轮询都不需要）
    if (this.hasParallelTools) this.lanePromise = this.parallelLane()
    try {
      const loops: Promise<void>[] = [this.heartbeatLoop(), this.claimPromise]
      if (this.lanePromise) loops.push(this.lanePromise)
      await Promise.all(loops)
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
   * 领取循环（契约 2.20 状态机，R3 复审修复后串行领取与并行领取彻底分离）：
   * - 解耦模式 = 服务端支持 parallel_claim 且本设备有并行工具。除此之外完全沿用原串行
   *   循环：空参数领取 → 领到即串行执行完 → 下一轮（旧服务端/BOSS-only 设备不空转）。
   * - 解耦模式下主循环恒发空参数领取（FIFO 不变）：无串行占用 → 立即领取；串行占用 →
   *   阻塞等待串行 runner settle（不发任何领取请求），settle 后立即发下一条空参数领取。
   *   服务端支持标记随心跳晚到唤醒的是并行领取通道（parallelLane），主循环不改发参数。
   * - 并行作业的等待与重试全部在专职并行领取通道内，不占用串行领取节奏。
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
        inv = await api.claim(claimWaitSeconds, false, this.claimController.signal)
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
      if (runner) {
        // 串行作业：阻塞领取循环至完成——继续领取只会立刻领到下一条串行作业。
        // 并行作业的领取由并行通道在串行占用期间负责（含服务端标记晚到场景）
        await runner
      }
    }
  }

  /**
   * 专职并行领取通道（契约 2.20 R3 复审修复）：独立循环，任一时刻至多一个在途
   * parallel_only 长轮询，跨串行作业复用——取代串行 settle 时的脱挂机制（每次 settle
   * 遗留一条在途轮询、失败进共用退避拖慢串行队列）。
   * - 激活条件 accepting && 解耦模式 && 串行占用；休眠等待按 25ms 轮询条件（与原
   *   decoupleSignal 同风格），休眠期间不发任何请求（主循环空参数领取覆盖 FIFO 全部作业）
   * - 领到并行作业后台执行（handleClaimed 统一入账）后立即继续领取；串行 settle 后
   *   在途轮询自然返回（交付或丢弃）再转入休眠
   * - 错误走 lane 私有退避（独立计时、封顶与主循环相同），不触碰主循环 backoff/状态；
   *   DeviceRevoked 沿 fail-loud 语义停整个循环；stopClaiming/shutdown abort 共用的
   *   claimController 使 lane 尽快结算退出
   */
  private async parallelLane(): Promise<void> {
    const { api, claimWaitSeconds, backoffBaseMs, backoffMaxMs } = this.opts
    let backoff = backoffBaseMs
    while (!this.stopped && this.accepting) {
      if (!(this.hasParallelTools && this.serverParallelClaim && this.serialOccupied)) {
        await sleep(25, this.claimController.signal)
        continue
      }
      if (this.opts.canClaim && !this.opts.canClaim()) {
        await sleep(this.opts.heartbeatIntervalMs, this.claimController.signal)
        continue
      }
      let inv: ClaimedInvocation | null = null
      try {
        inv = await api.claim(claimWaitSeconds, true, this.claimController.signal)
        backoff = backoffBaseMs
      } catch (err) {
        if (err instanceof AbortLoopError || this.stopped || !this.accepting) return
        if (err instanceof DeviceRevokedError) {
          this.opts.onConnection?.('revoked')
          logError('设备 token 已失效或被撤销，主循环停止。请重新 pair。')
          this.shutdown()
          return
        }
        if (err instanceof ApiError || err instanceof NetworkError) {
          // lane 私有退避：只影响并行领取节奏，串行领取（主循环）不受影响
          await sleep(backoff, this.claimController.signal)
          backoff = Math.min(backoff * 2, backoffMaxMs)
          continue
        }
        throw err
      }
      // 长轮询返回：即便串行已 settle（转入休眠前）也照常交付——成功领取即已接受
      if (inv && !this.stopped && this.accepting) this.handleClaimed(inv)
    }
  }

  /**
   * 统一领取处理（主循环与并行通道共用）：执行通道与领取参数同受协商门禁——
   * 并行 = serverParallelClaim && 声明并行；旧服务端（无标记）下声明并行的作业同样按
   * 串行执行（原行为）。串行作业置占用并返回 runner promise（主循环阻塞等待用）。
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
    return this.startRunner(inv, { parallel: false, releaseSerial: true })
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
