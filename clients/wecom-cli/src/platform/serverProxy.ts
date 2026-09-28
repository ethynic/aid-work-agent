/**
 * 服务端代理（M10c 起直连长期 token 模式）：read-session 抓取的聊天截图上传
 * 服务端 /api/client/v1/session-history，由 GLM-5.3-Flash 多模态并行解析为结构化
 * 消息（服务端 M10a 实现，commit 551871c7）。
 *
 * M10c（用户定稿）：自有机器调自有服务端，去掉激活码链路——不再有
 * /activate 调用、DPAPI 缓存 server-binding.json、401 重激活；直接用管理端签发的
 * static 长期 token（服务端 POST /api/saas/client-bindings/static 签发，
 * 鉴权时跳过过期检查）配置进环境变量。
 *
 * 配置（环境变量驱动，不新增 CLI 动词）：
 * - AID_WECOM_SERVER_URL     服务端地址，设置即启用模型主通道
 * - AID_WECOM_SERVER_TOKEN   服务端长期 token（static binding 的 access_token）
 *
 * 错误分类（readSession 按此决定是否降级 OCR）：
 * - config               配置问题（缺 URL/TOKEN、token 无效被服务端 401 拒绝）
 *                        → 不降级，直接报给用户（静默走 OCR 会让用户以为模型通道
 *                        免费或正常）；token 是手工配置的，直报让用户修配置，不重试
 * - insufficient_credit  402 余额不足 → 不降级，明确报给用户（走 OCR 会让用户以为
 *                        模型通道免费）
 * - unavailable          网络错误/超时/5xx/422（服务端暂时不可用或请求形态不符）
 *                        → 可降级 OCR 兜底
 * 用户取消（signal abort）抛 CancelledError，绝不归并为 unavailable。
 */
import { CancelledError } from '../operations/types.js'

export interface ProxyAuth {
  serverUrl: string
  accessToken: string
}

/** 服务端调用失败分类（见文件头注释；readSession 按 kind 决定降级与否） */
export type ProxyFailureKind = 'config' | 'insufficient_credit' | 'unavailable'

/** 可识别的服务端代理错误（readSession 捕获后按 kind 分流降级/直报） */
export class ProxyError extends Error {
  constructor(
    readonly kind: ProxyFailureKind,
    message: string,
    /** HTTP 状态码（网络错误无） */
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ProxyError'
  }
}

export type FetchFn = (
  url: string,
  init: { method: string; headers: Record<string, string>; body: string; signal?: AbortSignal },
) => Promise<{
  status: number
  json: () => Promise<Record<string, unknown>>
}>

export interface ServerProxyDeps {
  env?: NodeJS.ProcessEnv
  fetchFn?: FetchFn
  /** 请求超时（默认 150s：服务端单页 120s + 余量；测试可缩短） */
  timeoutMs?: number
  /** 用户取消信号（解析请求响应；abort 时抛 CancelledError 而非 ProxyError） */
  signal?: AbortSignal
}

function normalizeServerUrl(raw: string | undefined): string {
  return (raw ?? '').trim().replace(/\/+$/, '')
}

/** 模型主通道开关：配置了 AID_WECOM_SERVER_URL 即启用（readSession 侧的廉价预检） */
export function serverUrlConfigured(env: NodeJS.ProcessEnv = process.env): string | null {
  return normalizeServerUrl(env.AID_WECOM_SERVER_URL) || null
}

// ---------- 服务端请求基建（超时/取消常量 + 共用 POST） ----------

/** 服务端单页模型超时 120s（settings.session_history.page_timeout_seconds）+ 余量 */
export const SESSION_HISTORY_TIMEOUT_MS = 150_000

/** 服务端单图解码后大小上限（服务端 SESSION_IMAGE_MAX_BYTES，客户端预检同值省一次无效上传） */
export const SESSION_IMAGE_MAX_BYTES = 5 * 1024 * 1024

/** 带超时与取消的服务端 POST（会话解析用）；返回状态码 + 解析后的 JSON 体 */
async function fetchJson(
  url: string,
  init: { method: string; headers: Record<string, string>; body: string },
  label: string,
  deps: ServerProxyDeps,
): Promise<{ status: number; data: Record<string, unknown> }> {
  const fetchFn: FetchFn = deps.fetchFn ?? (fetch as unknown as FetchFn)
  const timeoutMs = deps.timeoutMs ?? SESSION_HISTORY_TIMEOUT_MS
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const onOuterAbort = () => controller.abort()
  deps.signal?.addEventListener('abort', onOuterAbort, { once: true })
  if (deps.signal?.aborted) controller.abort()
  try {
    const resp = await fetchFn(url, { ...init, signal: controller.signal })
    const data = await resp.json().catch(() => ({} as Record<string, unknown>))
    return { status: resp.status, data }
  } catch (err) {
    // 用户取消优先于超时判定（取消绝不归并为可降级的 unavailable）
    if (deps.signal?.aborted) throw new CancelledError()
    if (timedOut) {
      throw new ProxyError('unavailable', `服务端${label}请求超时（>${Math.round(timeoutMs / 1000)}s）`)
    }
    throw new ProxyError('unavailable', `服务端${label}网络错误：${err instanceof Error ? err.message : String(err)}`)
  } finally {
    clearTimeout(timer)
    deps.signal?.removeEventListener('abort', onOuterAbort)
  }
}

export type ProxyResolution =
  | { status: 'disabled' } // 未配置 AID_WECOM_SERVER_URL → read-session 直接 OCR 通道
  | { status: 'ok'; auth: ProxyAuth }
  | { status: 'error'; serverUrl: string; kind: ProxyFailureKind; message: string }

/**
 * 解析代理凭据（M10c 直连模式）：URL + token 都配置 → 直接可用；缺任一 → config 错误
 * （message 提示两个环境变量名）。无网络请求、无本地状态——token 即配置即用。
 */
export function resolveProxyAuth(deps: ServerProxyDeps = {}): ProxyResolution {
  const env = deps.env ?? process.env
  const serverUrl = normalizeServerUrl(env.AID_WECOM_SERVER_URL)
  if (!serverUrl) return { status: 'disabled' }

  const accessToken = (env.AID_WECOM_SERVER_TOKEN ?? '').trim()
  if (!accessToken) {
    return {
      status: 'error',
      serverUrl,
      kind: 'config',
      message:
        '已配置 AID_WECOM_SERVER_URL 但缺少 AID_WECOM_SERVER_TOKEN（服务端长期 token）；'
        + '请同时设置 AID_WECOM_SERVER_URL 与 AID_WECOM_SERVER_TOKEN 后重试',
    }
  }
  return { status: 'ok', auth: { serverUrl, accessToken } }
}

// ---------- 会话历史解析响应模型（M10a 服务端契约） ----------

export interface SessionHistoryMessage {
  time?: string
  side: string
  kind: string
  text: string
}

/** /session-history 200 响应（M10a 契约；messages 已过服务端清洗，此处仅形态防御） */
export interface SessionHistoryResponse {
  messages: SessionHistoryMessage[]
  pages: number
  latency_ms: Record<string, unknown>
  model_usage: Record<string, unknown>
  billing: Record<string, unknown>
  failed_pages?: number[]
  warning?: string
}

export interface ParseSessionHistoryParams {
  /** 聊天截图 base64（PNG），时间序旧→新（驱动采集序为新→旧，调用方须先反转） */
  images: string[]
  /** 会话标题（仅入服务端台账 detail 供审计） */
  sessionTitle?: string
  signal?: AbortSignal
}

/** 响应 messages 条目防御性归一（side/kind/text 必须 string；time 非空才保留） */
function parseResponseMessages(raw: unknown): SessionHistoryMessage[] {
  const arr = Array.isArray(raw) ? raw : []
  const out: SessionHistoryMessage[] = []
  for (const r of arr) {
    const it = (r ?? {}) as Record<string, unknown>
    const side = typeof it.side === 'string' ? it.side : ''
    const kind = typeof it.kind === 'string' ? it.kind : ''
    const text = typeof it.text === 'string' ? it.text : ''
    if (!side || !kind || !text) continue
    const time = typeof it.time === 'string' && it.time.length > 0 ? it.time : undefined
    out.push(time !== undefined ? { time, side, kind, text } : { side, kind, text })
  }
  return out
}

/** 200 响应体 → SessionHistoryResponse（messages 缺失/非数组视为契约异常 → unavailable） */
function parseSessionHistoryResponse(data: Record<string, unknown>): SessionHistoryResponse {
  if (!Array.isArray(data.messages)) {
    throw new ProxyError('unavailable', '服务端响应缺少 messages 数组（契约异常）')
  }
  const obj = (v: unknown): Record<string, unknown> =>
    v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
  const resp: SessionHistoryResponse = {
    messages: parseResponseMessages(data.messages),
    pages: Number(data.pages ?? 0) || 0,
    latency_ms: obj(data.latency_ms),
    model_usage: obj(data.model_usage),
    billing: obj(data.billing),
  }
  if (Array.isArray(data.failed_pages)) {
    resp.failed_pages = data.failed_pages.filter((p): p is number => typeof p === 'number')
  }
  if (typeof data.warning === 'string' && data.warning.length > 0) resp.warning = data.warning
  return resp
}

/** 按状态码分类非 200 响应（401 config 不降级 / 402 不降级 / 5xx·422 可降级） */
function classifyHttpFailure(status: number, data: Record<string, unknown>): ProxyError {
  const detail = typeof data.detail === 'string' ? data.detail : ''
  if (status === 401) {
    // 直连模式：token 是手工配置的长期值，401 = 配置错误（token 无效/被禁用），
    // 不重试不降级——直报让用户修配置
    return new ProxyError(
      'config',
      `服务端拒绝 token（401${detail ? `：${detail}` : ''}）：AID_WECOM_SERVER_TOKEN 无效或已被禁用，请检查配置`,
      status,
    )
  }
  if (status === 402) {
    return new ProxyError(
      'insufficient_credit',
      `服务端积分余额不足（402${detail ? `：${detail}` : ''}）——模型通道按次计积分，请充值后重试；本次未降级 OCR（避免误以为模型通道免费）`,
      status,
    )
  }
  if (status === 502) {
    return new ProxyError('unavailable', `服务端模型全部页面解析失败（502${detail ? `：${detail}` : ''}）`, status)
  }
  if (status >= 500) {
    return new ProxyError('unavailable', `服务端错误（HTTP ${status}${detail ? `：${detail}` : ''}）`, status)
  }
  // 422（图片超限/base64 非法）等其余 4xx：请求形态问题，OCR 通道不受此限制 → 可降级
  return new ProxyError('unavailable', `服务端拒绝请求（HTTP ${status}${detail ? `：${detail}` : ''}）`, status)
}

/**
 * 会话历史解析（模型主通道）：截图 base64 上传服务端 → 结构化消息。
 *
 * 流程：resolveProxyAuth（URL+token 直读环境变量）→ POST session-history → 200 返回
 * 归一响应。失败抛 ProxyError（kind 决定 readSession 是否降级 OCR：config/
 * insufficient_credit 不降级，unavailable 降级）；用户取消抛 CancelledError。
 * 未配置 SERVER_URL 抛 ProxyError('config')（readSession 预检后不应走到）。
 */
export async function parseSessionHistory(
  params: ParseSessionHistoryParams,
  deps: ServerProxyDeps = {},
): Promise<SessionHistoryResponse> {
  const fullDeps: ServerProxyDeps = { ...deps, signal: params.signal ?? deps.signal }
  const resolution = resolveProxyAuth(fullDeps)
  if (resolution.status === 'error') {
    throw new ProxyError(resolution.kind, resolution.message)
  }
  if (resolution.status === 'disabled') {
    throw new ProxyError('config', '未配置 AID_WECOM_SERVER_URL（模型通道未启用）')
  }
  const { auth } = resolution

  const { status, data } = await fetchJson(
    `${auth.serverUrl}/api/client/v1/session-history`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.accessToken}` },
      body: JSON.stringify({ images: params.images, client: 'wecom', session_title: params.sessionTitle }),
    },
    '会话解析',
    fullDeps,
  )
  if (status === 200) return parseSessionHistoryResponse(data)
  throw classifyHttpFailure(status, data)
}
