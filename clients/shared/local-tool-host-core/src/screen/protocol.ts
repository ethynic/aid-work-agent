/**
 * 桌面实时画面的协议常量与心跳字段解析（设计 docs/system/runtime-screen-stream-design.md 第 5 节）。
 *
 * 取值与服务器实现逐字对齐（src/local_tools/screen_stream.py）。服务器对不认识的取值一律拒绝，
 * 例如 Runtime 只能报告七个结束原因里的一个，所以这里既当文档，也供运行时核对。
 *
 * 心跳响应里的画面字段是可选的：服务端查询画面会话出错或超时时，心跳照样成功，只是不带
 * `screen_session`（设计 4.4）。所以解析的唯一规则是「字段缺失或类型不对一律按没有这次通知处理」，
 * 唯一的后果是这一次不给画面续期；解析函数不抛异常，也不参与心跳成败的判定。
 */

export const SCREEN_STREAM_CAPABILITY = 'screen_stream_v1'
export const SCREEN_STREAM_VERSION = 1

export const SCREEN_SESSION_PATH = '/api/local-tools/runtime/screen/session'
export const SCREEN_SIGNAL_PATH = '/api/local-tools/runtime/screen/signal'

/** 服务器记录的会话状态 */
export const SCREEN_SERVER_STATES = ['pending', 'prepared', 'connecting', 'verifying', 'streaming', 'ended'] as const
export type ScreenServerState = (typeof SCREEN_SERVER_STATES)[number]

/** 心跳响应的 screen_session.state 只会出现这三种：待应答、传画面中（即续期）、已结束 */
export const SCREEN_NOTICE_STATES = ['pending', 'streaming', 'ended'] as const
export type ScreenNoticeState = (typeof SCREEN_NOTICE_STATES)[number]

/** status 信令的 state */
export const SCREEN_STATUS_STATES = ['connected', 'streaming', 'locked', 'unlocked', 'degraded', 'restored'] as const
export type ScreenStatusState = (typeof SCREEN_STATUS_STATES)[number]

/** 实际走的线路 */
export const SCREEN_PATHS = ['lan', 'p2p'] as const
export type ScreenPath = (typeof SCREEN_PATHS)[number]

export const RUNTIME_SIGNAL_TYPES = ['prepared', 'offer', 'candidate', 'status', 'end'] as const
export type RuntimeSignalType = (typeof RUNTIME_SIGNAL_TYPES)[number]

export const WEB_SIGNAL_TYPES = ['start', 'answer', 'candidate', 'end'] as const
export type WebSignalType = (typeof WEB_SIGNAL_TYPES)[number]

/** Runtime 可以报告的结束原因。其余三个（viewer_closed、device_offline、runtime_offline）只由服务器判定 */
export const RUNTIME_END_REASONS = [
  'runtime_stopped', 'runtime_rejected', 'same_machine', 'machine_check_unknown',
  'connect_failed', 'renewal_timeout', 'resource_busy',
] as const
export type RuntimeEndReason = (typeof RUNTIME_END_REASONS)[number]

export type PortProtocol = 'udp' | 'tcp'
/** 连接建立后，采集窗口从连接统计里取到的对端候选端口。统计不给对端地址，只给端口 */
export interface PeerPort {
  port: number
  protocol: PortProtocol
}

// ---- 计时（毫秒）----

/** 建连超时：从收到网页的 start 起算。没有可用地址时 ICE 自己不会报失败，必须由会话自己计时 */
export const CONNECT_TIMEOUT_MS = 30_000
/** 验证超时：从连接建立起算。服务器在 verifying 状态只等 15 秒 */
export const VERIFY_TIMEOUT_MS = 15_000
/** 画面租约：传画面期间连续这么久没有从心跳拿到续期就自行关闭采集 */
export const LEASE_TIMEOUT_MS = 30_000
/** 建连阶段的轮询间隔：从收到上一次响应时起算。加抖动是因为服务端的占用闸不排队，
 *  固定节拍的同步轮询会让排在后面的请求一直拿不到名额 */
export const POLL_INTERVAL_MS = 500
export const POLL_JITTER_MS = 100
export const RESOURCE_SAMPLE_MS = 5_000
export const LOCK_CHECK_MS = 2_000
/** 同机判定前等名字解析和公网映射地址到齐的时限 */
export const EVIDENCE_WAIT_MS = 1_500
/** 地址候选分不出同机时，等采集窗口报来对端端口的时限 */
export const PEER_PORT_WAIT_MS = 1_500
/** 内网类候选的暂存时限：配置了 STUN 时等比较两端公网出口（设计 4.1），到点还比不出来就照发，不挡建连 */
export const CANDIDATE_HOLD_MS = 3_000
/** 单个 mDNS 名字的解析超时。系统解析器对 mDNS 名字可能很久才报失败 */
export const HOST_LOOKUP_TIMEOUT_MS = 2_000
/** 信令遇到繁忙或网络错误时的重试间隔与次数上限 */
export const SIGNAL_RETRY_DELAY_MS = 500
export const SIGNAL_MAX_ATTEMPTS = 20
/** 传画面中超过这么多秒没有续期，管理界面提示「已 N 秒没有收到服务器确认」 */
export const RENEWAL_LATE_SECONDS = 10

// ---- 上限（只作记录，本轮不做计数门禁）----

/** 每个会话的信令条数上限，两个方向合计；end 不占存储 */
export const MAX_SIGNALS_PER_SESSION = 128
/** 单条信令 payload 的字节上限 */
export const MAX_PAYLOAD_BYTES = 16 * 1024

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** value 是否是 values 之一；用于核对服务器发来的字面量 */
export function isOneOf<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === 'string' && (values as readonly string[]).includes(value)
}

export function isRuntimeEndReason(value: unknown): value is RuntimeEndReason {
  return isOneOf(RUNTIME_END_REASONS, value)
}

/** 心跳响应里的会话通知 */
export interface ScreenSessionNotice {
  id: string
  state: ScreenNoticeState
}

/** 服务端是否支持实时画面：响应里带 `screen_stream: {version: 1}` 才算 */
export function serverSupportsScreenStream(response: unknown): boolean {
  const support = isRecord(response) ? response.screen_stream : null
  return isRecord(support) && support.version === SCREEN_STREAM_VERSION
}

/** 读心跳响应里的 screen_session；缺失或类型不对返回 null */
export function parseHeartbeatScreenSession(response: unknown): ScreenSessionNotice | null {
  const notice = isRecord(response) ? response.screen_session : null
  if (!isRecord(notice) || typeof notice.id !== 'string') return null
  if (!isOneOf(SCREEN_NOTICE_STATES, notice.state)) return null
  return { id: notice.id, state: notice.state }
}
