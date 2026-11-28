/**
 * 采集页逻辑（设计 6.1）：先建立不含画面的连接，等 Host 的同机判定，确认非本机后才采集主屏幕。
 * 参照 spikes/runtime-screen-stream-poc/sender.js 的验证实现。页面不访问网络；
 * 结束时只关连接与轨道，画面不显示、不落盘。
 */

/** 等待成功的候选对最多约 1.5 秒（100ms × 15），与验证程序 sender.js 的 peerPorts 一致 */
const PEER_PORT_POLL_MS = 100
const PEER_PORT_POLLS = 15
/** 传画面后每 5 秒一次编码器与线路统计（设计 4.5） */
const STATS_INTERVAL_MS = 5_000
/** 降低画质改采集轨道的约束（设计 4.5，实测有效）；1280×720 缩小 1.5 倍是 854×480 */
const DEGRADED_CONSTRAINTS: MediaTrackConstraints = { frameRate: { max: 5 }, width: { max: 854 }, height: { max: 480 } }
/** 画面目标：1280×720、15 帧（设计 1） */
const CAPTURE_CONSTRAINTS: MediaTrackConstraints = { width: { max: 1280 }, height: { max: 720 }, frameRate: { max: 15, ideal: 15 } }
/** 发送码率上限与画质策略（设计 6.1）：保持分辨率，CPU 跟不上时先降帧 */
const MAX_BITRATE = 2_500_000

type AddressKind = 'mdns' | 'loopback' | 'private' | 'shared' | 'public' | 'other'

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function textOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' ? value : null
}

export function normalizeAddress(value: unknown): string {
  return String(value ?? '').replace(/^::ffff:/i, '').replace(/%.*$/, '').toLowerCase()
}

/** POC stats.js classify 的地址分类（内网类含共享地址段与 mDNS 名字） */
export function classifyAddressKind(address: string | null | undefined): AddressKind {
  const normalized = normalizeAddress(address)
  if (!normalized) return 'other'
  if (/\.local$/i.test(normalized)) return 'mdns'
  if (/^(127\.|::1$)/i.test(normalized)) return 'loopback'
  if (/^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.)/.test(normalized) || /^f[cd]/i.test(normalized)) return 'private'
  if (/^(198\.1[89]\.|198\.19\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(normalized)) return 'shared'
  return 'public'
}

const PRIVATE_KINDS: ReadonlySet<AddressKind> = new Set(['private', 'shared', 'mdns'])

/** 实际线路（设计 4.1）：两端选中的都是内网地址为局域网直连，其余为点对点直连 */
export function derivePath(local: string | null | undefined, remote: string | null | undefined): 'lan' | 'p2p' {
  return PRIVATE_KINDS.has(classifyAddressKind(local)) && PRIVATE_KINDS.has(classifyAddressKind(remote)) ? 'lan' : 'p2p'
}

/** 把 H.264 排到编码首选（设计 6.1）：否则 Chromium 默认选 VP8 软编码 */
export function orderH264Codecs<T extends { mimeType: string }>(codecs: readonly T[]): T[] {
  const isH264 = (codec: { mimeType: string }) => codec.mimeType.toLowerCase() === 'video/h264'
  return [...codecs.filter(isH264), ...codecs.filter((codec) => !isH264(codec))]
}

function statAddress(candidate: Record<string, unknown> | null): string | null {
  if (!candidate) return null
  if (typeof candidate['address'] === 'string' && candidate['address']) return candidate['address']
  return typeof candidate['ip'] === 'string' && candidate['ip'] ? candidate['ip'] : null
}

export interface PeerPort { port: number; protocol: 'udp' | 'tcp' }

/** 连接建立后，每个成功的候选对里对端候选的端口（统计不给对端地址，只给端口；设计 4.2 规则 5、6） */
export function peerPortsFromStats(report: RTCStatsReport): PeerPort[] {
  const seen = new Map<string, PeerPort>()
  report.forEach((raw: unknown) => {
    const pair = asRecord(raw)
    if (!pair || pair['type'] !== 'candidate-pair' || pair['state'] !== 'succeeded') return
    const remoteId = typeof pair['remoteCandidateId'] === 'string' ? pair['remoteCandidateId'] : ''
    const remote = remoteId ? asRecord(report.get(remoteId)) : null
    const port = remote && typeof remote['port'] === 'number' && Number.isSafeInteger(remote['port']) ? remote['port'] : 0
    if (!port) return
    const protocol = remote!['protocol'] === 'tcp' ? 'tcp' : 'udp'
    seen.set(`${protocol}:${port}`, { port, protocol })
  })
  return [...seen.values()]
}

/** 提名成功候选对两端的地址分类，得出实际线路；取不到时为 null */
export function pathFromStats(report: RTCStatsReport): 'lan' | 'p2p' | null {
  let path: 'lan' | 'p2p' | null = null
  report.forEach((raw: unknown) => {
    const pair = asRecord(raw)
    if (!pair || pair['type'] !== 'candidate-pair' || pair['state'] !== 'succeeded' || pair['nominated'] !== true) return
    const localId = typeof pair['localCandidateId'] === 'string' ? pair['localCandidateId'] : ''
    const remoteId = typeof pair['remoteCandidateId'] === 'string' ? pair['remoteCandidateId'] : ''
    path = derivePath(statAddress(localId ? asRecord(report.get(localId)) : null), statAddress(remoteId ? asRecord(report.get(remoteId)) : null))
  })
  return path
}

/** 编码器是否因 CPU 受限（设计 4.5 的信号之一）；没有发送统计时为 null */
export function encoderCpuLimited(report: RTCStatsReport): boolean | null {
  let limited: boolean | null = null
  report.forEach((raw: unknown) => {
    const record = asRecord(raw)
    if (!record || record['type'] !== 'outbound-rtp' || record['kind'] !== 'video') return
    limited = record['qualityLimitationReason'] === 'cpu'
  })
  return limited
}

export class ScreenCaptureClient {
  private pc: RTCPeerConnection | null = null
  private transceiver: RTCRtpTransceiver | null = null
  private track: MediaStreamTrack | null = null
  private finished = false
  private connectedEmitted = false
  private remoteDescriptionSet = false
  private captureStarted = false
  private cpuLimitedReported = false
  private savedConstraints: MediaTrackConstraints = {}
  private peerPortsTimer: ReturnType<typeof setInterval> | null = null
  private statsTimer: ReturnType<typeof setInterval> | null = null
  private unsubscribe: (() => void) | null = null

  constructor(private readonly bridge: import('./bridge').ScreenCaptureBridge) {}

  /** 页面加载后立即调用：建立不含画面的连接并发出邀约；此后一切经桥驱动 */
  start(): void {
    this.unsubscribe = this.bridge.onHostMessage((message) => this.onHostMessage(message))
    void this.open()
  }

  private emit(message: Record<string, unknown>): void {
    this.bridge.sendPageMessage(message)
  }

  private async open(): Promise<void> {
    try {
      const mine = new RTCPeerConnection({
        iceServers: this.bridge.params.stunUrls.map((url) => ({ urls: url })),
        bundlePolicy: 'max-bundle',
      })
      this.pc = mine
      mine.onconnectionstatechange = () => this.onConnectionState(mine)
      mine.onicecandidate = (event) => this.onLocalCandidate(mine, event)
      // 先建一条不含画面的视频通道；确认非同机后用 replaceTrack 接画面，不需要重新协商
      const transceiver = mine.addTransceiver('video', { direction: 'sendonly' })
      this.transceiver = transceiver
      try {
        const capabilities = RTCRtpSender.getCapabilities('video')
        if (capabilities) transceiver.setCodecPreferences(orderH264Codecs(capabilities.codecs))
      } catch {
        // 编码偏好不可设置时按默认协商；占用差异由 4.5 的降画质兜底
      }
      const offer = await mine.createOffer()
      await mine.setLocalDescription(offer)
      if (this.pc !== mine || this.finished) return
      const description = mine.currentLocalDescription ?? mine.localDescription
      this.emit({ kind: 'offer', sdp: (description ?? offer).sdp })
    } catch {
      this.closeAll('connect_failed')
    }
  }

  private onLocalCandidate(mine: RTCPeerConnection, event: RTCPeerConnectionIceEvent): void {
    if (this.pc !== mine || this.finished) return
    const candidate = event.candidate
    if (!candidate) return
    const line = candidate.candidate || ''
    const parts = line.split(' ')
    // 采集窗口拿过屏幕采集权限，本机候选是真实地址：srflx 的公网映射地址是明文，供同机判定与出口比较
    if ((parts[7] ?? '') === 'srflx') {
      const address = normalizeAddress(parts[4])
      if (address) this.emit({ kind: 'mapped', address })
    }
    this.emit({ kind: 'candidate', candidate: line, sdp_mid: candidate.sdpMid ?? null, sdp_mline_index: candidate.sdpMLineIndex ?? null })
  }

  private onConnectionState(mine: RTCPeerConnection): void {
    if (this.pc !== mine || this.finished) return
    const state = mine.connectionState
    if (state === 'connected' && !this.connectedEmitted) {
      this.connectedEmitted = true
      // 不含画面的连接已建立：Host 进入验证阶段并判定观看设备
      this.emit({ kind: 'connected' })
      this.startPeerPortsPoll(mine)
      return
    }
    if (state === 'failed' || state === 'closed') {
      this.closeAll(this.captureStarted ? 'runtime_stopped' : 'connect_failed')
    }
    // disconnected 先等重连或转 failed，不结束（POC 同款处理）
  }

  private startPeerPortsPoll(mine: RTCPeerConnection): void {
    if (this.peerPortsTimer || this.finished) return
    let attempts = 0
    this.peerPortsTimer = setInterval(() => {
      attempts += 1
      if (this.finished || this.pc !== mine || attempts > PEER_PORT_POLLS) {
        this.stopPeerPortsPoll()
        return
      }
      void mine.getStats().then((report) => {
        if (this.finished || this.pc !== mine) {
          this.stopPeerPortsPoll()
          return
        }
        const ports = peerPortsFromStats(report)
        if (ports.length) {
          this.stopPeerPortsPoll()
          this.emit({ kind: 'peer-ports', ports })
        }
      }).catch(() => {})
    }, PEER_PORT_POLL_MS)
  }

  private stopPeerPortsPoll(): void {
    if (this.peerPortsTimer) {
      clearInterval(this.peerPortsTimer)
      this.peerPortsTimer = null
    }
  }

  private startStatsLoop(mine: RTCPeerConnection): void {
    if (this.statsTimer) return
    this.statsTimer = setInterval(() => {
      if (this.finished || this.pc !== mine) {
        this.stopStatsLoop()
        return
      }
      void mine.getStats().then((report) => {
        if (this.finished || this.pc !== mine) return
        const limited = encoderCpuLimited(report)
        // 只在状态变化时上报：信令按整个会话计数（设计 5.4），每 5 秒无变化也上报会浪费上限
        if (limited !== null && limited !== this.cpuLimitedReported) {
          this.cpuLimitedReported = limited
          this.emit({ kind: 'cpu-limited', limited })
        }
      }).catch(() => {})
    }, STATS_INTERVAL_MS)
  }

  private stopStatsLoop(): void {
    if (this.statsTimer) {
      clearInterval(this.statsTimer)
      this.statsTimer = null
    }
  }

  private onHostMessage(raw: unknown): void {
    const message = asRecord(raw)
    if (!message || typeof message['kind'] !== 'string' || this.finished) return
    switch (message['kind']) {
      case 'answer': {
        const sdp = message['sdp']
        if (typeof sdp !== 'string' || !this.pc || this.remoteDescriptionSet) return
        this.remoteDescriptionSet = true
        void this.pc.setRemoteDescription({ type: 'answer', sdp }).catch(() => {})
        return
      }
      case 'candidate': {
        const candidate = message['candidate']
        if (typeof candidate !== 'string' || !candidate || !this.pc) return
        // 网页的候选既是 ICE 输入，也是同机判定的证据；Host 按收到 remote-candidate 的顺序编号解析
        this.emit({ kind: 'remote-candidate', candidate })
        void this.pc.addIceCandidate({
          candidate,
          sdpMid: textOrNull(message['sdp_mid']),
          sdpMLineIndex: numberOrNull(message['sdp_mline_index']),
        }).catch(() => {})
        return
      }
      case 'resolved':
        // 同机判定在 Host 完成，页面不需要名字解析结果
        return
      case 'verdict': {
        const verdict = message['verdict']
        if (verdict === 'other') {
          void this.beginCapture()
          return
        }
        // 同机/无法确认：全程不采集。reason 用协议允许的结束原因（设计 §10）：
        // 网页据此分别显示「同一台电脑无法使用」与「无法确认是否为同一台电脑」
        this.closeAll(verdict === 'same' ? 'same_machine' : 'machine_check_unknown')
        return
      }
      case 'degrade':
        void this.applyTrackConstraints(DEGRADED_CONSTRAINTS)
        return
      case 'restore':
        void this.applyTrackConstraints(this.savedConstraints)
        return
      default:
        return
    }
  }

  private async beginCapture(): Promise<void> {
    const mine = this.pc
    if (!mine || this.finished || this.captureStarted) return
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({ audio: false, video: CAPTURE_CONSTRAINTS })
    } catch {
      this.closeAll('connect_failed')
      return
    }
    if (this.pc !== mine || this.finished) {
      for (const track of stream.getTracks()) track.stop()
      return
    }
    const track = stream.getVideoTracks()[0]
    if (!track) {
      this.closeAll('connect_failed')
      return
    }
    const sender = this.transceiver?.sender
    if (!sender) {
      this.closeAll('connect_failed')
      return
    }
    track.addEventListener('ended', () => {
      if (!this.finished) this.closeAll('runtime_stopped')
    })
    try {
      track.contentHint = 'detail'
    } catch {
      // contentHint 不可用时按默认提示
    }
    try {
      await sender.replaceTrack(track)
    } catch {
      this.closeAll('connect_failed')
      return
    }
    try {
      const parameters = sender.getParameters()
      parameters.degradationPreference = 'maintain-resolution'
      if (!parameters.encodings || parameters.encodings.length === 0) parameters.encodings = [{}]
      parameters.encodings[0]!.maxBitrate = MAX_BITRATE
      await sender.setParameters(parameters)
    } catch {
      // 码率上限设置失败时按编码器默认继续
    }
    this.track = track
    this.captureStarted = true
    this.savedConstraints = typeof track.getConstraints === 'function' ? track.getConstraints() : {}
    // 画面接上后报告实际线路（取不到时省略，Host 发不带 path 的 streaming 状态）
    const path = await this.currentPath(mine)
    if (this.pc === mine && !this.finished) this.emit({ kind: 'streaming', ...path })
    this.startStatsLoop(mine)
  }

  private async currentPath(mine: RTCPeerConnection): Promise<{ path: 'lan' | 'p2p' } | Record<string, never>> {
    try {
      const path = pathFromStats(await mine.getStats())
      return path ? { path } : {}
    } catch {
      return {}
    }
  }

  private async applyTrackConstraints(constraints: MediaTrackConstraints): Promise<void> {
    const track = this.track
    if (!track || this.finished) return
    try {
      await track.applyConstraints(constraints)
    } catch {
      // 降低/恢复失败时保持当前画质，观看继续
    }
  }

  /** 结束连接与采集。reason 为 Runtime 的结束原因；出画面前 connect_failed，之后 runtime_stopped */
  private closeAll(reason?: string): void {
    if (this.finished) return
    this.finished = true
    this.stopPeerPortsPoll()
    this.stopStatsLoop()
    if (this.track) {
      try {
        this.track.stop()
      } catch {
        // 已停止
      }
      this.track = null
    }
    const mine = this.pc
    this.pc = null
    this.transceiver = null
    if (mine) {
      try {
        mine.close()
      } catch {
        // 已关闭
      }
    }
    this.emit(reason ? { kind: 'closed', reason } : { kind: 'closed' })
    this.unsubscribe?.()
    this.unsubscribe = null
  }
}
