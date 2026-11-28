/**
 * 实时画面观看的连接组合函数（设计 runtime-screen-stream-design.md 5.4、6.3、10）。
 *
 * 独立于组件，便于单测。职责：创建会话、按节奏轮询信令、驱动 RTCPeerConnection、
 * 把 Runtime 的状态信令映射为弹框可渲染的状态；结束路径统一收口。
 *
 * 关键顺序约束（设计 5.4 / D17）：
 * - 未收到 Runtime 的 prepared 信令前不发 start（提前发会让会话在建连阶段等不到 Runtime）；
 * - 不预先添加通道，直接用 ontrack 给的轨道（预加通道协商时不会被复用，画面出不来）；
 * - 远端描述设置之前到达的 candidate 先缓存，设置完再按顺序加入。
 *
 * 轮询节奏：未出画面 500ms 一次、出画面后 5 秒一次（兼作存活证明），都从收到上一次
 * 响应时起算并加 100ms 内随机抖动（服务端占用闸不排队，固定节拍会同步成批）。
 * 503 SCREEN_STREAM_BUSY 与网络错误稍后重试、不结束会话；409/404 按结束处理。
 */

import { reactive, shallowRef } from 'vue'
import {
  createScreenSession,
  pollScreenSession,
  sendScreenSignal,
  endScreenSession,
  ScreenStreamApiError,
  type ScreenSessionCreated,
  type ScreenSessionSignals,
  type ScreenSignal,
  type ViewerSignalType,
} from '@/api/screenStream'
import {
  inboundVideoDelta,
  inboundVideoStats,
  pathFromStats,
  type InboundVideoSample,
  type ScreenPath,
} from './screenViewerStats'

// ---- 节奏与上限（与服务端 screen_stream.py、Runtime protocol.ts 的口径一致）----

/** 出画面前的轮询间隔（毫秒） */
export const PREPARED_POLL_INTERVAL_MS = 500
/** 出画面后的轮询间隔（毫秒），兼作存活证明 */
export const WATCH_POLL_INTERVAL_MS = 5000
/** 轮询抖动上限（毫秒） */
export const POLL_JITTER_MS = 100
/** 503 或网络错误后的重试等待（毫秒），不结束会话 */
export const RETRY_POLL_DELAY_MS = 500
/** 信令重试间隔与次数（answer/start 关键路径对齐 Runtime 的 20 次） */
export const SIGNAL_RETRY_DELAY_MS = 500
export const SIGNAL_MAX_ATTEMPTS = 20
/** 地址候选允许的重试次数：丢失可容忍（ICE 有其余候选），不值得长等 */
export const CANDIDATE_MAX_ATTEMPTS = 3
/** 观看统计刷新间隔（毫秒） */
export const STATS_INTERVAL_MS = 2000

/** 观看统计面板刷新的间隔下限提示：与服务端信令上限无关联，仅用于展示节奏 */
export const STATS_REFRESH_HINT_MS = STATS_INTERVAL_MS

/** 浏览器「本地网络访问」权限的候选名称（Chrome 157、Edge 156 认 local-network-access 及其拆分名） */
const LOCAL_NETWORK_PERMISSION_NAMES = ['local-network-access', 'local-network']

/** 连接三步的标签与一句说明（设计 10：建立直连时说明这一步还没有画面） */
export const SCREEN_VIEWER_STEPS = [
  { label: '唤醒 Runtime', hint: '对方电脑靠心跳唤醒，通常几秒内应答' },
  { label: '建立直连', hint: '正在建立点对点直连，这一步还没有画面' },
  { label: '确认观看设备', hint: '已连上对方电脑，正在确认观看设备' },
] as const

/** 结束态的展示键（设计 10 网页端文案表；session_active / unsupported 来自创建会话的错误码） */
export type ScreenViewerEndKey =
  | 'same_machine'
  | 'machine_check_unknown'
  | 'connect_failed'
  | 'device_offline'
  | 'session_active'
  | 'runtime_stopped'
  | 'runtime_rejected'
  | 'runtime_offline'
  | 'renewal_timeout'
  | 'resource_busy'
  | 'viewer_closed'
  | 'unsupported'
  | 'failed'

export interface ScreenViewerEnd {
  key: ScreenViewerEndKey
  title: string
  description: string
  /** 附加提示（如本地网络放行说明） */
  hint?: string
  retryable: boolean
}

/** 结束态文案表（设计 10，逐条对应） */
export const SCREEN_VIEWER_ENDS: Record<Exclude<ScreenViewerEndKey, 'viewer_closed'>, ScreenViewerEnd> = {
  same_machine: {
    key: 'same_machine',
    title: '同一台电脑无法使用此功能',
    description: '请在另一台电脑或手机上打开本页面后重试。',
    retryable: true,
  },
  machine_check_unknown: {
    key: 'machine_check_unknown',
    title: '无法确认是否为同一台电脑',
    description: '出于安全考虑没有开始采集画面，可以换一台设备或换个浏览器再试。',
    retryable: true,
  },
  connect_failed: {
    key: 'connect_failed',
    title: '当前网络无法直连',
    description: '实时画面只在两台电脑之间点对点直连，不经过服务器；当前网络打不通。',
    hint: '若浏览器弹出「本地网络访问」提示，请选择允许；未放行时同一网络内通常也无法直连。',
    retryable: true,
  },
  device_offline: {
    key: 'device_offline',
    title: 'Runtime 电脑不在线',
    description: '对方电脑需要开机、未锁屏并保持 Runtime 在线。',
    retryable: true,
  },
  session_active: {
    key: 'session_active',
    title: '已有窗口正在查看这台电脑',
    description: '一台电脑同一时间只允许一路画面，请先关闭其他正在查看的窗口。',
    retryable: true,
  },
  runtime_stopped: {
    key: 'runtime_stopped',
    title: '对方已停止画面共享',
    description: '对方电脑上的用户停止了画面共享，可以重新查看。',
    retryable: true,
  },
  runtime_rejected: {
    key: 'runtime_rejected',
    title: '对方拒绝了本次查看',
    description: '对方电脑上的用户拒绝了本次查看请求，可以重新查看。',
    retryable: true,
  },
  runtime_offline: {
    key: 'runtime_offline',
    title: 'Runtime 连接已断开',
    description: '对方电脑的 Runtime 中途掉线，画面中断，可以重新查看。',
    retryable: true,
  },
  renewal_timeout: {
    key: 'renewal_timeout',
    title: '画面已自动停止',
    description: '对方电脑连续 30 秒没有收到服务器确认，按规则自动停止了画面共享；停的只是画面，不会去动这台电脑上的任务。',
    retryable: true,
  },
  resource_busy: {
    key: 'resource_busy',
    title: '画面已停止',
    description: '对方电脑正在执行任务，资源不足，降低画质后仍然不够，已优先保证任务；任务本身不受影响，结束后可以重新查看。',
    retryable: true,
  },
  unsupported: {
    key: 'unsupported',
    title: '当前 Runtime 版本不支持实时画面',
    description: '请升级对方电脑的 Runtime 后再试。',
    retryable: false,
  },
  failed: {
    key: 'failed',
    title: '画面连接已结束',
    description: '实时画面因未知原因结束，可以重新查看。',
    retryable: true,
  },
}

/** Runtime / 服务端的结束原因 → 展示键；unknown 与空值落到 failed */
function endKeyFromReason(reason: string | null | undefined): ScreenViewerEndKey {
  switch (reason) {
    case 'viewer_closed':
    case 'runtime_stopped':
    case 'runtime_rejected':
    case 'same_machine':
    case 'machine_check_unknown':
    case 'connect_failed':
    case 'renewal_timeout':
    case 'resource_busy':
    case 'device_offline':
    case 'runtime_offline':
      return reason
    default:
      return 'failed'
  }
}

/** 创建会话的错误码 → 展示键 */
function endKeyFromCreateError(error: unknown): ScreenViewerEndKey {
  if (error instanceof ScreenStreamApiError) {
    if (error.code === 'DEVICE_OFFLINE') return 'device_offline'
    if (error.code === 'SCREEN_SESSION_ACTIVE') return 'session_active'
    if (error.code === 'SCREEN_STREAM_UNSUPPORTED') return 'unsupported'
  }
  return 'failed'
}

export interface ScreenViewerDevice {
  device_id: string
  name?: string | null
}

export interface ScreenViewerStats {
  durationMs: number
  width: number | null
  height: number | null
  fps: number | null
  bitrateBps: number | null
}

export interface ScreenViewerState {
  phase: 'connecting' | 'watching' | 'ended'
  /** 连接三步的当前步（1 起算）；仅 connecting 阶段有意义 */
  step: 1 | 2 | 3
  deviceName: string
  /** 实际线路（局域网直连 / 点对点直连），来自 status streaming 与 getStats */
  path: ScreenPath | null
  /** 目标电脑锁屏：画面暂停，覆盖提示，解锁后自动恢复 */
  locked: boolean
  /** 任务运行中资源不足、已降低画质：画面左上角提示 */
  degraded: boolean
  /** 建连前浏览器「本地网络访问」权限处于 prompt：先显示一句说明 */
  localNetworkPrompt: boolean
  stats: ScreenViewerStats
  end: ScreenViewerEnd | null
}

/** 组合函数依赖的接口，测试可整体替换 */
export interface ScreenViewerApi {
  createSession: typeof createScreenSession
  pollSession: typeof pollScreenSession
  sendSignal: typeof sendScreenSignal
  endSession: typeof endScreenSession
}

export interface ScreenViewerDeps {
  api?: ScreenViewerApi
  /** 抖动随机源，测试注入固定值 */
  random?: () => number
}

interface RemoteCandidateInit {
  candidate: string
  sdpMid: string | null
  sdpMLineIndex: number | null
}

const defaultApi: ScreenViewerApi = { createSession: createScreenSession, pollSession: pollScreenSession, sendSignal: sendScreenSignal, endSession: endScreenSession }

/** 延迟（可被假计时器控制） */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 浏览器「本地网络访问」权限是否处于 prompt（不支持的浏览器返回 false，不挡建连） */
export async function isLocalNetworkPermissionPrompt(): Promise<boolean> {
  try {
    const permissions = (navigator as Navigator & { permissions?: Permissions }).permissions
    if (!permissions?.query) return false
    for (const name of LOCAL_NETWORK_PERMISSION_NAMES) {
      try {
        const status = await permissions.query({ name } as unknown as PermissionDescriptor)
        if (status?.state === 'prompt') return true
        if (status?.state) return false
      } catch {
        // 当前浏览器不认识这个权限名，尝试下一个
      }
    }
  } catch {
    // Permissions API 不可用：按无提示处理
  }
  return false
}

/**
 * 创建观看控制器。用法：
 *   const viewer = useScreenViewer()
 *   viewer.start(device)      // 打开弹框时
 *   viewer.endByUser()        // 关闭弹框 / Esc / 结束按钮（立即补发 end，连点只发一次）
 *   viewer.restart()          // 结束态的「重新查看」
 */
export function useScreenViewer(deps: ScreenViewerDeps = {}) {
  const api = deps.api ?? defaultApi
  const random = deps.random ?? Math.random

  const state = reactive<ScreenViewerState>({
    phase: 'connecting',
    step: 1,
    deviceName: '',
    path: null,
    locked: false,
    degraded: false,
    localNetworkPrompt: false,
    stats: { durationMs: 0, width: null, height: null, fps: null, bitrateBps: null },
    end: null,
  })
  /** ontrack 给出的轨道与包装出的媒体流（D17：不预加通道，直接用轨道） */
  const track = shallowRef<MediaStreamTrack | null>(null)
  const stream = shallowRef<MediaStream | null>(null)

  let device: ScreenViewerDevice | null = null
  let session: { deviceId: string; sessionId: string } | null = null
  let peer: RTCPeerConnection | null = null
  /** 无活动会话时为 true：所有异步回调用它做统一护栏 */
  let finished = true
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let statsTimer: ReturnType<typeof setInterval> | null = null
  let pollIntervalMs = PREPARED_POLL_INTERVAL_MS
  let seq = 0
  let startSent = false
  let remoteReady = false
  let streamingStartedAt = 0
  let lastEndReason: string | null = null
  let lastSample: InboundVideoSample | null = null
  const earlyCandidates: RemoteCandidateInit[] = []

  // ==================== 本地清理 ====================

  function clearTimers(): void {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
    if (statsTimer) { clearInterval(statsTimer); statsTimer = null }
  }

  function closePeer(): void {
    const mine = peer
    peer = null
    if (mine) {
      try { mine.close() } catch { /* 已关闭 */ }
    }
  }

  function resetView(): void {
    state.phase = 'connecting'
    state.step = 1
    state.path = null
    state.locked = false
    state.degraded = false
    state.localNetworkPrompt = false
    state.stats = { durationMs: 0, width: null, height: null, fps: null, bitrateBps: null }
    state.end = null
    track.value = null
    stream.value = null
  }

  // ==================== 结束路径 ====================

  function buildEnd(key: ScreenViewerEndKey, descriptionOverride?: string): ScreenViewerEnd | null {
    if (key === 'viewer_closed') return null
    const base = SCREEN_VIEWER_ENDS[key]
    return descriptionOverride ? { ...base, description: descriptionOverride } : base
  }

  /** 收尾并展示结束态；reason 为空或 viewer_closed 时不展示（用户主动结束由弹框直接关闭） */
  function finishWithKey(key: ScreenViewerEndKey, descriptionOverride?: string): void {
    if (finished) return
    finished = true
    session = null
    clearTimers()
    closePeer()
    state.phase = 'ended'
    state.end = buildEnd(key, descriptionOverride)
  }

  /** 按 Runtime / 服务端的结束原因收尾 */
  function finishWithReason(reason: string | null | undefined): void {
    if (finished) return
    const key = endKeyFromReason(reason ?? lastEndReason)
    finishWithKey(key)
  }

  /**
   * 用户主动结束（关闭弹框、Esc、结束按钮）：立即补发 end 再收尾。
   * 同步置位防重复，连点只发一次；结束幂等且不造成损失，不弹确认。
   */
  function endByUser(): void {
    if (finished) return
    finished = true
    const target = session
    session = null
    clearTimers()
    closePeer()
    state.phase = 'ended'
    state.end = null
    if (target) {
      void api.endSession(target.deviceId, target.sessionId).catch(() => { /* 结束失败不影响本地收尾；会话由服务端超时收敛 */ })
    }
  }

  // ==================== 信令发送 ====================

  /** 发送网页信令；会话已结束时按缓存原因收尾，繁忙/网络错误稍后重试（不结束会话） */
  async function sendSignalWithRetry(type: ViewerSignalType, payload: Record<string, any>, maxAttempts: number): Promise<void> {
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      if (finished || !session) return
      try {
        await api.sendSignal(session.deviceId, session.sessionId, type, payload)
        return
      } catch (error) {
        if (finished) return
        if (error instanceof ScreenStreamApiError
          && (error.code === 'SCREEN_SESSION_ENDED' || error.code === 'SCREEN_SESSION_NOT_FOUND')) {
          finishWithReason(lastEndReason)
          return
        }
        if (attempt < maxAttempts) await delay(SIGNAL_RETRY_DELAY_MS)
      }
    }
    // 重试用尽：不结束会话；answer/start 发不出去时，会话由服务端超时收敛，轮询随后可见
  }

  function sendLocalCandidate(candidate: RTCIceCandidate): void {
    const json = typeof candidate.toJSON === 'function'
      ? candidate.toJSON()
      : { candidate: candidate.candidate, sdpMid: candidate.sdpMid, sdpMLineIndex: candidate.sdpMLineIndex }
    void sendSignalWithRetry('candidate', {
      candidate: json.candidate ?? '',
      sdp_mid: json.sdpMid ?? null,
      sdp_mline_index: typeof json.sdpMLineIndex === 'number' ? json.sdpMLineIndex : null,
    }, CANDIDATE_MAX_ATTEMPTS)
  }

  // ==================== WebRTC ====================

  function attachTrack(incoming: MediaStreamTrack): void {
    if (finished) return
    track.value = incoming
    try {
      stream.value = new MediaStream([incoming])
    } catch {
      // MediaStream 不可用（测试环境）：组件改用 track 兜底
      stream.value = null
    }
  }

  function onConnectionStateChange(mine: RTCPeerConnection): void {
    if (finished || peer !== mine) return
    const connectionState = mine.connectionState
    if (connectionState === 'failed' || connectionState === 'closed') {
      // 观看中掉线 → Runtime 掉线；出画面前失败 → 无法直连。disconnected 先等恢复或转 failed
      finishWithReason(lastEndReason ?? (state.phase === 'watching' ? 'runtime_offline' : 'connect_failed'))
    }
  }

  async function addRemoteCandidate(mine: RTCPeerConnection, item: RemoteCandidateInit): Promise<void> {
    try {
      await mine.addIceCandidate({ candidate: item.candidate, sdpMid: item.sdpMid, sdpMLineIndex: item.sdpMLineIndex })
    } catch {
      // 单条候选失败可容忍，ICE 还有其余候选
    }
  }

  async function handleOffer(payload: Record<string, any>): Promise<void> {
    const mine = peer
    const sdp = payload?.sdp
    if (!mine || typeof sdp !== 'string' || remoteReady) return
    try {
      remoteReady = false
      await mine.setRemoteDescription({ type: 'offer', sdp })
      remoteReady = true
      // 早到的候选：远端描述就绪后按到达顺序加入
      const pending = earlyCandidates.splice(0)
      for (const item of pending) await addRemoteCandidate(mine, item)
      const answer = await mine.createAnswer()
      await mine.setLocalDescription(answer)
      const description = mine.currentLocalDescription ?? mine.localDescription
      void sendSignalWithRetry('answer', { sdp: description?.sdp ?? answer.sdp }, SIGNAL_MAX_ATTEMPTS)
    } catch {
      // 协商失败：不重复结束，交由会话超时收敛（connecting 30 秒），轮询随后可见
    }
  }

  function handleRemoteCandidate(payload: Record<string, any>): void {
    const mine = peer
    const candidate = payload?.candidate
    if (!mine || typeof candidate !== 'string' || !candidate) return
    const item: RemoteCandidateInit = {
      candidate,
      sdpMid: typeof payload.sdp_mid === 'string' ? payload.sdp_mid : null,
      sdpMLineIndex: typeof payload.sdp_mline_index === 'number' ? payload.sdp_mline_index : null,
    }
    if (!remoteReady) {
      // 收到 offer 之前到达的候选不可加入：先缓存
      earlyCandidates.push(item)
      return
    }
    void addRemoteCandidate(mine, item)
  }

  function enterWatching(path: string | null | undefined): void {
    if (state.phase === 'watching') {
      if (path === 'lan' || path === 'p2p') state.path = path
      return
    }
    state.phase = 'watching'
    if (path === 'lan' || path === 'p2p') state.path = path
    state.locked = false
    state.degraded = false
    streamingStartedAt = Date.now()
    pollIntervalMs = WATCH_POLL_INTERVAL_MS
    lastSample = null
    if (!statsTimer) {
      statsTimer = setInterval(() => void refreshStats(), STATS_INTERVAL_MS)
    }
  }

  function handleStatus(payload: Record<string, any>): void {
    const statusState = payload?.state
    switch (statusState) {
      case 'connected':
        // 不含画面的连接已建立：进入第三步「确认观看设备」
        if (state.phase === 'connecting') state.step = 3
        return
      case 'streaming':
        enterWatching(payload?.path)
        return
      case 'locked':
        state.locked = true
        return
      case 'unlocked':
        state.locked = false
        return
      case 'degraded':
        state.degraded = true
        return
      case 'restored':
        state.degraded = false
        return
      default:
        // 服务端只保存合法取值；未知取值防御式忽略
        return
    }
  }

  // ==================== 观看统计 ====================

  async function refreshStats(): Promise<void> {
    const mine = peer
    if (!mine || finished || state.phase !== 'watching') return
    try {
      const report = await mine.getStats()
      if (finished) return
      if (!state.path) {
        const path = pathFromStats(report)
        if (path) state.path = path
      }
      const sample = inboundVideoStats(report)
      if (sample) {
        const delta = inboundVideoDelta(lastSample, sample)
        lastSample = sample
        if (delta.fps !== null) state.stats.fps = delta.fps
        if (delta.bitrateBps !== null) state.stats.bitrateBps = delta.bitrateBps
        if (sample.width) state.stats.width = sample.width
        if (sample.height) state.stats.height = sample.height
      }
      state.stats.durationMs = Date.now() - streamingStartedAt
    } catch {
      // 统计读取失败忽略，下一轮再取
    }
  }

  // ==================== 信令轮询 ====================

  function schedulePoll(delayMs?: number): void {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
    if (finished || !session) return
    const wait = delayMs ?? (pollIntervalMs + random() * POLL_JITTER_MS)
    pollTimer = setTimeout(() => void pollOnce(), wait)
  }

  async function pollOnce(): Promise<void> {
    if (finished || !session) return
    let result: ScreenSessionSignals
    try {
      result = await api.pollSession(session.deviceId, session.sessionId, seq)
    } catch (error) {
      if (finished) return
      // 会话已结束或不存在：结束本地观看；503 / 网络错误：稍后重试，不结束会话
      if (error instanceof ScreenStreamApiError
        && (error.code === 'SCREEN_SESSION_NOT_FOUND' || error.code === 'SCREEN_SESSION_ENDED')) {
        finishWithReason(lastEndReason)
        return
      }
      schedulePoll(RETRY_POLL_DELAY_MS)
      return
    }
    if (finished || !session) return
    const summary = result.session
    if (summary.end_reason) lastEndReason = summary.end_reason
    if (summary.state === 'ended') {
      finishWithReason(summary.end_reason)
      return
    }
    for (const signal of result.signals) {
      if (signal.seq > seq) seq = signal.seq
      await handleSignal(signal)
      if (finished) return
    }
    // 下一次轮询从收到本响应时起算 + 抖动
    schedulePoll()
  }

  async function handleSignal(signal: ScreenSignal): Promise<void> {
    const payload = signal.payload && typeof signal.payload === 'object' ? signal.payload : {}
    switch (signal.type) {
      case 'prepared':
        // 门禁：收到 prepared 才发 start；提前发会让会话在建连阶段等不到 Runtime
        if (!startSent) {
          startSent = true
          state.step = 2
          void sendSignalWithRetry('start', {}, SIGNAL_MAX_ATTEMPTS)
        }
        return
      case 'offer':
        await handleOffer(payload)
        return
      case 'candidate':
        handleRemoteCandidate(payload)
        return
      case 'status':
        handleStatus(payload)
        return
      case 'end':
        finishWithReason(typeof payload?.reason === 'string' ? payload.reason : null)
        return
      default:
        return
    }
  }

  // ==================== 启动 ====================

  function openPeerConnection(stunUrls: string[]): boolean {
    try {
      const mine = new RTCPeerConnection({
        iceServers: stunUrls.map((urls) => ({ urls })),
        bundlePolicy: 'max-bundle',
      })
      peer = mine
      mine.ontrack = (event) => attachTrack(event.track)
      mine.onicecandidate = (event) => { if (event.candidate) sendLocalCandidate(event.candidate) }
      mine.onconnectionstatechange = () => onConnectionStateChange(mine)
      return true
    } catch {
      // 浏览器不支持 WebRTC：按无法直连收尾
      finishWithKey('connect_failed')
      return false
    }
  }

  async function run(target: ScreenViewerDevice): Promise<void> {
    // 建连前查询「本地网络访问」权限：处于 prompt 时先给一句说明，不让弹窗凭空出现（设计 6.3）
    state.localNetworkPrompt = await isLocalNetworkPermissionPrompt()
    let created: ScreenSessionCreated
    try {
      created = await api.createSession(target.device_id)
    } catch (error) {
      if (finished) return
      const key = endKeyFromCreateError(error)
      const message = error instanceof Error && error.message ? error.message : undefined
      finishWithKey(key, key === 'failed' ? message : undefined)
      return
    }
    if (finished) {
      // 取消发生在会话创建之后：补发 end，不留悬挂会话
      void api.endSession(target.device_id, created.sessionId).catch(() => { /* 会话由服务端超时收敛 */ })
      return
    }
    session = { deviceId: target.device_id, sessionId: created.sessionId }
    seq = 0
    startSent = false
    remoteReady = false
    lastEndReason = null
    pollIntervalMs = PREPARED_POLL_INTERVAL_MS
    if (!openPeerConnection(created.stunUrls)) return
    schedulePoll(0)
  }

  /** 开始观看一场新的会话；进行中重复调用直接忽略（防重复提交） */
  function start(target: ScreenViewerDevice): void {
    if (!finished) return
    clearTimers()
    closePeer()
    earlyCandidates.length = 0
    device = target
    resetView()
    finished = false
    state.deviceName = target.name || '未命名设备'
    void run(target)
  }

  /** 结束态的「重新查看」：用同一台设备重开一场会话 */
  function restart(): void {
    if (!finished || !device) return
    start(device)
  }

  /** 组件卸载时调用：仍在观看则补发 end */
  function dispose(): void {
    endByUser()
    device = null
  }

  return { state, track, stream, start, endByUser, restart, dispose }
}

export type ScreenViewer = ReturnType<typeof useScreenViewer>
