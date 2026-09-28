/**
 * 服务端代理（M10b 模型主通道）：read-session 抓取的聊天截图上传服务端
 * /api/client/v1/session-history，由 GLM-5.3-Flash 多模态并行解析为结构化消息
 * （服务端 M10a 实现，commit 551871c7）。模式对齐 weixin-cli serverProxy.ts
 * （激活码→access_token、DPAPI CurrentUser 加密缓存、401 重激活、402 透传），
 * 差异：weixin-cli 由 PowerShell 驱动持 token 调服务端（env 注入），wecom-cli
 * 由 TS 侧直接调用（驱动只负责导航+滚动截图），因此激活与解析请求均带超时/取消。
 *
 * 配置（环境变量驱动，不新增 CLI 动词）：
 * - AID_WECOM_SERVER_URL        服务端地址，设置即启用模型主通道
 * - AID_WECOM_ACTIVATION_CODE   一次性激活码；本地无有效绑定时首次调用自动激活
 *
 * access_token 不落明文：DPAPI（CurrentUser）加密后存
 * %LOCALAPPDATA%\AidWorkAgent\wecom-cli\server-binding.json。
 *
 * 错误分类（readSession 按此决定是否降级 OCR）：
 * - config               配置/激活问题（无绑定无激活码、激活码被拒、token 重激活后
 *                        仍 401）→ 不降级，直接报给用户（静默走 OCR 会让用户以为
 *                        模型通道免费或正常）
 * - insufficient_credit  402 余额不足 → 不降级，明确报给用户（走 OCR 会让用户以为
 *                        模型通道免费）
 * - unavailable          网络错误/超时/5xx/422（服务端暂时不可用或请求形态不符）
 *                        → 可降级 OCR 兜底
 * 用户取消（signal abort）抛 CancelledError，绝不归并为 unavailable。
 */
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { hostname } from 'node:os'
import { join } from 'node:path'
import { CancelledError } from '../operations/types.js'
import { protectText, unprotectText, type DpapiRunnerFn } from '../security/dpapi.js'

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
  dpapiRunFn?: DpapiRunnerFn
  /** 测试用：覆盖状态目录（默认 %LOCALAPPDATA%\AidWorkAgent\wecom-cli 或 ~/.AidWorkAgent/wecom-cli） */
  stateDir?: string
  hostnameFn?: () => string
  /** 请求超时（默认 150s：服务端单页 120s + 余量；激活与解析各自独立计时；测试可缩短） */
  timeoutMs?: number
  /** 用户取消信号（激活与解析请求均响应；abort 时抛 CancelledError 而非 ProxyError） */
  signal?: AbortSignal
}

interface BindingFile {
  server_url: string
  token_blob: string
  tenant_name?: string
  activated_at?: string
}

export function getStateDir(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.LOCALAPPDATA || env.HOME || '.'
  return join(base, 'AidWorkAgent', 'wecom-cli')
}

function bindingPath(stateDir: string): string {
  return join(stateDir, 'server-binding.json')
}

function normalizeServerUrl(raw: string | undefined): string {
  return (raw ?? '').trim().replace(/\/+$/, '')
}

/** 模型主通道开关：配置了 AID_WECOM_SERVER_URL 即启用（readSession 侧的廉价预检） */
export function serverUrlConfigured(env: NodeJS.ProcessEnv = process.env): string | null {
  return normalizeServerUrl(env.AID_WECOM_SERVER_URL) || null
}

/** 读取本地绑定并解出 access_token；文件缺失/损坏/解密失败一律返回 null（触发重新激活） */
export async function loadBinding(
  deps: ServerProxyDeps = {},
): Promise<(ProxyAuth & { tenantName?: string }) | null> {
  const env = deps.env ?? process.env
  const stateDir = deps.stateDir ?? getStateDir(env)
  const path = bindingPath(stateDir)
  if (!existsSync(path)) return null
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as BindingFile
    if (!parsed.server_url || !parsed.token_blob) return null
    const accessToken = await unprotectText(parsed.token_blob, deps.dpapiRunFn)
    if (!accessToken) return null
    return { serverUrl: parsed.server_url, accessToken, tenantName: parsed.tenant_name }
  } catch {
    return null
  }
}

/** 删除本地绑定缓存（401 token 失效时清掉重激活用）；文件不存在/删除失败不抛 */
export function clearBinding(deps: ServerProxyDeps = {}): void {
  const env = deps.env ?? process.env
  const stateDir = deps.stateDir ?? getStateDir(env)
  try {
    unlinkSync(bindingPath(stateDir))
  } catch {
    // 不存在/占用等：重激活成功后会整体覆盖，无需失败
  }
}

// ---------- 服务端请求基建（超时/取消常量 + 共用 POST） ----------

/** 服务端单页模型超时 120s（settings.session_history.page_timeout_seconds）+ 余量 */
export const SESSION_HISTORY_TIMEOUT_MS = 150_000

/** 服务端单图解码后大小上限（服务端 SESSION_IMAGE_MAX_BYTES，客户端预检同值省一次无效上传） */
export const SESSION_IMAGE_MAX_BYTES = 5 * 1024 * 1024

/** 带超时与取消的服务端 POST（激活/会话解析共用）；返回状态码 + 解析后的 JSON 体 */
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

/** 激活码 → access_token，DPAPI 加密后落盘（明文 token 仅存在于内存） */
export async function activateAndStore(
  serverUrl: string,
  activationCode: string,
  deps: ServerProxyDeps = {},
): Promise<ProxyAuth> {
  const env = deps.env ?? process.env
  const stateDir = deps.stateDir ?? getStateDir(env)
  const machineId = (deps.hostnameFn ?? hostname)()

  const { status, data } = await fetchJson(
    `${serverUrl}/api/client/v1/activate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        activation_code: activationCode.trim().toUpperCase(),
        machine_id: machineId,
        client_name: `wecom-cli@${machineId}`,
      }),
    },
    '激活',
    deps,
  )
  if (status !== 200) {
    const detail = typeof data.detail === 'string' ? data.detail : `HTTP ${status}`
    throw new ProxyError('config', `激活失败：${detail}`, status)
  }
  const accessToken = typeof data.access_token === 'string' ? data.access_token : ''
  if (!accessToken) throw new ProxyError('config', '激活响应缺少 access_token（服务端契约变更？）')

  const blob = await protectText(accessToken, deps.dpapiRunFn)
  mkdirSync(stateDir, { recursive: true })
  const file: BindingFile = {
    server_url: serverUrl,
    token_blob: blob,
    tenant_name: typeof data.tenant_name === 'string' ? data.tenant_name : undefined,
    activated_at: new Date().toISOString(),
  }
  writeFileSync(bindingPath(stateDir), JSON.stringify(file), { mode: 0o600 })
  return { serverUrl, accessToken }
}

export type ProxyResolution =
  | { status: 'disabled' } // 未配置 AID_WECOM_SERVER_URL → read-session 直接 OCR 通道
  | { status: 'ok'; auth: ProxyAuth }
  | { status: 'error'; serverUrl: string; kind: ProxyFailureKind; message: string }

/**
 * 解析代理凭据：本地绑定命中 → 复用；否则有激活码 → 惰性激活；否则 config 错误。
 * 失败归并为 error 状态（kind 区分 config/unavailable，readSession 按此决定降级）；
 * 唯一例外：用户取消（CancelledError）直接透传，绝不吞成 error。
 */
export async function resolveProxyAuth(deps: ServerProxyDeps = {}): Promise<ProxyResolution> {
  const env = deps.env ?? process.env
  const serverUrl = normalizeServerUrl(env.AID_WECOM_SERVER_URL)
  if (!serverUrl) return { status: 'disabled' }

  const cached = await loadBinding(deps)
  if (cached && cached.serverUrl === serverUrl) return { status: 'ok', auth: cached }

  const code = (env.AID_WECOM_ACTIVATION_CODE ?? '').trim()
  if (!code) {
    return {
      status: 'error',
      serverUrl,
      kind: 'config',
      message:
        '已配置 AID_WECOM_SERVER_URL 但本机无有效绑定；请设置 AID_WECOM_ACTIVATION_CODE（一次性激活码）后重试，首次调用会自动激活',
    }
  }
  try {
    const auth = await activateAndStore(serverUrl, code, deps)
    return { status: 'ok', auth }
  } catch (err) {
    if (err instanceof CancelledError) throw err
    if (err instanceof ProxyError) return { status: 'error', serverUrl, kind: err.kind, message: err.message }
    return { status: 'error', serverUrl, kind: 'unavailable', message: err instanceof Error ? err.message : String(err) }
  }
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

/** 按状态码分类非 200 响应（402 不降级 / 5xx·422 可降级 / 其余 4xx 可降级但带状态码） */
function classifyHttpFailure(status: number, data: Record<string, unknown>): ProxyError {
  const detail = typeof data.detail === 'string' ? data.detail : ''
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
 * 流程：resolveProxyAuth（本地绑定/惰性激活）→ POST session-history → 401 时清绑定
 * 重激活一次重试 → 200 返回归一响应。失败抛 ProxyError（kind 决定 readSession 是否
 * 降级 OCR：config/insufficient_credit 不降级，unavailable 降级）；用户取消抛
 * CancelledError。未配置 SERVER_URL 抛 ProxyError('config')（readSession 预检后不应走到）。
 */
export async function parseSessionHistory(
  params: ParseSessionHistoryParams,
  deps: ServerProxyDeps = {},
): Promise<SessionHistoryResponse> {
  const fullDeps: ServerProxyDeps = { ...deps, signal: params.signal ?? deps.signal }
  const resolution = await resolveProxyAuth(fullDeps)
  if (resolution.status === 'error') {
    throw new ProxyError(resolution.kind, resolution.message)
  }
  if (resolution.status === 'disabled') {
    throw new ProxyError('config', '未配置 AID_WECOM_SERVER_URL（模型通道未启用）')
  }
  let auth = resolution.auth

  const postOnce = (): Promise<{ status: number; data: Record<string, unknown> }> =>
    fetchJson(
      `${auth.serverUrl}/api/client/v1/session-history`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${auth.accessToken}` },
        body: JSON.stringify({ images: params.images, client: 'wecom', session_title: params.sessionTitle }),
      },
      '会话解析',
      fullDeps,
    )

  let first = await postOnce()
  if (first.status === 200) return parseSessionHistoryResponse(first.data)

  // 401：token 失效（服务端重启/换库/过期）→ 清缓存重激活一次重试
  if (first.status === 401) {
    clearBinding(fullDeps)
    const reResolved = await resolveProxyAuth(fullDeps)
    if (reResolved.status === 'error') {
      throw new ProxyError(reResolved.kind, `token 失效且重新激活失败：${reResolved.message}`)
    }
    if (reResolved.status === 'disabled') {
      throw new ProxyError('config', '未配置 AID_WECOM_SERVER_URL（模型通道未启用）')
    }
    auth = reResolved.auth
    first = await postOnce()
    if (first.status === 200) return parseSessionHistoryResponse(first.data)
    if (first.status === 401) {
      throw new ProxyError('config', 'token 无效且重新激活后仍被拒绝（401），请检查服务端配置', 401)
    }
  }
  throw classifyHttpFailure(first.status, first.data)
}
