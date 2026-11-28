/**
 * 采集页逻辑单测（jsdom + 假 RTCPeerConnection/RTCRtpSender/navigator.mediaDevices/桥 + mock 时钟）：
 * H.264 排序、verdict 门（same/unknown 不采集）、采集与编码参数、peer-ports 收集与超时、
 * 线路判定、closed reason 阶段映射、cpu-limited 上报、degrade/restore 约束。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getScreenCaptureBridge, type ScreenCaptureBridge } from '../bridge'
import {
  classifyAddressKind,
  derivePath,
  encoderCpuLimited,
  orderH264Codecs,
  pathFromStats,
  peerPortsFromStats,
  ScreenCaptureClient,
} from '../screenCaptureClient'

const STATS_INTERVAL = 5_000
const SRFLX_LINE = 'candidate:2 1 udp 2130706431 8.8.8.8 50001 typ srflx raddr 0 rport 0 generation 0'
const HOST_LINE = 'candidate:1 1 udp 2130706431 10.1.2.3 50002 typ host generation 0'
const WEB_LINE = 'candidate:3 1 udp 2130706431 10.200.100.9 50000 typ host generation 0'

// ---- 假对象 ----

class FakeSender {
  track: unknown = null
  replaceTrackCalls: unknown[] = []
  parameters: Record<string, unknown> = { encodings: [{}] }
  replaceTrack = vi.fn(async (track: unknown) => { this.track = track; this.replaceTrackCalls.push(track) })
  setParameters = vi.fn(async (parameters: Record<string, unknown>) => { this.parameters = parameters })
  getParameters() { return this.parameters }
}

class FakeTransceiver {
  direction = 'sendonly'
  sender = new FakeSender()
  setCodecPreferences = vi.fn()
}

class FakeTrack {
  ended: (() => void) | null = null
  stopped = false
  contentHint = ''
  constraints: Record<string, unknown> = { width: { max: 1280 }, height: { max: 720 }, frameRate: { max: 15, ideal: 15 } }
  applyConstraints = vi.fn(async (next: Record<string, unknown>) => { this.constraints = next })
  getConstraints() { return this.constraints }
  stop() { this.stopped = true }
  addEventListener(_type: string, handler: () => void) { this.ended = handler }
}

class FakePeerConnection {
  static instances: FakePeerConnection[] = []
  connectionState = 'new'
  onconnectionstatechange: (() => void) | null = null
  onicecandidate: ((event: { candidate: { candidate: string; sdpMid?: string; sdpMLineIndex?: number } | null }) => void) | null = null
  transceivers: FakeTransceiver[] = []
  closed = false
  getStats = vi.fn(async () => new Map<string, Record<string, unknown>>() as unknown as RTCStatsReport)
  setRemoteDescription = vi.fn(async () => {})
  addIceCandidate = vi.fn(async () => {})
  iceServers: unknown
  bundlePolicy: unknown
  localDescription = { type: 'offer', sdp: 'v=0-fake-offer' }
  currentLocalDescription = this.localDescription

  constructor(config: Record<string, unknown>) {
    this.iceServers = config['iceServers']
    this.bundlePolicy = config['bundlePolicy']
    FakePeerConnection.instances.push(this)
  }

  addTransceiver() {
    const transceiver = new FakeTransceiver()
    this.transceivers.push(transceiver)
    return transceiver
  }
  async createOffer() { return { type: 'offer', sdp: 'v=0-fake-offer' } }
  async setLocalDescription() { /* 邀约记录在 localDescription 字段上 */ }
  close() { this.closed = true }
  setState(state: string) { this.connectionState = state; this.onconnectionstatechange?.() }
  emitCandidate(line: string, sdpMid?: string, sdpMLineIndex?: number) {
    this.onicecandidate?.({ candidate: { candidate: line, sdpMid, sdpMLineIndex } })
  }
}

// ---- 工具 ----

function fakeBridge(params: { sessionId: string; stunUrls: string[] } = { sessionId: 'sess-1', stunUrls: ['stun:a:3478'] }) {
  const sent: Array<Record<string, unknown>> = []
  const listeners = new Set<(message: unknown) => void>()
  const bridge: ScreenCaptureBridge = {
    params,
    onHostMessage: (callback) => { listeners.add(callback); return () => { listeners.delete(callback) } },
    sendPageMessage: (message) => { sent.push(message as Record<string, unknown>) },
  }
  return { sent, emit: (message: unknown) => listeners.forEach((listener) => listener(message)), bridge }
}

async function flush(): Promise<void> { for (let i = 0; i < 20; i++) await Promise.resolve() }

async function startClient(bridge: ScreenCaptureBridge): Promise<FakePeerConnection> {
  new ScreenCaptureClient(bridge).start()
  await flush()
  return FakePeerConnection.instances[FakePeerConnection.instances.length - 1]!
}

function statsOf(entries: Array<[string, Record<string, unknown>]>): RTCStatsReport {
  return new Map(entries) as unknown as RTCStatsReport
}

function displayMediaMock(): ReturnType<typeof vi.fn> {
  return (window.navigator.mediaDevices as unknown as { getDisplayMedia: ReturnType<typeof vi.fn> }).getDisplayMedia
}

function captureConstraintEntry(): Array<[string, Record<string, unknown>]> {
  return [
    ['pair-1', { type: 'candidate-pair', state: 'succeeded', nominated: true, localCandidateId: 'l1', remoteCandidateId: 'r1' }],
    ['l1', { type: 'local-candidate', address: '10.0.0.2' }],
    ['r1', { type: 'remote-candidate', address: '10.0.0.9', port: 50003 }],
  ]
}

beforeEach(() => {
  vi.useFakeTimers()
  FakePeerConnection.instances = []
  vi.stubGlobal('RTCPeerConnection', FakePeerConnection)
  vi.stubGlobal('RTCRtpSender', { getCapabilities: vi.fn(() => ({ codecs: [{ mimeType: 'video/VP8' }, { mimeType: 'video/H264' }] })) })
  Object.defineProperty(window.navigator, 'mediaDevices', {
    configurable: true,
    value: { getDisplayMedia: vi.fn(async () => { throw new Error('not driven by this test') }) },
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

// ---- 纯函数 ----

describe('纯函数', () => {
  it('H.264 排到编码首选，其余保持原序', () => {
    const codecs = [{ mimeType: 'video/VP8' }, { mimeType: 'video/H264' }, { mimeType: 'video/rtx' }, { mimeType: 'video/H265' }]
    expect(orderH264Codecs(codecs).map((codec) => codec.mimeType)).toEqual(['video/H264', 'video/VP8', 'video/rtx', 'video/H265'])
  })

  it('地址分类照 POC classify：mdns/环回/内网/共享段/公网/其他', () => {
    expect(classifyAddressKind('a1b2c3d4-5.local')).toBe('mdns')
    expect(classifyAddressKind('127.0.0.1')).toBe('loopback')
    expect(classifyAddressKind('10.200.100.9')).toBe('private')
    expect(classifyAddressKind('192.168.1.4')).toBe('private')
    expect(classifyAddressKind('172.17.0.1')).toBe('private')
    expect(classifyAddressKind('100.100.1.2')).toBe('shared')
    expect(classifyAddressKind('8.8.8.8')).toBe('public')
    expect(classifyAddressKind('')).toBe('other')
    expect(derivePath('10.0.0.1', 'a1b2.local')).toBe('lan')
    expect(derivePath('10.0.0.1', '8.8.8.8')).toBe('p2p')
    expect(derivePath(null, '10.0.0.1')).toBe('p2p')
  })

  it('peer-ports 从成功候选对里取对端端口并去重；线路按两端地址分类', () => {
    const report = statsOf([
      ['pair-1', { type: 'candidate-pair', state: 'succeeded', nominated: true, localCandidateId: 'l1', remoteCandidateId: 'r1' }],
      ['l1', { type: 'local-candidate', address: '10.0.0.2' }],
      ['r1', { type: 'remote-candidate', address: '10.0.0.9', port: 50003, protocol: 'udp' }],
      ['pair-2', { type: 'candidate-pair', state: 'succeeded', localCandidateId: 'l2', remoteCandidateId: 'r2' }],
      ['r2', { type: 'remote-candidate', port: 50003, protocol: 'udp' }],
    ])
    expect(peerPortsFromStats(report)).toEqual([{ port: 50003, protocol: 'udp' }])
    expect(pathFromStats(report)).toBe('lan')
    const other = statsOf([
      ['pair-1', { type: 'candidate-pair', state: 'succeeded', nominated: true, localCandidateId: 'l1', remoteCandidateId: 'r1' }],
      ['l1', { type: 'local-candidate', address: '10.0.0.2' }],
      ['r1', { type: 'remote-candidate', address: '203.0.113.7', port: 40001 }],
    ])
    expect(pathFromStats(other)).toBe('p2p')
    expect(pathFromStats(new Map() as unknown as RTCStatsReport)).toBe(null)
  })

  it('cpu-limited 取发送方向视频统计的 qualityLimitationReason', () => {
    const limited = statsOf([['o1', { type: 'outbound-rtp', kind: 'video', qualityLimitationReason: 'cpu' }]])
    const fine = statsOf([['o1', { type: 'outbound-rtp', kind: 'video', qualityLimitationReason: 'none' }]])
    const missing = statsOf([['i1', { type: 'inbound-rtp', kind: 'video' }]])
    expect(encoderCpuLimited(limited)).toBe(true)
    expect(encoderCpuLimited(fine)).toBe(false)
    expect(encoderCpuLimited(missing)).toBe(null)
  })
})

// ---- 建连与信令 ----

describe('建连与邀约', () => {
  it('启动即建连：iceServers 来自窗口参数、H.264 首选、offer 先于本机候选发出', async () => {
    const { sent, bridge } = fakeBridge({ sessionId: 'sess-9', stunUrls: ['stun:a:3478', 'stun:b:3478'] })
    const pc = await startClient(bridge)
    expect(pc.iceServers).toEqual([{ urls: 'stun:a:3478' }, { urls: 'stun:b:3478' }])
    expect(pc.bundlePolicy).toBe('max-bundle')
    expect(pc.transceivers[0]!.direction).toBe('sendonly')
    const preferences = pc.transceivers[0]!.setCodecPreferences.mock.calls[0]![0] as Array<{ mimeType: string }>
    expect(preferences[0]!.mimeType.toLowerCase()).toBe('video/h264')
    expect(sent[0]).toEqual({ kind: 'offer', sdp: 'v=0-fake-offer' })
    pc.emitCandidate(HOST_LINE, '0', 0)
    expect(sent[1]).toEqual({ kind: 'candidate', candidate: HOST_LINE, sdp_mid: '0', sdp_mline_index: 0 })
  })

  it('srflx 候选的公网映射地址先上报 mapped，host 候选不上报', async () => {
    const { sent, bridge } = fakeBridge()
    const pc = await startClient(bridge)
    pc.emitCandidate(SRFLX_LINE, '0', 0)
    pc.emitCandidate(HOST_LINE)
    expect(sent[1]).toEqual({ kind: 'mapped', address: '8.8.8.8' })
    expect(sent[2]).toEqual({ kind: 'candidate', candidate: SRFLX_LINE, sdp_mid: '0', sdp_mline_index: 0 })
    expect(sent[3]).toEqual({ kind: 'candidate', candidate: HOST_LINE, sdp_mid: null, sdp_mline_index: null })
  })
})

describe('Host 消息', () => {
  it('answer 进远端描述；网页候选回发 remote-candidate 并加入 ICE；resolved 忽略', async () => {
    const { sent, emit, bridge } = fakeBridge()
    const pc = await startClient(bridge)
    emit({ kind: 'answer', sdp: 'v=0-answer' })
    await flush()
    expect(pc.setRemoteDescription).toHaveBeenCalledWith({ type: 'answer', sdp: 'v=0-answer' })
    emit({ kind: 'candidate', candidate: WEB_LINE, sdp_mid: null, sdp_mline_index: 1 })
    await flush()
    expect(sent.find((message) => message['kind'] === 'remote-candidate')).toEqual({ kind: 'remote-candidate', candidate: WEB_LINE })
    expect(pc.addIceCandidate).toHaveBeenCalledWith({ candidate: WEB_LINE, sdpMid: null, sdpMLineIndex: 1 })
    emit({ kind: 'resolved', id: 1, address: null })
    expect(sent.filter((message) => message['kind'] === 'resolved')).toHaveLength(0)
  })

  it('verdict same/unknown 不采集：closed 分别带协议允许的 same_machine / machine_check_unknown', async () => {
    const cases = [
      { verdict: 'same', expected: { kind: 'closed', reason: 'same_machine' } },
      { verdict: 'unknown', expected: { kind: 'closed', reason: 'machine_check_unknown' } },
    ] as const
    for (const { verdict, expected } of cases) {
      const { sent, emit, bridge } = fakeBridge()
      await startClient(bridge)
      emit({ kind: 'verdict', verdict, rule: 1 })
      await flush()
      expect(displayMediaMock()).not.toHaveBeenCalled()
      expect(sent[sent.length - 1]).toEqual(expected)
    }
  })

  it('unknown 判定报 machine_check_unknown：网页据此显示「无法确认是否为同一台电脑」（设计 §10）', async () => {
    const { sent, emit, bridge } = fakeBridge()
    await startClient(bridge)
    emit({ kind: 'verdict', verdict: 'unknown', rule: 4 })
    await flush()
    const closed = sent.filter((message) => message['kind'] === 'closed')
    expect(closed).toEqual([{ kind: 'closed', reason: 'machine_check_unknown' }])
    expect(displayMediaMock()).not.toHaveBeenCalled()
  })

  it('结束后的消息不再驱动采集', async () => {
    const { emit, bridge } = fakeBridge()
    await startClient(bridge)
    emit({ kind: 'verdict', verdict: 'same', rule: 1 })
    await flush()
    emit({ kind: 'verdict', verdict: 'other', rule: 1 })
    await flush()
    expect(displayMediaMock()).not.toHaveBeenCalled()
  })
})

describe('采集与统计', () => {
  interface Fixture {
    sent: Array<Record<string, unknown>>
    emit: (message: unknown) => void
    pc: FakePeerConnection
    track: FakeTrack
    sender: FakeSender
    getDisplay: ReturnType<typeof vi.fn>
  }

  async function captureStarted(): Promise<Fixture> {
    const fixture = fakeBridge()
    const pc = await startClient(fixture.bridge)
    const track = new FakeTrack()
    vi.mocked(displayMediaMock()).mockImplementation(async () => ({
      getVideoTracks: () => [track],
      getTracks: () => [track],
    }))
    pc.getStats.mockResolvedValue(statsOf(captureConstraintEntry()))
    fixture.emit({ kind: 'verdict', verdict: 'other', rule: 1 })
    await flush()
    return { ...fixture, pc, track, sender: pc.transceivers[0]!.sender, getDisplay: displayMediaMock() }
  }

  it('verdict other 才采集：720p/15 帧约束、画面接到已协商通道、码率上限与画质策略、streaming 带线路', async () => {
    const { sent, getDisplay, track, sender } = await captureStarted()
    expect(getDisplay).toHaveBeenCalledWith({ audio: false, video: { width: { max: 1280 }, height: { max: 720 }, frameRate: { max: 15, ideal: 15 } } })
    expect(sender.replaceTrackCalls[0]).toBe(track)
    expect(track.contentHint).toBe('detail')
    expect(sender.setParameters).toHaveBeenCalled()
    const parameters = sender.parameters as { degradationPreference?: string; encodings: Array<{ maxBitrate?: number }> }
    expect(parameters.degradationPreference).toBe('maintain-resolution')
    expect(parameters.encodings[0]!.maxBitrate).toBe(2500000)
    expect(sent.find((message) => message['kind'] === 'streaming')).toEqual({ kind: 'streaming', path: 'lan' })
  })

  it('连接建立先报 connected 并等成功候选对收 peer-ports（一次），超时静默', async () => {
    const { sent, bridge } = fakeBridge()
    const pc = await startClient(bridge)
    pc.getStats.mockResolvedValue(statsOf([
      ['pair-1', { type: 'candidate-pair', state: 'succeeded', nominated: true, localCandidateId: 'l1', remoteCandidateId: 'r1' }],
      ['l1', { type: 'local-candidate', address: '10.0.0.2' }],
      ['r1', { type: 'remote-candidate', port: 50003, protocol: 'udp' }],
    ]))
    pc.setState('connected')
    await flush()
    expect(sent.find((message) => message['kind'] === 'connected')).toEqual({ kind: 'connected' })
    await vi.advanceTimersByTimeAsync(200)
    expect(sent.filter((message) => message['kind'] === 'peer-ports')).toEqual([{ kind: 'peer-ports', ports: [{ port: 50003, protocol: 'udp' }] }])
    // 没有成功候选对时最多等约 1.5 秒，之后不再上报
    const quiet = fakeBridge()
    const quietPc = await startClient(quiet.bridge)
    quietPc.getStats.mockResolvedValue(statsOf([]))
    quietPc.setState('connected')
    await flush()
    await vi.advanceTimersByTimeAsync(1_700)
    expect(quiet.sent.filter((message) => message['kind'] === 'peer-ports')).toHaveLength(0)
  })

  it('degrade 改采集轨道约束，restore 回到初始约束', async () => {
    const { emit, track } = await captureStarted()
    emit({ kind: 'degrade' })
    await flush()
    expect(track.applyConstraints).toHaveBeenCalledWith({ frameRate: { max: 5 }, width: { max: 854 }, height: { max: 480 } })
    emit({ kind: 'restore' })
    await flush()
    expect(track.applyConstraints).toHaveBeenLastCalledWith({ width: { max: 1280 }, height: { max: 720 }, frameRate: { max: 15, ideal: 15 } })
  })

  it('cpu-limited 只在状态变化时上报；出画面后轨道结束报 runtime_stopped', async () => {
    const { sent, pc, track } = await captureStarted()
    pc.getStats.mockResolvedValue(statsOf([['o1', { type: 'outbound-rtp', kind: 'video', qualityLimitationReason: 'cpu' }]]))
    await vi.advanceTimersByTimeAsync(STATS_INTERVAL + 100)
    expect(sent.filter((message) => message['kind'] === 'cpu-limited')).toEqual([{ kind: 'cpu-limited', limited: true }])
    await vi.advanceTimersByTimeAsync(STATS_INTERVAL)
    expect(sent.filter((message) => message['kind'] === 'cpu-limited')).toHaveLength(1)
    pc.getStats.mockResolvedValue(statsOf([['o1', { type: 'outbound-rtp', kind: 'video', qualityLimitationReason: 'none' }]]))
    await vi.advanceTimersByTimeAsync(STATS_INTERVAL)
    expect(sent.filter((message) => message['kind'] === 'cpu-limited')).toEqual([{ kind: 'cpu-limited', limited: true }, { kind: 'cpu-limited', limited: false }])
    track.ended?.()
    await flush()
    expect(sent[sent.length - 1]).toEqual({ kind: 'closed', reason: 'runtime_stopped' })
  })

  it('出画面前连接 failed 报 connect_failed，全程不采集', async () => {
    const { sent, bridge } = fakeBridge()
    const pc = await startClient(bridge)
    pc.setState('failed')
    await flush()
    expect(sent[sent.length - 1]).toEqual({ kind: 'closed', reason: 'connect_failed' })
    expect(displayMediaMock()).not.toHaveBeenCalled()
  })
})

describe('桥', () => {
  it('形状不完整时 fail loud，完整时返回桥', () => {
    expect(() => getScreenCaptureBridge()).toThrow()
    vi.stubGlobal('screenCapture', { params: { sessionId: 's' }, onHostMessage: () => () => {}, sendPageMessage: () => {} })
    expect(() => getScreenCaptureBridge()).toThrow()
    const complete = { params: { sessionId: 's', stunUrls: ['stun:a'] }, onHostMessage: () => () => {}, sendPageMessage: () => {} }
    vi.stubGlobal('screenCapture', complete)
    expect(getScreenCaptureBridge()).toBe(complete)
  })
})
