/**
 * 设备侧的两个画面接口（设计 5.3）：取会话与网页信令、追加一条 Runtime 信令。
 *
 * HTTP 走注入的传输层。Host 用现有的 ApiClient 实现它：503 且 code 为 SCREEN_STREAM_BUSY 或网络错误
 * 归为 busy / network，409 SCREEN_SESSION_ENDED 归为 ended，404 归为 not_found，其余 4xx/5xx 归为 rejected。
 *
 * Runtime 发往网页的信令严格按调用顺序逐条发，上一条请求结束（成功或放弃）之后才开始下一条。
 * 服务器只按到达顺序编号，不保证发送顺序：验证程序实测并发发送时地址候选会先于连接请求到达，
 * 网页因为没有远端描述而拒收（2026-11-27）。
 *
 * 这个模块的对外方法不向调用方抛异常，一律用结果对象返回。
 */
import {
  SCREEN_SERVER_STATES, SCREEN_SIGNAL_PATH, SCREEN_SESSION_PATH, SIGNAL_MAX_ATTEMPTS, SIGNAL_RETRY_DELAY_MS,
  isOneOf, isRecord, type ScreenServerState,
} from './protocol.js'

export type ScreenTransportErrorKind = 'busy' | 'ended' | 'not_found' | 'rejected' | 'network'

export class ScreenTransportError extends Error {
  constructor(readonly kind: ScreenTransportErrorKind, message: string = `画面接口请求失败：${kind}`) {
    super(message)
    this.name = 'ScreenTransportError'
  }
}

export interface ScreenTransport {
  /** path 是 '/api/local-tools/runtime/screen/session' 或 '/api/local-tools/runtime/screen/signal' */
  post(path: string, body: unknown): Promise<unknown>
}

/** 读到的一条信令。dir 是发信方向（web / rt）；服务器的读取接口不带这个字段时为 null */
export interface ScreenSignal {
  seq: number
  dir: string | null
  type: string
  payload: unknown
}

export interface ScreenSessionView {
  id: string
  state: ScreenServerState
  endReason: string | null
  /** 服务端给的 STUN 地址（只含 stun: / stuns:）；响应里没带时为 null */
  stunUrls: string[] | null
}

export interface ScreenSessionRead {
  session: ScreenSessionView
  signals: ScreenSignal[]
}

export type SignalFailure = ScreenTransportErrorKind | 'abandoned'

export type SignalPostResult =
  | { ok: true; attempts: number }
  /** kind 为 ended / not_found 表示服务器已经结束这个会话；abandoned 表示 abandon() 之后没再发 */
  | { ok: false; kind: SignalFailure; attempts: number }

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    timer.unref()
  })
}

function parseSessionRead(body: unknown, afterSeq: number): ScreenSessionRead | null {
  if (!isRecord(body) || !isRecord(body.session)) return null
  const { id, state } = body.session
  if (typeof id !== 'string' || !isOneOf(SCREEN_SERVER_STATES, state) || !Array.isArray(body.signals)) return null
  // 形状不对的信令条目丢掉，不拖垮这一次读取
  const signals = body.signals.flatMap((entry: unknown): ScreenSignal[] => {
    if (!isRecord(entry) || typeof entry.seq !== 'number' || typeof entry.type !== 'string' || entry.seq <= afterSeq) return []
    return [{ seq: entry.seq, dir: typeof entry.dir === 'string' ? entry.dir : null, type: entry.type, payload: entry.payload }]
  })
  // 结束原因和 STUN 列表只是附带信息，类型不对时按没有处理
  const endReason = body.session.end_reason
  const rawStun = body.session.stun_urls
  const stunUrls = Array.isArray(rawStun) ? rawStun.filter((url): url is string => typeof url === 'string' && /^(stun|stuns):/i.test(url)) : null
  return {
    session: { id, state, endReason: typeof endReason === 'string' ? endReason : null, stunUrls },
    signals,
  }
}

export interface ScreenSignalingOptions {
  /** 繁忙或网络错误时的重试间隔，缺省 SIGNAL_RETRY_DELAY_MS */
  retryDelayMs?: number
  /** 含第一次在内的最多尝试次数，缺省 SIGNAL_MAX_ATTEMPTS */
  maxAttempts?: number
}

export class ScreenSignalingClient {
  private chain: Promise<void> = Promise.resolve()
  /** 每次 abandon() 加一：之前排进队列的信令据此知道自己已经作废 */
  private epoch = 0
  private readonly retryDelayMs: number
  private readonly maxAttempts: number

  constructor(private readonly transport: ScreenTransport, options: ScreenSignalingOptions = {}) {
    this.retryDelayMs = options.retryDelayMs ?? SIGNAL_RETRY_DELAY_MS
    this.maxAttempts = options.maxAttempts ?? SIGNAL_MAX_ATTEMPTS
  }

  /** 按调用顺序逐条发送。繁忙和网络错误会重试；服务器已结束会话、被拒绝时不重试 */
  postSignal(sessionId: string, type: string, payload: unknown): Promise<SignalPostResult> {
    const epoch = this.epoch
    const run = async (): Promise<SignalPostResult> => {
      for (let attempts = 1; ; attempts++) {
        if (epoch !== this.epoch) return { ok: false, kind: 'abandoned', attempts: attempts - 1 }
        try {
          await this.transport.post(SCREEN_SIGNAL_PATH, { session_id: sessionId, type, payload })
          return { ok: true, attempts }
        } catch (error) {
          // 不是 ScreenTransportError 的异常说明传输层没按约定分类，按不可重试处理，免得把队列堵住
          const kind: ScreenTransportErrorKind = error instanceof ScreenTransportError ? error.kind : 'rejected'
          if (kind !== 'busy' && kind !== 'network') return { ok: false, kind, attempts }
          if (attempts >= this.maxAttempts) return { ok: false, kind, attempts }
          if (epoch !== this.epoch) return { ok: false, kind: 'abandoned', attempts }
          await delay(this.retryDelayMs)
        }
      }
    }
    const result = this.chain.then(run, run)
    this.chain = result.then(() => undefined, () => undefined)
    return result
  }

  /**
   * 放弃队列里还没开始发的信令，正在等重试的那条也不再重试（已经发出的那次请求由它自己结束）。
   * 会话结束或被新会话替换时调用；之后仍然可以 postSignal，例如最后一次 end。
   */
  abandon(): void {
    this.epoch++
  }

  /** 取会话状态和编号大于 afterSeq 的网页信令。传输失败或响应形状不对时返回 null */
  async readSession(sessionId: string, afterSeq: number): Promise<ScreenSessionRead | null> {
    let body: unknown
    try {
      body = await this.transport.post(SCREEN_SESSION_PATH, { session_id: sessionId, after_seq: afterSeq })
    } catch {
      return null
    }
    return parseSessionRead(body, afterSeq)
  }
}
