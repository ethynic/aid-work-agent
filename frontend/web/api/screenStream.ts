/**
 * 实时画面（桌面画面查看）API
 *
 * 对应后端 src/local_tools/api.py 的网页侧画面接口（设计 runtime-screen-stream-design.md 5.2）：
 *   POST /api/local-tools/devices/{id}/screen-sessions                  创建会话
 *   GET  /api/local-tools/devices/{id}/screen-sessions/{sid}?after_seq  轮询会话与 Runtime 信令
 *   POST /api/local-tools/devices/{id}/screen-sessions/{sid}/signals    追加一条网页信令
 *   POST /api/local-tools/devices/{id}/screen-sessions/{sid}/end        结束会话
 *
 * 错误处理：后端 HTTPException 的 detail 为 {error, code, debug}，解析为带 code 的
 * ScreenStreamApiError 抛出；组合函数按 code 区分重试（SCREEN_STREAM_BUSY）、
 * 结束（SCREEN_SESSION_ENDED / SCREEN_SESSION_NOT_FOUND）与提示（DEVICE_OFFLINE 等）。
 */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/local-tools`

/** 带错误码的接口错误：code 与后端 screen_stream.ERRORS 的键逐字对应 */
export class ScreenStreamApiError extends Error {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = 'ScreenStreamApiError'
    this.code = code
  }
}

/** 创建会话响应：session 里带 STUN 服务器列表 */
export interface ScreenSessionCreated {
  sessionId: string
  state: string
  stunUrls: string[]
}

/** 轮询响应：会话状态 + 编号大于 after_seq 的 Runtime 信令 */
export interface ScreenSessionSignals {
  session: { id: string; state: string; end_reason: string | null }
  signals: ScreenSignal[]
}

export interface ScreenSignal {
  seq: number
  type: 'prepared' | 'offer' | 'candidate' | 'status' | 'end'
  payload: Record<string, any>
}

/** 网页 → Runtime 的信令类型（服务端按 type 判别，取值固定四种） */
export type ViewerSignalType = 'start' | 'answer' | 'candidate' | 'end'

/**
 * 解析后端错误响应并抛出带 code 的错误。
 * FastAPI 的 HTTPException detail 结构为 {detail: {error, code, debug}}。
 */
async function throwScreenError(res: Response, fallback: string): Promise<never> {
  let message = fallback
  let code = 'SCREEN_STREAM_ERROR'
  try {
    const body = await res.json()
    const detail = body?.detail
    if (detail && typeof detail === 'object') {
      if (typeof detail.error === 'string' && detail.error) message = detail.error
      if (typeof detail.code === 'string' && detail.code) code = detail.code
    } else if (typeof detail === 'string' && detail) {
      message = detail
    }
  } catch {
    // 响应体非 JSON 时使用兜底文案
  }
  throw new ScreenStreamApiError(code, message)
}

/** 校验 2xx；失败抛 ScreenStreamApiError */
async function ensureOk(res: Response, fallback: string): Promise<void> {
  if (!res.ok) {
    await throwScreenError(res, fallback)
  }
}

/**
 * 创建实时画面会话：同一设备同一时间只允许一个未结束会话。
 * 可能的 code：DEVICE_NOT_FOUND / DEVICE_OFFLINE / SCREEN_STREAM_UNSUPPORTED /
 * SCREEN_SESSION_ACTIVE / SCREEN_STREAM_DISABLED / SCREEN_STREAM_BUSY
 */
export async function createScreenSession(deviceId: string): Promise<ScreenSessionCreated> {
  const res = await fetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}/screen-sessions`, {
    method: 'POST',
    headers: { ...getAuthHeader() },
  })
  await ensureOk(res, '创建画面会话失败，请稍后重试')
  const body = await res.json()
  const session = body?.session
  if (!session || typeof session.id !== 'string') {
    throw new ScreenStreamApiError('SCREEN_STREAM_ERROR', '创建画面会话失败，请稍后重试')
  }
  return {
    sessionId: session.id,
    state: typeof session.state === 'string' ? session.state : 'pending',
    stunUrls: Array.isArray(session.stun_urls) ? session.stun_urls.filter((u: unknown) => typeof u === 'string' && u) : [],
  }
}

/**
 * 轮询会话：返回会话状态与 Runtime 发来的、序号大于 afterSeq 的信令。
 * 可能的 code：SCREEN_SESSION_NOT_FOUND / SCREEN_STREAM_BUSY / SCREEN_STREAM_DISABLED
 */
export async function pollScreenSession(deviceId: string, sessionId: string, afterSeq: number): Promise<ScreenSessionSignals> {
  const res = await fetch(
    `${API_BASE}/devices/${encodeURIComponent(deviceId)}/screen-sessions/${encodeURIComponent(sessionId)}?after_seq=${afterSeq}`,
    { headers: { ...getAuthHeader() } },
  )
  await ensureOk(res, '读取画面会话失败，请稍后重试')
  const body = await res.json()
  const session = body?.session
  if (!session || typeof session.id !== 'string') {
    throw new ScreenStreamApiError('SCREEN_STREAM_ERROR', '读取画面会话失败，请稍后重试')
  }
  return {
    session: {
      id: session.id,
      state: typeof session.state === 'string' ? session.state : 'pending',
      end_reason: typeof session.end_reason === 'string' ? session.end_reason : null,
    },
    signals: Array.isArray(body.signals) ? body.signals : [],
  }
}

/**
 * 追加一条网页信令（start / answer / candidate）。
 * 可能的 code：SCREEN_SESSION_ENDED / SCREEN_SIGNAL_LIMIT / SCREEN_SESSION_NOT_FOUND / SCREEN_STREAM_BUSY
 */
export async function sendScreenSignal(
  deviceId: string, sessionId: string, type: ViewerSignalType, payload: Record<string, any>,
): Promise<number | null> {
  const res = await fetch(
    `${API_BASE}/devices/${encodeURIComponent(deviceId)}/screen-sessions/${encodeURIComponent(sessionId)}/signals`,
    {
      method: 'POST',
      headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, payload }),
    },
  )
  await ensureOk(res, '发送画面信令失败，请稍后重试')
  const body = await res.json()
  return typeof body?.seq === 'number' ? body.seq : null
}

/**
 * 结束会话（幂等，对已结束的会话也返回成功）。请求体里的 reason 服务端原样忽略，固定记 viewer_closed。
 */
export async function endScreenSession(deviceId: string, sessionId: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/devices/${encodeURIComponent(deviceId)}/screen-sessions/${encodeURIComponent(sessionId)}/end`,
    {
      method: 'POST',
      headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'viewer_closed' }),
    },
  )
  await ensureOk(res, '结束画面会话失败，请稍后重试')
}
