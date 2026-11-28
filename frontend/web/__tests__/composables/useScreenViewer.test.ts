/**
 * useScreenViewer 连接组合函数测试（mock API 与 RTCPeerConnection，假计时器驱动）
 *
 * 验证点（设计 5.4 / 6.3 / 10）：
 * - 流程顺序：创建会话 → prepared 门禁 → start → offer 应答 → status 驱动状态
 * - 轮询节奏从响应起算 + 抖动；503 与网络错误重试且不结束会话
 * - 早到的候选缓存、远端描述就绪后按顺序加入
 * - 结束路径：Runtime end 信令、轮询发现 ended、404、用户结束（连点只发一次）
 * - 锁屏 / 降画质 / 线路与观看统计
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScreenStreamApiError, createScreenSession, pollScreenSession, sendScreenSignal, endScreenSession, type ScreenSignal } from '@/api/screenStream'
import {
  PREPARED_POLL_INTERVAL_MS,
  POLL_JITTER_MS,
  WATCH_POLL_INTERVAL_MS,
  SCREEN_VIEWER_ENDS,
  useScreenViewer,
  type ScreenViewerApi,
  type ScreenViewerState,
} from '@/composables/useScreenViewer'

const mockCreate = vi.mocked(createScreenSession)
const mockPoll = vi.mocked(pollScreenSession)
const mockSend = vi.mocked(sendScreenSignal)
const mockEnd = vi.mocked(endScreenSession)

vi.mock('@/api/screenStream', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/screenStream')>()
  return {
    ...actual,
    createScreenSession: vi.fn(),
    pollScreenSession: vi.fn(),
    sendScreenSignal: vi.fn(),
    endScreenSession: vi.fn(),
  }
})

const DEVICE = { device_id: 'dev-1', name: '测试电脑' }
const CREATED = { sessionId: 'sess-1', state: 'pending', stunUrls: ['stun:stun.example.com:3478'] }
const PENDING = { session: { id: 'sess-1', state: 'pending', end_reason: null }, signals: [] }

function pendingSignals(signals: Array<{ seq: number; type: ScreenSignal['type']; payload?: Record<string, any> }>) {
  return {
    session: { id: 'sess-1', state: 'pending', end_reason: null },
    signals: signals.map((signal) => ({ seq: signal.seq, type: signal.type, payload: signal.payload ?? {} })),
  }
}

class FakePeerConnection {
  static instances: FakePeerConnection[] = []
  iceServers: Array<{ urls: string }> = []
  connectionState: RTCPeerConnectionState = 'new'
  ontrack: ((event: { track: MediaStreamTrack }) => void) | null = null
  onicecandidate: ((event: { candidate: RTCIceCandidate | null }) => void) | null = null
  onconnectionstatechange: (() => void) | null = null
  remote: RTCSessionDescriptionInit | null = null
  local: RTCSessionDescriptionInit | null = null
  addedCandidates: Array<RTCIceCandidateInit | null> = []
  statsQueue: RTCStatsReport[] = []

  constructor(config: RTCConfiguration) {
    this.iceServers = (config.iceServers ?? []) as Array<{ urls: string }>
    FakePeerConnection.instances.push(this)
  }
  get currentLocalDescription() { return this.local }
  get localDescription() { return this.local }
  async setRemoteDescription(description: RTCSessionDescriptionInit) { this.remote = description }
  async createAnswer() { return { type: 'answer' as const, sdp: 'answer-sdp' } }
  async setLocalDescription(description: RTCSessionDescriptionInit) { this.local = description }
  async addIceCandidate(candidate: RTCIceCandidateInit | null) { this.addedCandidates.push(candidate) }
  async getStats() { return this.statsQueue.shift() ?? emptyReport() }
  close() { this.connectionState = 'closed' }
  /** 模拟连接失败（如观看中对端消失） */
  fail() { this.connectionState = 'failed'; this.onconnectionstatechange?.() }
}

const fakeTrack = { kind: 'video', stop: vi.fn() } as unknown as MediaStreamTrack
const FAKE_CANDIDATE_LINE = 'candidate:1 1 udp 2122260223 192.168.1.5 50000 typ host'
const fakeCandidate = {
  candidate: FAKE_CANDIDATE_LINE,
  sdpMid: '0',
  sdpMLineIndex: 0,
  toJSON() {
    return { candidate: FAKE_CANDIDATE_LINE, sdpMid: '0', sdpMLineIndex: 0 }
  },
} as unknown as RTCIceCandidate

function emptyReport(): RTCStatsReport {
  return {
    forEach(_callback: (record: unknown) => void) { /* 无记录 */ },
    get(_id: string) { return undefined },
  } as unknown as RTCStatsReport
}

function reportWith(records: Array<Record<string, unknown>>): RTCStatsReport {
  return {
    forEach(callback: (record: unknown) => void) { records.forEach((record) => callback(record)) },
    get(id: string) { return records.find((record) => record['id'] === id) },
  } as unknown as RTCStatsReport
}

function watchingStatsReport(overrides: Record<string, unknown> = {}): RTCStatsReport {
  return reportWith([
    { id: 'lc', type: 'local-candidate', address: '192.168.1.10' },
    { id: 'rc', type: 'remote-candidate', address: '192.168.5.20' },
    { id: 'pair', type: 'candidate-pair', state: 'succeeded', nominated: true, localCandidateId: 'lc', remoteCandidateId: 'rc' },
    {
      type: 'inbound-rtp', kind: 'video',
      frameWidth: 1280, frameHeight: 720, framesPerSecond: null,
      bytesReceived: 1_000, framesDecoded: 0, timestamp: 0,
      ...overrides,
    },
  ])
}

let viewer: ReturnType<typeof useScreenViewer> | null = null

function makeApi(): ScreenViewerApi {
  return { createSession: mockCreate, pollSession: mockPoll, sendSignal: mockSend, endSession: mockEnd }
}

function createViewer(): ReturnType<typeof useScreenViewer> {
  viewer = useScreenViewer({ api: makeApi(), random: () => 0.5 })
  return viewer
}

async function drive(ms: number): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms)
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('RTCPeerConnection', FakePeerConnection)
  FakePeerConnection.instances.length = 0
  vi.clearAllMocks()
  mockSend.mockResolvedValue(1)
  mockEnd.mockResolvedValue(undefined)
  mockCreate.mockResolvedValue({ ...CREATED })
})

afterEach(() => {
  viewer?.dispose()
  viewer = null
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('连接三步与 prepared 门禁', () => {
  it('创建会话 → 轮询 → 收到 prepared 才发 start，步骤依次推进', async () => {
    mockPoll.mockResolvedValue(pendingSignals([]))
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(mockCreate).toHaveBeenCalledExactlyOnceWith('dev-1')
    expect(FakePeerConnection.instances).toHaveLength(1)
    const pc = FakePeerConnection.instances[0]!
    // stun_urls 作为 iceServers 传入
    expect(pc.iceServers).toEqual([{ urls: 'stun:stun.example.com:3478' }])
    expect(v.state.phase).toBe('connecting')
    expect(v.state.step).toBe(1)
    expect(mockSend).not.toHaveBeenCalled()

    // 第一次轮询：还没有 prepared，不发 start
    await drive(0)
    expect(mockPoll).toHaveBeenCalledExactlyOnceWith('dev-1', 'sess-1', 0)
    expect(mockSend).not.toHaveBeenCalled()

    // 收到 prepared → 发 start（门禁）
    mockPoll.mockResolvedValue(pendingSignals([{ seq: 1, type: 'prepared', payload: {} }]))
    await drive(600)
    expect(mockSend).toHaveBeenCalledWith('dev-1', 'sess-1', 'start', {})
    expect(v.state.step).toBe(2)
  })

  it('status connected → 第三步；status streaming → 观看中并切到 5 秒轮询（从响应起算 + 抖动）', async () => {
    mockPoll
      .mockResolvedValueOnce(pendingSignals([{ seq: 1, type: 'prepared', payload: {} }]))
      .mockResolvedValueOnce(pendingSignals([{ seq: 2, type: 'offer', payload: { sdp: 'offer-sdp' } }]))
      .mockResolvedValueOnce(pendingSignals([{ seq: 3, type: 'status', payload: { state: 'connected' } }]))
      .mockResolvedValueOnce(pendingSignals([{ seq: 4, type: 'status', payload: { state: 'streaming', path: 'lan' } }]))
      .mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)   // 第一次轮询（prepared）
    await drive(600) // 第二次（offer → 应答）
    expect(mockSend).toHaveBeenCalledWith('dev-1', 'sess-1', 'answer', { sdp: 'answer-sdp' })
    await drive(600) // 第三次（connected）
    expect(v.state.step).toBe(3)
    await drive(600) // 第四次（streaming）
    expect(v.state.phase).toBe('watching')
    expect(v.state.path).toBe('lan')
    expect(v.state.locked).toBe(false)

    // 出画面后：500ms 不再触发下一次轮询（间隔已切到 5 秒 + 抖动），从响应起算满 5 秒才轮询
    const calls = mockPoll.mock.calls.length
    await drive(500)
    expect(mockPoll).toHaveBeenCalledTimes(calls)
    await drive(WATCH_POLL_INTERVAL_MS)
    expect(mockPoll).toHaveBeenCalledTimes(calls + 1)
  })

  it('未出画面的轮询间隔为 500ms + 抖动', async () => {
    mockPoll.mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    const calls = mockPoll.mock.calls.length
    // random=0.5 → 间隔 500 + 50 = 550ms；549ms 内不轮询，到点再来
    await drive(PREPARED_POLL_INTERVAL_MS + POLL_JITTER_MS / 2 - 1)
    expect(mockPoll).toHaveBeenCalledTimes(calls)
    await drive(1)
    expect(mockPoll).toHaveBeenCalledTimes(calls + 1)
  })

  it('ontrack 给的轨道直接挂上（不预加通道）；网页候选经 signals 上报', async () => {
    mockPoll.mockResolvedValue(pendingSignals([{ seq: 1, type: 'prepared', payload: {} }]))
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    const pc = FakePeerConnection.instances[0]!
    pc.ontrack?.({ track: fakeTrack })
    expect(v.track.value).toBe(fakeTrack)

    pc.onicecandidate?.({ candidate: fakeCandidate })
    await drive(0)
    expect(mockSend).toHaveBeenCalledWith('dev-1', 'sess-1', 'candidate', {
      candidate: fakeCandidate.candidate,
      sdp_mid: '0',
      sdp_mline_index: 0,
    })
  })
})

describe('早到的候选缓存', () => {
  it('收到 offer 之前到达的 candidate 先缓存，远端描述设置完再按顺序加入', async () => {
    mockPoll
      .mockResolvedValueOnce(pendingSignals([
        { seq: 1, type: 'candidate', payload: { candidate: 'cand-early', sdp_mid: '0', sdp_mline_index: 0 } },
        { seq: 2, type: 'candidate', payload: { candidate: 'cand-early-2', sdp_mid: '0', sdp_mline_index: 0 } },
      ]))
      .mockResolvedValueOnce(pendingSignals([{ seq: 3, type: 'offer', payload: { sdp: 'offer-sdp' } }]))
      .mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    const pc = FakePeerConnection.instances[0]!
    // 远端描述还没设置：候选没有加入，也没有应答
    expect(pc.remote).toBe(null)
    expect(pc.addedCandidates).toHaveLength(0)
    expect(mockSend).not.toHaveBeenCalledWith('dev-1', 'sess-1', 'answer', expect.anything())

    await drive(600)
    // offer 处理完：远端描述就绪，早到的候选按到达顺序加入，随后发 answer
    expect(pc.remote).toEqual({ type: 'offer', sdp: 'offer-sdp' })
    expect(pc.addedCandidates.map((c) => c?.candidate)).toEqual(['cand-early', 'cand-early-2'])
    expect(mockSend).toHaveBeenCalledWith('dev-1', 'sess-1', 'answer', { sdp: 'answer-sdp' })
    expect(v.state.phase).toBe('connecting')
  })
})

describe('503 与网络错误：稍后重试，不结束会话', () => {
  it('轮询遇 SCREEN_STREAM_BUSY 按重试间隔再来，会话不结束', async () => {
    mockPoll
      .mockRejectedValueOnce(new ScreenStreamApiError('SCREEN_STREAM_BUSY', '实时画面暂时繁忙，请稍后重试'))
      .mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.phase).toBe('connecting')
    expect(v.state.end).toBe(null)
    expect(mockEnd).not.toHaveBeenCalled()
    await drive(500)
    expect(mockPoll).toHaveBeenCalledTimes(2)
  })

  it('轮询遇网络错误同样重试', async () => {
    mockPoll
      .mockRejectedValueOnce(new TypeError('fetch failed'))
      .mockResolvedValue(PENDING)
    createViewer().start(DEVICE)
    await drive(0)
    await drive(500)
    expect(mockPoll).toHaveBeenCalledTimes(2)
  })

  it('发送 start 遇繁忙时重试后成功', async () => {
    mockPoll
      .mockResolvedValueOnce(pendingSignals([{ seq: 1, type: 'prepared', payload: {} }]))
      .mockResolvedValue(PENDING)
    mockSend
      .mockRejectedValueOnce(new ScreenStreamApiError('SCREEN_STREAM_BUSY', '繁忙'))
      .mockResolvedValueOnce(2)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(mockSend).toHaveBeenCalledTimes(1)
    await drive(500)
    expect(mockSend).toHaveBeenCalledTimes(2)
    expect(mockSend).toHaveBeenLastCalledWith('dev-1', 'sess-1', 'start', {})
    expect(v.state.phase).toBe('connecting')
  })
})

describe('结束路径', () => {
  it('Runtime 的 end 信令按原因映射结束态', async () => {
    mockPoll.mockResolvedValue(pendingSignals([{ seq: 1, type: 'end', payload: { reason: 'renewal_timeout' } }]))
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.phase).toBe('ended')
    expect(v.state.end?.key).toBe('renewal_timeout')
    expect(v.state.end?.title).toBe('画面已自动停止')
    expect(v.state.end?.description).toContain('不会去动这台电脑上的任务')
    expect(mockEnd).not.toHaveBeenCalled()
  })

  it('轮询发现会话已结束（state=ended）按 end_reason 映射', async () => {
    mockPoll.mockResolvedValue({ session: { id: 'sess-1', state: 'ended', end_reason: 'resource_busy' }, signals: [] })
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.end?.key).toBe('resource_busy')
    expect(v.state.end?.title).toBe('画面已停止')
    expect(v.state.end?.retryable).toBe(true)
  })

  it('轮询 404（会话不存在）结束本地观看，展示兜底文案', async () => {
    mockPoll.mockRejectedValue(new ScreenStreamApiError('SCREEN_SESSION_NOT_FOUND', '会话不存在或无权访问'))
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.phase).toBe('ended')
    expect(v.state.end?.key).toBe('failed')
    expect(v.state.end?.title).toBe('画面连接已结束')
  })

  it('发送 answer 时会话已结束（409）→ 按缓存原因或兜底结束，不再发 end', async () => {
    mockPoll
      .mockResolvedValueOnce(pendingSignals([{ seq: 1, type: 'offer', payload: { sdp: 'offer-sdp' } }]))
      .mockResolvedValue(PENDING)
    mockSend.mockRejectedValue(new ScreenStreamApiError('SCREEN_SESSION_ENDED', '会话已结束'))
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    await drive(0)
    expect(v.state.phase).toBe('ended')
    expect(mockEnd).not.toHaveBeenCalled()
  })

  it('创建会话报 DEVICE_OFFLINE / SCREEN_SESSION_ACTIVE / 不支持，各自映射文案', async () => {
    mockCreate.mockRejectedValueOnce(new ScreenStreamApiError('DEVICE_OFFLINE', 'Runtime 电脑不在线'))
    let v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.end?.key).toBe('device_offline')
    expect(v.state.end?.title).toBe('Runtime 电脑不在线')

    mockCreate.mockRejectedValueOnce(new ScreenStreamApiError('SCREEN_SESSION_ACTIVE', '已有窗口正在查看这台电脑'))
    v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.end?.key).toBe('session_active')
    expect(v.state.end?.title).toBe('已有窗口正在查看这台电脑')

    mockCreate.mockRejectedValueOnce(new ScreenStreamApiError('SCREEN_STREAM_UNSUPPORTED', '当前 Runtime 版本不支持'))
    v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.end?.key).toBe('unsupported')
    expect(v.state.end?.retryable).toBe(false)
    expect(v.state.end?.title).toBe('当前 Runtime 版本不支持实时画面')
    expect(mockEnd).not.toHaveBeenCalled()
  })

  it('观看中连接失败 → Runtime 掉线文案；出画面前失败 → 无法直连（附放行提示）', async () => {
    mockPoll.mockResolvedValue(PENDING)
    let v = createViewer()
    v.start(DEVICE)
    await drive(0)
    FakePeerConnection.instances[0]!.fail()
    expect(v.state.end?.key).toBe('connect_failed')
    expect(v.state.end?.title).toBe('当前网络无法直连')
    expect(v.state.end?.hint).toContain('本地网络访问')

    // 观看中掉线
    mockPoll.mockReset()
    mockPoll.mockResolvedValue(PENDING)
    v = createViewer()
    v.start(DEVICE)
    await drive(0)
    FakePeerConnection.instances[1]!.ontrack?.({ track: fakeTrack })
    v.state.phase = 'watching'
    FakePeerConnection.instances[1]!.fail()
    expect(v.state.end?.key).toBe('runtime_offline')
    expect(v.state.end?.title).toBe('Runtime 连接已断开')
  })

  it('用户结束：立即补发 end、同步护栏连点只发一次、结束后不重复发送', async () => {
    mockPoll.mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    v.endByUser()
    v.endByUser()
    expect(mockEnd).toHaveBeenCalledExactlyOnceWith('dev-1', 'sess-1')
    expect(v.state.phase).toBe('ended')
    expect(v.state.end).toBe(null)

    // 已结束（Runtime 侧）再调 endByUser 不补发
    mockEnd.mockClear()
    v.endByUser()
    expect(mockEnd).not.toHaveBeenCalled()
  })

  it('取消发生在会话创建之前：会话创建成功后补发 end，不留悬挂会话', async () => {
    let resolveCreate: ((value: typeof CREATED) => void) | null = null
    mockCreate.mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve }))
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(mockCreate).toHaveBeenCalledTimes(1)
    v.endByUser()
    expect(mockEnd).not.toHaveBeenCalled()
    resolveCreate!({ ...CREATED })
    await drive(0)
    expect(mockEnd).toHaveBeenCalledExactlyOnceWith('dev-1', 'sess-1')
    expect(FakePeerConnection.instances).toHaveLength(0)
  })

  it('结束态的「重新查看」用同一台设备重开一场会话', async () => {
    mockPoll.mockResolvedValue(pendingSignals([{ seq: 1, type: 'end', payload: { reason: 'runtime_stopped' } }]))
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.end?.title).toBe(SCREEN_VIEWER_ENDS.runtime_stopped.title)
    mockPoll.mockResolvedValue(PENDING)
    v.restart()
    await drive(0)
    expect(mockCreate).toHaveBeenCalledTimes(2)
    expect(v.state.phase).toBe('connecting')
    expect(v.state.end).toBe(null)
    expect(v.state.deviceName).toBe('测试电脑')
  })

  it('进行中重复 start 被忽略（防重复提交）', async () => {
    mockPoll.mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    v.start(DEVICE)
    await drive(0)
    expect(mockCreate).toHaveBeenCalledExactlyOnceWith('dev-1')
  })
})

describe('锁屏、降画质与观看统计', () => {
  async function enterWatching(): Promise<ReturnType<typeof useScreenViewer>> {
    mockPoll
      .mockResolvedValueOnce(pendingSignals([{ seq: 1, type: 'prepared', payload: {} }]))
      .mockResolvedValueOnce(pendingSignals([{ seq: 2, type: 'offer', payload: { sdp: 'offer-sdp' } }]))
      .mockResolvedValueOnce(pendingSignals([{ seq: 3, type: 'status', payload: { state: 'streaming' } }]))
      .mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    await drive(600)
    await drive(600)
    expect(v.state.phase).toBe('watching')
    return v
  }

  it('locked / unlocked 与 degraded / restored 驱动覆盖提示与降画质横幅', async () => {
    const v = await enterWatching()
    mockPoll.mockResolvedValue(pendingSignals([
      { seq: 4, type: 'status', payload: { state: 'locked' } },
      { seq: 5, type: 'status', payload: { state: 'degraded' } },
    ]))
    // 观看中轮询间隔 5 秒 + 抖动
    await drive(WATCH_POLL_INTERVAL_MS + POLL_JITTER_MS)
    expect(v.state.locked).toBe(true)
    expect(v.state.degraded).toBe(true)
    mockPoll.mockResolvedValue(pendingSignals([
      { seq: 6, type: 'status', payload: { state: 'unlocked' } },
      { seq: 7, type: 'status', payload: { state: 'restored' } },
    ]))
    await drive(WATCH_POLL_INTERVAL_MS + POLL_JITTER_MS)
    expect(v.state.locked).toBe(false)
    expect(v.state.degraded).toBe(false)
  })

  it('线路与统计从 getStats 读出：提名对判线路、分辨率帧率码率与时长', async () => {
    const v = await enterWatching()
    const pc = FakePeerConnection.instances[0]!
    // 第一次统计：得到线路与分辨率；码率/帧率首帧无参照
    pc.statsQueue.push(watchingStatsReport())
    await drive(2_000)
    expect(v.state.path).toBe('lan')
    expect(v.state.stats.width).toBe(1280)
    expect(v.state.stats.height).toBe(720)
    expect(v.state.stats.bitrateBps).toBe(null)

    // 第二次统计：2 秒内收到 1_624_000 字节、30 帧 → 码率 6.496 Mbps、帧率 15
    pc.statsQueue.push(watchingStatsReport({ bytesReceived: 1_625_000, framesDecoded: 30, timestamp: 2_000 }))
    const before = v.state.stats.durationMs
    await drive(2_000)
    expect(v.state.stats.fps).toBe(15)
    expect(v.state.stats.bitrateBps).toBe((1_624_000 * 8) / 2)
    expect(v.state.stats.durationMs).toBeGreaterThanOrEqual(before)
  })

  it('status streaming 带线路时直接采用，无需等统计', async () => {
    mockPoll
      .mockResolvedValueOnce(pendingSignals([{ seq: 1, type: 'prepared', payload: {} }]))
      .mockResolvedValueOnce(pendingSignals([{ seq: 2, type: 'status', payload: { state: 'streaming', path: 'p2p' } }]))
      .mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    await drive(600)
    expect(v.state.phase).toBe('watching')
    expect(v.state.path).toBe('p2p')
  })
})

describe('「本地网络访问」权限提示', () => {
  it('权限处于 prompt：建连前显示说明，照常创建会话', async () => {
    const query = vi.fn(async ({ name }: { name: string }) => {
      if (name === 'local-network-access') throw new TypeError('unknown permission')
      return { state: 'prompt' } as PermissionStatus
    })
    vi.stubGlobal('navigator', { permissions: { query } })
    mockPoll.mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(query).toHaveBeenCalledWith({ name: 'local-network-access' })
    expect(query).toHaveBeenCalledWith({ name: 'local-network' })
    expect(v.state.localNetworkPrompt).toBe(true)
    expect(mockCreate).toHaveBeenCalledTimes(1)
  })

  it('权限已允许（granted）：不显示说明', async () => {
    const query = vi.fn(async () => ({ state: 'granted' }) as PermissionStatus)
    vi.stubGlobal('navigator', { permissions: { query } })
    mockPoll.mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.localNetworkPrompt).toBe(false)
    expect(mockCreate).toHaveBeenCalledTimes(1)
  })

  it('权限 API 不可用（jsdom 无 navigator.permissions）：不显示说明，不挡建连', async () => {
    mockPoll.mockResolvedValue(PENDING)
    const v = createViewer()
    v.start(DEVICE)
    await drive(0)
    expect(v.state.localNetworkPrompt).toBe(false)
    expect(mockCreate).toHaveBeenCalledTimes(1)
  })
})

describe('结束态文案表', () => {
  it('十三个状态逐条有标题与说明（设计 10）', () => {
    const keys = [
      'same_machine', 'machine_check_unknown', 'connect_failed', 'device_offline', 'session_active',
      'runtime_stopped', 'runtime_rejected', 'runtime_offline', 'renewal_timeout', 'resource_busy', 'failed', 'unsupported',
    ] as const
    for (const key of keys) {
      const entry = SCREEN_VIEWER_ENDS[key]
      expect(entry.title.length).toBeGreaterThan(0)
      expect(entry.description.length).toBeGreaterThan(0)
    }
    expect(SCREEN_VIEWER_ENDS.machine_check_unknown.title).toBe('无法确认是否为同一台电脑')
    expect(SCREEN_VIEWER_ENDS.same_machine.title).toBe('同一台电脑无法使用此功能')
    expect(SCREEN_VIEWER_ENDS.connect_failed.hint).toContain('本地网络访问')
    expect(SCREEN_VIEWER_ENDS.resource_busy.description).toContain('降低画质')
    expect(SCREEN_VIEWER_ENDS.unsupported.retryable).toBe(false)
  })
})

describe('状态初始值', () => {
  it('新建控制器处于 connecting / 第一步 / 无结束态（供弹框挂载前渲染）', () => {
    const v = createViewer()
    expect((v.state as ScreenViewerState).phase).toBe('connecting')
    expect(v.state.step).toBe(1)
    expect(v.state.end).toBe(null)
  })
})
