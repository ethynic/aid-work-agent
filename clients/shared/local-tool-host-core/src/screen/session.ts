/**
 * 画面会话控制器：把信令、同机判定、画面租约、锁屏检测和任务优先串起来（设计 4.3～4.5、6.1）。
 *
 * 画面会话是 Host 里一段独立的异步流程。它在心跳成败判定之外被调用：任何一步出错都不能向调用方抛出，
 * 否则会被当成心跳失败。所以对外方法全部自己消化异常，外部世界只通过注入的端口对象交互，
 * 定时器一律 unref（不阻止 Host 进程退出），日志只经 ports.log 输出。
 *
 * 几条固定的不变量：
 *   - 结束会话时先关窗口，再尽力发 end；服务器或网页发起的结束不再发 end；
 *   - 采集只在采集页拿到「确认非同机」之后开始，Runtime 这边拿不到证据就是无法确认；
 *   - 日志里只有会话编号、状态名、判定结论和规则编号、结束原因、计数和耗时，
 *     不出现地址、mDNS 名字、候选、SDP、信令内容和 STUN 地址。
 */
import {
  CANDIDATE_HOLD_MS, CONNECT_TIMEOUT_MS, EVIDENCE_WAIT_MS, LEASE_TIMEOUT_MS, LOCK_CHECK_MS, PEER_PORT_WAIT_MS, POLL_INTERVAL_MS, POLL_JITTER_MS,
  RESOURCE_SAMPLE_MS, RENEWAL_LATE_SECONDS, VERIFY_TIMEOUT_MS,
  isRecord, isRuntimeEndReason, parseHeartbeatScreenSession,
  type PeerPort, type PortProtocol, type RuntimeEndReason, type RuntimeSignalType,
} from './protocol.js'
import { candidateHost, classifyCandidate, normalizeAddress, type CandidateKind } from './candidate.js'
import { judgeByAddresses, judgeByPeerPorts, type ScreenVerdict, type VerdictEvidence, type VerdictRule } from './machineVerdict.js'
import { ResourcePolicy } from './resourcePolicy.js'
import type { HostLookupResult } from './hostNetwork.js'
import type { ScreenSessionRead, ScreenSignal, SignalPostResult } from './signaling.js'
import type { FromPageMessage, ToPageMessage } from './messages.js'

export interface ScreenShellPort {
  /** 让壳创建隐藏采集窗口并开始建连 */
  open(session: { sessionId: string; stunUrls: string[] }): void
  /** 转交给采集页 */
  send(sessionId: string, message: ToPageMessage): void
  /** 先关窗口 */
  close(sessionId: string): void
}

export interface ScreenSignalingPort {
  readSession(sessionId: string, afterSeq: number): Promise<ScreenSessionRead | null>
  postSignal(sessionId: string, type: string, payload: unknown): Promise<SignalPostResult>
  abandon(): void
}

export interface ScreenNetworkPort {
  /** 本机全部网卡地址（已归一化） */
  localAddresses(): string[]
  resolveHost(name: string): Promise<HostLookupResult>
}

export interface ScreenSessionPorts {
  signaling: ScreenSignalingPort
  shell: ScreenShellPort
  network: ScreenNetworkPort
  /** 对端端口是不是本机的套接字。返回 null 表示端口表查不到，不等于「不是本机的端口」 */
  isLocalPort(port: number, protocol: PortProtocol): Promise<boolean | null>
  /** 现有的锁屏检测（checkDesktopInteractive 的 interactive），注入 */
  desktopInteractive(): Promise<boolean>
  cpuBusyPercent(): number | null
  taskRunning(): boolean
  /** 会话响应里没带 STUN 列表时使用的缺省值（只含 stun:/stuns:） */
  stunUrls: string[]
  log(event: string, fields: Record<string, string | number | boolean | null>): void
}

/** 给管理界面的状态摘要。形状固定，只含这几个字段 */
export interface ScreenSummary {
  sessionId: string
  /** prepared、connecting、verifying 都算 connecting */
  state: 'connecting' | 'streaming' | 'locked' | 'renewing'
  /** 是否已降低画质 */
  degraded: boolean
  /** 开始出画面的时间（epoch 毫秒），没有就是 null */
  startedAt: number | null
  /** 多少秒没有收到服务器确认；传画面中才有 */
  secondsSinceRenewal: number | null
}

type Phase = 'prepared' | 'connecting' | 'verifying' | 'streaming'
type Timer = ReturnType<typeof setTimeout>
type LogFields = Record<string, string | number | boolean | null>

/** 两端公网出口不同时不发给服务器的候选类别：不让本机所在的网络里的地址离开这个网络（设计 4.1） */
const HIDDEN_WHEN_EGRESS_DIFFERS: ReadonlySet<CandidateKind> = new Set(['private', 'shared', 'mdns'])
/** 等证据到齐时每隔多久看一次 */
const EVIDENCE_POLL_MS = 100

interface RemoteEvidence {
  type: string
  address: string | null
}

interface Session {
  readonly id: string
  readonly createdAt: number
  phase: Phase
  /** 轮询游标：已经处理过的最大信令编号 */
  seq: number
  stunUrls: string[]
  /** 这个会话的全部定时器，结束时一起清掉 */
  readonly timers: Set<Timer>
  polling: boolean
  pollInFlight: boolean
  // 同机判定的证据
  /** 网页的地址候选，按到达顺序 */
  readonly remotes: RemoteEvidence[]
  /** 本机经 STUN 得到的公网映射地址 */
  readonly mapped: string[]
  mappedReported: boolean
  /** 还没回来的名字解析 */
  pendingResolves: number
  resolveSeq: number
  peerPorts: PeerPort[] | null
  peerPortsWaiter: ((ports: PeerPort[] | null) => void) | null
  /** 暂存的内网类候选：配了 STUN 时等能比较两端公网出口再决定发不发；null 表示不在暂存 */
  heldCandidates: Array<{ candidate: string; sdp_mid: string | null; sdp_mline_index: number | null }> | null
  // 任务优先
  readonly policy: ResourcePolicy
  encoderCpuLimited: boolean
  degraded: boolean
  // 租约与锁屏
  /** 上一次锁屏检测的结果。开始时按「没有锁屏」算，所以只在变成锁屏时才通知网页 */
  interactive: boolean
  lockCheckInFlight: boolean
  startedAt: number | null
  lastRenewAt: number
}

export class ScreenSessionController {
  private active: Session | null = null
  private disposed = false

  constructor(private readonly ports: ScreenSessionPorts) {}

  /** 每次心跳成功后调用，传入原始心跳响应对象 */
  onHeartbeatScreen(response: unknown): void {
    this.guard('onHeartbeatScreen', () => {
      const notice = parseHeartbeatScreenSession(response)
      if (!notice || this.disposed) return
      const current = this.active
      if (notice.state === 'pending') {
        if (current?.id === notice.id) return
        // 服务器保证同一设备只有一个未结束的会话：编号不同说明当前会话其实已经结束，只是通知没送到。
        // 先关掉它的窗口（不发 end），再开始新会话
        if (current) this.finish(current, null)
        this.begin(notice.id)
        return
      }
      // 编号不认识的通知直接忽略（例如超时收敛恰好发生在某次心跳查询里的会话）
      if (!current || current.id !== notice.id) return
      if (notice.state === 'ended') this.finish(current, null)
      else if (current.phase === 'streaming') this.touchLease(current)
    })
  }

  /** 采集窗口经壳发来的消息 */
  onShellMessage(sessionId: string, message: FromPageMessage): void {
    this.guard('onShellMessage', () => {
      const s = this.active
      if (this.disposed || !s || s.id !== sessionId || !isRecord(message)) return
      switch (message.kind) {
        case 'offer':
          if (typeof message.sdp === 'string') this.send(s, 'offer', { sdp: message.sdp })
          return
        case 'candidate':
          this.onLocalCandidate(s, message)
          return
        case 'mapped': {
          if (typeof message.address !== 'string' || message.address === '') return
          const address = normalizeAddress(message.address)
          if (address && !s.mapped.includes(address)) s.mapped.push(address)
          s.mappedReported = true
          return
        }
        case 'remote-candidate':
          if (typeof message.candidate === 'string') this.onRemoteCandidate(s, message.candidate)
          return
        case 'connected':
          this.onConnected(s)
          return
        case 'peer-ports':
          this.onPeerPorts(s, message.ports)
          return
        case 'streaming':
          this.onStreaming(s, message.path)
          return
        case 'closed':
          this.finish(s, isRuntimeEndReason(message.reason) ? message.reason : s.phase === 'streaming' ? 'runtime_stopped' : 'connect_failed')
          return
        case 'cpu-limited':
          s.encoderCpuLimited = message.limited === true
          return
        default:
          return
      }
    })
  }

  /** 本机用户在出画面前拒绝；已经出画面后按停止处理 */
  reject(sessionId: string): void {
    this.guard('reject', () => this.endByUser(sessionId, s => (s.phase === 'streaming' ? 'runtime_stopped' : 'runtime_rejected')))
  }

  /** 本机用户停止查看。可重复调用；对不存在或不匹配的会话编号没有效果 */
  stop(sessionId: string): void {
    this.guard('stop', () => this.endByUser(sessionId, () => 'runtime_stopped'))
  }

  /** 壳通知：采集窗口的渲染进程异常退出 */
  windowCrashed(sessionId: string): void {
    this.guard('windowCrashed', () => this.endByUser(sessionId, s => (s.phase === 'streaming' ? 'runtime_stopped' : 'connect_failed')))
  }

  /** 给管理界面的状态摘要，没有会话时为 null */
  summary(): ScreenSummary | null {
    const s = this.active
    if (!s) return null
    const streaming = s.phase === 'streaming'
    const secondsSinceRenewal = streaming ? Math.max(0, Math.floor((Date.now() - s.lastRenewAt) / 1000)) : null
    let state: ScreenSummary['state'] = 'connecting'
    if (streaming) {
      // 收不到服务器确认的提示更急：到点会自动停止，所以它优先于锁屏提示
      if (secondsSinceRenewal !== null && secondsSinceRenewal > RENEWAL_LATE_SECONDS) state = 'renewing'
      else state = s.interactive ? 'streaming' : 'locked'
    }
    return { sessionId: s.id, state, degraded: s.degraded, startedAt: s.startedAt, secondsSinceRenewal }
  }

  /** Host 停止执行或退出时调用：结束一切，不等网络请求 */
  dispose(): void {
    this.guard('dispose', () => {
      this.disposed = true
      if (this.active) this.finish(this.active, 'runtime_stopped')
    })
  }

  // ---- 会话的开始与结束 ----

  private begin(id: string): void {
    const s: Session = {
      id, createdAt: Date.now(), phase: 'prepared', seq: 0, stunUrls: [...this.ports.stunUrls], timers: new Set(),
      polling: false, pollInFlight: false,
      remotes: [], mapped: [], mappedReported: false, pendingResolves: 0, resolveSeq: 0, peerPorts: null, peerPortsWaiter: null, heldCandidates: null,
      policy: new ResourcePolicy(), encoderCpuLimited: false, degraded: false,
      interactive: true, lockCheckInFlight: false, startedAt: null, lastRenewAt: 0,
    }
    this.active = s
    this.log('screen_session_started', { session: id, state: s.phase })
    this.send(s, 'prepared', {})
    s.polling = true
    this.run(s, 'poll', () => this.poll(s))
  }

  /**
   * 结束会话。reason 为 null 表示服务器或网页发起的结束，不再发 end。
   * 顺序：先摘下会话（之后到达的定时器和异步结果都按「不是当前会话」处理）→ 先关窗口 →
   * 清掉全部定时器并停止轮询 → 再尽力发 end。
   */
  private finish(s: Session, reason: RuntimeEndReason | null): void {
    if (this.active !== s) return
    this.active = null
    this.closeShell(s)
    for (const timer of s.timers) clearTimeout(timer)
    s.timers.clear()
    s.polling = false
    s.peerPortsWaiter = null
    this.log('screen_session_ended', { session: s.id, reason, state: s.phase, durationMs: Date.now() - s.createdAt })
    // 这个会话还在排队的信令作废；已经发出的那次请求由它自己结束
    this.run(s, 'abandon', () => this.ports.signaling.abandon())
    if (reason) this.send(s, 'end', { reason })
  }

  private endByUser(sessionId: string, reason: (s: Session) => RuntimeEndReason): void {
    const s = this.active
    if (this.disposed || !s || s.id !== sessionId) return
    this.finish(s, reason(s))
  }

  // ---- 轮询（prepared、connecting、verifying 三个阶段）----

  private async poll(s: Session): Promise<void> {
    if (!s.polling || s.pollInFlight) return
    s.pollInFlight = true
    let read: ScreenSessionRead | null = null
    try {
      read = await this.ports.signaling.readSession(s.id, s.seq)
    } catch {
      // 这一次读取作废，下一次轮询再取
    }
    if (this.active !== s) return
    s.pollInFlight = false
    if (read) this.handleRead(s, read)
    // 从收到这次响应时起算，加少量抖动
    if (this.active === s && s.polling) this.later(s, POLL_INTERVAL_MS + Math.floor(Math.random() * POLL_JITTER_MS), () => this.poll(s))
  }

  private handleRead(s: Session, read: ScreenSessionRead): void {
    if (read.session.state === 'ended') {
      this.finish(s, null)
      return
    }
    if (read.session.stunUrls) s.stunUrls = read.session.stunUrls
    for (const signal of read.signals) {
      if (this.active !== s) return
      // 游标先前进：处理某条信令时出错，不让它后面的信令跟着卡住
      s.seq = Math.max(s.seq, signal.seq)
      this.run(s, 'web-signal', () => this.handleWebSignal(s, signal))
    }
  }

  private handleWebSignal(s: Session, signal: ScreenSignal): void {
    const payload = isRecord(signal.payload) ? signal.payload : null
    switch (signal.type) {
      case 'start':
        if (s.phase === 'prepared' && payload) this.beginConnect(s)
        return
      case 'answer': {
        const sdp = payload?.sdp
        if (typeof sdp === 'string') this.toPage(s, { kind: 'answer', sdp })
        return
      }
      case 'candidate': {
        const candidate = payload?.candidate
        if (typeof candidate === 'string') {
          this.toPage(s, { kind: 'candidate', candidate, sdp_mid: textOrNull(payload?.sdp_mid), sdp_mline_index: numberOrNull(payload?.sdp_mline_index) })
        }
        return
      }
      case 'end':
        this.finish(s, null)
        return
      default:
        // 类型不认识：忽略这一次，不算错误
    }
  }

  private beginConnect(s: Session): void {
    s.phase = 'connecting'
    this.log('screen_phase', { session: s.id, state: s.phase })
    try {
      this.ports.shell.open({ sessionId: s.id, stunUrls: s.stunUrls })
    } catch (error) {
      // 窗口没建成就不可能连上：按建连失败结束，不等 30 秒
      this.report(s, 'shell.open', error)
      this.finish(s, 'connect_failed')
      return
    }
    this.later(s, CONNECT_TIMEOUT_MS, () => {
      if (s.phase === 'connecting') this.finish(s, 'connect_failed')
    })
  }

  // ---- 采集页的消息 ----

  private onLocalCandidate(s: Session, message: Extract<FromPageMessage, { kind: 'candidate' }>): void {
    if (typeof message.candidate !== 'string' || message.candidate === '') return
    const entry = { candidate: message.candidate, sdp_mid: message.sdp_mid ?? null, sdp_mline_index: message.sdp_mline_index ?? null }
    const kind = classifyCandidate(message.candidate).kind
    if (!HIDDEN_WHEN_EGRESS_DIFFERS.has(kind)) return this.sendCandidate(s, entry)
    // 内网类候选：Runtime 的地址几乎总是先于网页的映射地址发出，发送时判断会漏（设计 4.1）。
    // 配了 STUN 就先暂存，等比出两端出口再决定；没配 STUN 时永远比不出来，直接发
    if (s.stunUrls.length === 0) return this.sendCandidate(s, entry)
    if (!s.heldCandidates) {
      s.heldCandidates = []
      this.later(s, CANDIDATE_HOLD_MS, () => this.flushHeld(s))
    }
    s.heldCandidates.push(entry)
  }

  /** 出口比出来了且相同（网页的映射地址里出现了本机的映射地址）：暂存的候选不用再等，马上决定 */
  private maybeFlushHeld(s: Session): void {
    if (!s.heldCandidates || s.mapped.length === 0) return
    const viewerMapped = s.remotes.flatMap(remote => (remote.type === 'srflx' && remote.address ? [remote.address] : []))
    if (viewerMapped.some(address => s.mapped.includes(address))) this.flushHeld(s)
  }

  /** 把暂存的内网候选按当前掌握的出口信息发或弃；出口还没比出来就照发，不挡建连 */
  private flushHeld(s: Session): void {
    const held = s.heldCandidates
    if (!held) return
    s.heldCandidates = null
    for (const entry of held) {
      const kind = classifyCandidate(entry.candidate).kind
      if (HIDDEN_WHEN_EGRESS_DIFFERS.has(kind) && this.egressDiffers(s)) {
        this.log('screen_candidate_filtered', { session: s.id, kind })
        continue
      }
      this.sendCandidate(s, entry)
    }
  }

  private sendCandidate(s: Session, entry: { candidate: string; sdp_mid: string | null; sdp_mline_index: number | null }): void {
    // 出口已经确定不同时（暂存清空之后才来的内网候选），内网类地址不下发
    if (HIDDEN_WHEN_EGRESS_DIFFERS.has(classifyCandidate(entry.candidate).kind) && this.egressDiffers(s)) {
      this.log('screen_candidate_filtered', { session: s.id, kind: classifyCandidate(entry.candidate).kind })
      return
    }
    this.send(s, 'candidate', entry)
  }

  /** 两端公网出口是否不同：本机的映射地址和网页的映射地址（type 为 srflx 的明文地址）都已知，
   *  且网页的映射地址里没有一个是本机的。任一端未知时返回 false（信息不全，照常下发） */
  private egressDiffers(s: Session): boolean {
    if (s.mapped.length === 0) return false
    const viewerMapped = s.remotes.flatMap(remote => (remote.type === 'srflx' && remote.address ? [remote.address] : []))
    return viewerMapped.length > 0 && !viewerMapped.some(address => s.mapped.includes(address))
  }

  private onRemoteCandidate(s: Session, candidate: string): void {
    const classified = classifyCandidate(candidate)
    const host = candidateHost(candidate)
    const entry: RemoteEvidence = { type: classified.type, address: null }
    s.remotes.push(entry)
    // 明文地址直接用；mDNS 名字交给系统解析器
    if (classified.kind !== 'mdns') {
      entry.address = normalizeAddress(host) || null
      // 网页的映射地址到了：如果它和本机的一致，暂存的内网候选不用再等
      this.maybeFlushHeld(s)
      return
    }
    s.pendingResolves++
    const id = ++s.resolveSeq
    this.run(s, 'resolveHost', async () => {
      let result: HostLookupResult
      try {
        result = await this.ports.network.resolveHost(host)
      } catch {
        result = { ok: false, code: 'ERROR' }
      }
      if (this.active !== s) return
      s.pendingResolves--
      if (result.ok) entry.address = normalizeAddress(result.address) || null
      // 解析出的地址只回给采集页，不写日志
      this.toPage(s, { kind: 'resolved', id, address: result.ok ? result.address : null })
    })
  }

  private onConnected(s: Session): void {
    // 连接状态反复时采集页会再报一次 connected，只在第一次时处理
    if (s.phase !== 'connecting') return
    s.phase = 'verifying'
    this.log('screen_phase', { session: s.id, state: s.phase })
    this.send(s, 'status', { state: 'connected' })
    this.later(s, VERIFY_TIMEOUT_MS, () => {
      if (s.phase === 'verifying') this.finish(s, 'machine_check_unknown')
    })
    this.run(s, 'judge', () => this.judgeMachine(s))
  }

  private onPeerPorts(s: Session, ports: unknown): void {
    if (!Array.isArray(ports)) return
    const usable: PeerPort[] = []
    for (const entry of ports) {
      if (!isRecord(entry) || typeof entry.port !== 'number') continue
      usable.push({ port: entry.port, protocol: entry.protocol === 'tcp' ? 'tcp' : 'udp' })
    }
    // 空列表说明还没有成功的候选对，不算报来
    if (usable.length === 0) return
    s.peerPorts = usable
    const waiter = s.peerPortsWaiter
    s.peerPortsWaiter = null
    if (waiter) waiter(usable)
  }

  private onStreaming(s: Session, path: unknown): void {
    if (s.phase === 'streaming') return
    const route = path === 'lan' || path === 'p2p' ? path : null
    // 出画面后不再轮询，靠心跳续期和获知结束；验证时限也不再适用
    s.polling = false
    s.phase = 'streaming'
    s.startedAt = Date.now()
    this.log('screen_phase', { session: s.id, state: s.phase })
    this.send(s, 'status', route ? { state: 'streaming', path: route } : { state: 'streaming' })
    this.touchLease(s)
    this.every(s, LOCK_CHECK_MS, () => this.checkLock(s))
    this.every(s, RESOURCE_SAMPLE_MS, () => this.sampleResources(s))
  }

  // ---- 同机判定 ----

  private async judgeMachine(s: Session): Promise<void> {
    // 先等还没回来的名字解析和本机的公网映射地址到齐，最多约 1.5 秒。没有配置 STUN 时不等映射地址
    const expectMapped = s.stunUrls.length > 0
    for (let waited = 0; waited < EVIDENCE_WAIT_MS; waited += EVIDENCE_POLL_MS) {
      if (s.pendingResolves === 0 && (!expectMapped || s.mappedReported)) break
      await this.sleep(s, EVIDENCE_POLL_MS)
      if (this.active !== s) return
    }
    const byAddresses = judgeByAddresses({ locals: this.ports.network.localAddresses(), mapped: s.mapped, remotes: s.remotes })
    if (byAddresses.verdict !== 'unknown') {
      this.deliverVerdict(s, byAddresses.verdict, byAddresses.rule, byAddresses.evidence)
      return
    }
    // 地址候选分不出来：等采集页报来对端端口（最多约 1.5 秒），查本机端口表，按规则 5、6 下结论
    const ports = await this.waitForPeerPorts(s, PEER_PORT_WAIT_MS)
    if (this.active !== s) return
    if (!ports) {
      this.deliverVerdict(s, 'unknown', 4, byAddresses.evidence)
      return
    }
    const byPorts = await judgeByPeerPorts(ports, (port, protocol) => this.lookupLocalPort(s, port, protocol))
    if (this.active !== s) return
    this.deliverVerdict(s, byPorts.verdict, byPorts.rule, byAddresses.evidence)
  }

  private async lookupLocalPort(s: Session, port: number, protocol: PortProtocol): Promise<boolean | null> {
    try {
      const found = await this.ports.isLocalPort(port, protocol)
      // 只把明确的 true / false 当证据：null（端口表查不到）原样传下去，不能变成「不属于本机」
      return found === true || found === false ? found : null
    } catch (error) {
      // 端口表查不到不是证据，按 null 处理（不放行）
      this.report(s, 'isLocalPort', error)
      return null
    }
  }

  private waitForPeerPorts(s: Session, ms: number): Promise<PeerPort[] | null> {
    if (s.peerPorts) return Promise.resolve(s.peerPorts)
    return new Promise(resolve => {
      s.peerPortsWaiter = resolve
      this.later(s, ms, () => {
        if (s.peerPortsWaiter !== resolve) return
        s.peerPortsWaiter = null
        resolve(null)
      })
    })
  }

  private deliverVerdict(s: Session, verdict: ScreenVerdict, rule: VerdictRule, evidence: VerdictEvidence): void {
    if (this.active !== s) return
    // 结论和规则编号写进日志；evidence 只有计数和是否不同，不含地址
    this.log('screen_verdict', { session: s.id, verdict, rule, ...evidence })
    this.toPage(s, { kind: 'verdict', verdict, rule })
  }

  // ---- 画面租约、锁屏检测、任务优先（都在 streaming 阶段）----

  private touchLease(s: Session): void {
    s.lastRenewAt = Date.now()
    // 每次续期都重新计时。之前排下的计时器到点时发现已经续过期，什么都不做
    this.later(s, LEASE_TIMEOUT_MS, () => {
      if (s.phase === 'streaming' && Date.now() - s.lastRenewAt >= LEASE_TIMEOUT_MS) this.finish(s, 'renewal_timeout')
    })
  }

  private async checkLock(s: Session): Promise<void> {
    // 上一次检测还没返回时跳过这一次，不叠加
    if (s.lockCheckInFlight) return
    s.lockCheckInFlight = true
    let interactive: boolean | null = null
    try {
      interactive = (await this.ports.desktopInteractive()) === true
    } catch (error) {
      // 查询出错按没有变化处理
      this.report(s, 'desktopInteractive', error)
    }
    s.lockCheckInFlight = false
    if (this.active !== s || s.phase !== 'streaming' || interactive === null || interactive === s.interactive) return
    s.interactive = interactive
    this.send(s, 'status', { state: interactive ? 'unlocked' : 'locked' })
  }

  private sampleResources(s: Session): void {
    let action: ReturnType<ResourcePolicy['sample']>
    try {
      action = s.policy.sample({
        taskRunning: this.ports.taskRunning(),
        machineCpuBusy: this.ports.cpuBusyPercent(),
        encoderCpuLimited: s.encoderCpuLimited,
      })
    } catch (error) {
      // 读不到任务状态或 CPU 占用：跳过这一次采样，观看继续
      this.report(s, 'resource-sample', error)
      return
    }
    if (action === 'degrade') {
      s.degraded = true
      this.toPage(s, { kind: 'degrade' })
      this.send(s, 'status', { state: 'degraded' })
    } else if (action === 'restore') {
      s.degraded = false
      this.toPage(s, { kind: 'restore' })
      this.send(s, 'status', { state: 'restored' })
    } else if (action === 'stop') {
      this.finish(s, 'resource_busy')
    }
  }

  // ---- 与端口对象打交道的最小封装：任何一处出错都只记日志 ----

  private send(s: Session, type: RuntimeSignalType, payload: unknown): void {
    let posting: Promise<SignalPostResult>
    try {
      posting = this.ports.signaling.postSignal(s.id, type, payload)
    } catch (error) {
      this.report(s, `post:${type}`, error)
      return
    }
    posting.then(
      result => {
        // 发信令时发现服务器已经结束这个会话：同样结束本地会话
        if (this.active === s && !result.ok && (result.kind === 'ended' || result.kind === 'not_found')) this.finish(s, null)
      },
      error => this.report(s, `post:${type}`, error),
    )
  }

  private toPage(s: Session, message: ToPageMessage): void {
    try {
      this.ports.shell.send(s.id, message)
    } catch (error) {
      this.report(s, `send:${message.kind}`, error)
    }
  }

  private closeShell(s: Session): void {
    this.run(s, 'shell.close', () => this.ports.shell.close(s.id))
  }

  /** 排一个属于这个会话的定时器；会话结束时被清掉，回调只在会话仍是当前会话时执行 */
  private later(s: Session, ms: number, work: () => void): void {
    const timer = setTimeout(() => {
      s.timers.delete(timer)
      if (this.active === s) this.run(s, 'timer', work)
    }, ms)
    timer.unref()
    s.timers.add(timer)
  }

  private every(s: Session, ms: number, work: () => void): void {
    const timer = setInterval(() => {
      if (this.active === s) this.run(s, 'timer', work)
    }, ms)
    timer.unref()
    s.timers.add(timer)
  }

  private sleep(s: Session, ms: number): Promise<void> {
    return new Promise(resolve => this.later(s, ms, resolve))
  }

  /** 执行一段可能是异步的流程，同步异常和 Promise 的拒绝都消化掉 */
  private run(s: Session, where: string, work: () => void | Promise<void>): void {
    try {
      const result = work()
      if (result) void result.then(undefined, error => this.report(s, where, error))
    } catch (error) {
      this.report(s, where, error)
    }
  }

  private guard(where: string, work: () => void): void {
    try {
      work()
    } catch (error) {
      this.report(this.active, where, error)
    }
  }

  private log(event: string, fields: LogFields): void {
    try {
      this.ports.log(event, fields)
    } catch {
      // 日志失败不影响画面会话
    }
  }

  /** 记录一处异常。错误信息里可能带地址或名字（例如解析器的报错），所以只记错误的类型名 */
  private report(s: Session | null, where: string, error: unknown): void {
    this.log('screen_error', { session: s?.id ?? null, where, error: error instanceof Error ? error.name : 'Error' })
  }
}

function textOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' ? value : null
}
