/**
 * 官网客服（web_c）C 端 API
 *
 * 免登录：请求不携带认证 header，身份凭证（ext 签名三元组 / 匿名 uid）由
 * buildIdentityQuery 按 URL query 或本地持久化构建，拼接到每个请求。
 * 后端模块 src/saas/api/web_c.py（设计 docs/system/web-c-channel-design.md）。
 */

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/c/web_c`

// ============== 匿名身份持久化（localStorage + cookie 双写，互为兜底） ==============

const ANON_TTL_DAYS = 365

function anonStorageKey(configId: string): string {
  return `web_c_anon_${configId}`
}

function anonCookieKey(configId: string): string {
  return `web_c_anon_${configId}`
}

function generateAnonUid(): string {
  // crypto.randomUUID 含版本位（4）与 variant 位，匹配后端 anon_ uuid4 校验
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `anon_${crypto.randomUUID()}`
  }
  // 兜底：手工拼 uuid4 形态
  const hex = '0123456789abcdef'
  let out = ''
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) out += '-'
    else if (i === 14) out += '4'
    else if (i === 19) out += hex[(Math.floor(Math.random() * 16) & 0x3) | 0x8]
    else out += hex[Math.floor(Math.random() * 16)]
  }
  return `anon_${out}`
}

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}=([^;]*)`))
  return match ? decodeURIComponent(match[1]) : ''
}

function writeCookie(name: string, value: string): void {
  const maxAge = ANON_TTL_DAYS * 24 * 3600
  // SameSite=Lax + 域级 path（iframe 嵌入第三方 cookie 受浏览器策略约束，
  // Safari ITP 下持久性预期在配置页帮助文字明示）
  document.cookie = `${name}=${encodeURIComponent(value)}; max-age=${maxAge}; path=/; SameSite=Lax`
}

/** 获取（必要时生成）匿名身份 uid：localStorage 优先，cookie 兜底互写 */
export function resolveAnonUid(configId: string): string {
  const lsKey = anonStorageKey(configId)
  const ckKey = anonCookieKey(configId)
  let uid = ''
  try {
    uid = localStorage.getItem(lsKey) || ''
  } catch {
    /* 隐私模式等场景 localStorage 不可用 */
  }
  if (!uid) uid = readCookie(ckKey)
  if (!/^anon_[0-9a-f-]{36}$/i.test(uid)) uid = generateAnonUid()
  try {
    localStorage.setItem(lsKey, uid)
  } catch {
    /* ignore */
  }
  writeCookie(ckKey, uid)
  return uid
}

// ============== uid 模式当前会话持有（sessionStorage，标签页生命周期） ==============
// 仅 userid 链接（uid 模式）下 userid 不验签，会话 id 不可由 userid 派生；
// 当前会话由前端 sessionStorage 持有：同标签页刷新保持、关标签页即新会话，
// 伪造 userid 者读不到他人 session_id（设计 §3.5）。sessionStorage 不可用
// （隐私模式/第三方 iframe 存储限制）降级为内存持有：刷新即新会话，只损失
// 连续性不损失安全性。

function sessionStorageKey(configId: string): string {
  return `web_c_sess_${configId}`
}

export function readStoredSessionId(configId: string): string {
  try {
    const value = sessionStorage.getItem(sessionStorageKey(configId)) || ''
    return value.length <= 200 ? value : ''
  } catch {
    return ''
  }
}

export function writeStoredSessionId(configId: string, sessionId: string): void {
  if (!sessionId || sessionId.length > 200) return
  try {
    sessionStorage.setItem(sessionStorageKey(configId), sessionId)
  } catch {
    /* 降级内存持有，刷新即新会话 */
  }
}

export function clearStoredSessionId(configId: string): void {
  try {
    sessionStorage.removeItem(sessionStorageKey(configId))
  } catch {
    /* ignore */
  }
}

// ============== 身份凭证构建 ==============

export interface IdentityQuery {
  u?: string
  ts?: string
  sig?: string
  name?: string
  uid?: string
}

/**
 * 从 URL query 提取身份凭证：签名三元组齐备走 ext；仅有 u（无/不全签名）按
 * 「仅 userid 链接」透传，可用性由后端渠道 identity_mode 门控（uid 模式接受，
 * 其余模式 403，由前端提示）；无 u 回退匿名 uid（同样受后端门控）。
 */
export function buildIdentityQuery(configId: string, routeQuery: Record<string, unknown>): IdentityQuery {
  const u = typeof routeQuery.u === 'string' ? routeQuery.u : ''
  if (u) {
    return {
      u,
      ts: typeof routeQuery.ts === 'string' ? routeQuery.ts : '',
      sig: typeof routeQuery.sig === 'string' ? routeQuery.sig : '',
      name: typeof routeQuery.name === 'string' ? routeQuery.name : ''
    }
  }
  return { uid: resolveAnonUid(configId) }
}

function identityQueryString(identity: IdentityQuery): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(identity)) {
    if (value) params.set(key, value)
  }
  return params.toString()
}

export function identityPath(tenantId: string, configId: string, identity: IdentityQuery, subpath = '', extraQuery = ''): string {
  const base = `${API_BASE}/${encodeURIComponent(tenantId)}/${encodeURIComponent(configId)}`
  const parts = [identityQueryString(identity), extraQuery].filter(Boolean)
  return `${base}${subpath}${parts.length ? `?${parts.join('&')}` : ''}`
}

// ============== 接口 ==============

export interface BootstrapResponse {
  success: boolean
  greeting: string
  /** 附件上传开关（渠道 config，默认 false：隐藏附件按钮，纯文字聊天） */
  allow_attachment?: boolean
  /** 历史会话入口开关（渠道 config，默认 false：不显示右上角「历史会话」按钮） */
  show_history?: boolean
  identity: { mode: string; channel_user_id: string; display_name?: string | null }
  subagent: { agent_id: string; name: string } | null
  sessions: { session_id: string; subagent_id: string; updated_at: string; last_message_at: string }[]
}

export interface UploadRef {
  file_id: string
  name: string
  type: 'image' | 'file'
  mime_type: string
  size: number
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error((data as { error?: string }).error || `请求失败（${response.status}）`)
  }
  return data as T
}

export function getBootstrap(tenantId: string, configId: string, identity: IdentityQuery): Promise<BootstrapResponse> {
  return request(identityPath(tenantId, configId, identity, '/bootstrap'))
}

export interface ChatResponse {
  success: boolean
  session_id: string
  runner_id: string | null
  status: string
  direct_reply: string | null
  waiting_text?: string
}

export function postChat(
  tenantId: string, configId: string, identity: IdentityQuery,
  body: { text: string; client_request_id: string; attachments: UploadRef[]; session_id?: string; new_session?: boolean }
): Promise<ChatResponse> {
  return request(identityPath(tenantId, configId, identity, '/chat'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
}

export function uploadFile(
  tenantId: string, configId: string, identity: IdentityQuery, file: File
): Promise<UploadRef> {
  const form = new FormData()
  form.append('file', file)
  return request(identityPath(tenantId, configId, identity, '/upload'), {
    method: 'POST',
    body: form
  })
}

export function cancelRunner(
  tenantId: string, configId: string, identity: IdentityQuery, runnerId: string
): Promise<{ success: boolean }> {
  return request(identityPath(tenantId, configId, identity, `/runners/${encodeURIComponent(runnerId)}/cancel`), { method: 'POST' })
}

// ============== Runner 快照（轮询渲染流式 output / waiting / 终态） ==============

export interface RunnerSnapshot {
  runner_id: string
  status: string
  snapshot: { output?: string; images?: unknown[]; downloadableFiles?: { file_id: string; name?: string; file_name?: string; size?: number; mime_type?: string; download_url?: string }[]; waiting?: Record<string, unknown> }
  result?: { output?: string; images?: unknown[] } | null
  waiting_text?: string
}

export function getRunner(
  tenantId: string, configId: string, identity: IdentityQuery, runnerId: string
): Promise<{ success: boolean; runner: RunnerSnapshot }> {
  return request(identityPath(tenantId, configId, identity, `/runners/${encodeURIComponent(runnerId)}`))
}

export interface SessionSummary {
  session_id: string
  subagent_id: string
  created_at?: string
  updated_at?: string
  last_message_at?: string
}

export function listSessions(tenantId: string, configId: string, identity: IdentityQuery, limit = 50): Promise<{ success: boolean; sessions: SessionSummary[] }> {
  return request(identityPath(tenantId, configId, identity, '/sessions', `limit=${limit}`))
}

export interface ChannelMessage {
  message_id: string
  role: string
  content: string
  message_type: string
  attachments?: Record<string, unknown>[] | null
  metadata?: Record<string, unknown> | null
  created_at: string
}

export function getSessionMessages(
  tenantId: string, configId: string, identity: IdentityQuery, sessionId: string, page = 1, pageSize = 50
): Promise<{ success: boolean; messages: ChannelMessage[]; total: number }> {
  return request(identityPath(tenantId, configId, identity,
    `/sessions/${encodeURIComponent(sessionId)}/messages`, `page=${page}&page_size=${pageSize}`))
}

/** C 端附件代理下载地址（归属校验在后端，防遍历） */
export function webCFileUrl(tenantId: string, configId: string, identity: IdentityQuery, fileId: string): string {
  return identityPath(tenantId, configId, identity, `/files/${encodeURIComponent(fileId)}`)
}

/** Runner events SSE 地址（变更通知流；事件无载荷，每帧触发快照刷新） */
export function runnerEventsUrl(tenantId: string, configId: string, identity: IdentityQuery, runnerId: string, afterSeq = 0): string {
  return identityPath(tenantId, configId, identity,
    `/runners/${encodeURIComponent(runnerId)}/events`, `after_seq=${afterSeq}`)
}
