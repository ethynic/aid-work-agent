/**
 * 企业微信个人账号 RPA 管理 / 绑定 / 审计 API Client
 *
 * 对应后端路由前缀：/api/saas/wecom-personal-rpa/*（src/saas/api/wecom_personal_rpa_admin.py）
 * 全部 require_admin + 租户隔离，复用 saasTenant.ts 的 getSaasAuthHeader()（自动带 X-Tenant-Id）。
 *
 * 响应约定（后端 _ok/_fail）：
 * - 成功：{ success: true, data: ... }
 * - SaaS 未启用：200 + { success: false, message }（非异常，需按 success 判断）
 * - 业务错误：HTTPException → { detail: { success: false, message, debug? } }
 */

import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/wecom-personal-rpa`

// ==================== 类型定义 ====================

export interface RpaAccountSummary {
  account_id: string
  display_name: string
  status: string
  last_login_at: string | null
}

export interface RpaClientSummary {
  client_id: string
  name: string
  status: string
  min_version: string
  last_seen_at: string | null
  created_at: string | null
  updated_at: string | null
  accounts_count: number
  accounts: RpaAccountSummary[]
}

export interface RpaAccount {
  account_id: string
  client_id: string
  display_name: string
  status: string
  paused_reason: string | null
  last_login_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface RpaBinding {
  binding_id: string
  account_id: string
  conversation_type: string
  display_name: string
  search_key: string
  stable_id: string
  status: string
  last_verified_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface RpaAudit {
  audit_id: string
  client_id: string | null
  account_id: string | null
  action_id: string | null
  category: string
  payload: Record<string, any> | null
  created_at: string | null
}

export interface RegisterClientReq {
  name: string
  min_version?: string
  subagent_type?: string
}

export interface RegisterClientResult {
  client_id: string
  client_secret: string
  min_version: string
  name: string
  secret_warning: string
}

export interface RotateSecretResult {
  client_id: string
  client_secret: string
  secret_warning: string
}

export interface PauseResumeReq {
  scope: 'tenant' | 'account' | 'conversation'
  account_id?: string
  conversation_id?: string
  reason?: string
}

export interface PauseResumeResult {
  scope: string
  action: 'pause' | 'resume'
  affected: string[]
}

export interface ListBindingsParams {
  status?: string
  account_id?: string
}

export interface ListAuditParams {
  category?: string
  client_id?: string
  account_id?: string
  action_id?: string
  limit?: number
}

// ==================== 内部工具 ====================

/**
 * 解析后端响应，统一处理三种形态：
 * - 成功 200：返回 body（含 data）
 * - SaaS 未启用 200：success=false，抛 body.message
 * - HTTPException 4xx/5xx：{ detail: { success, message, debug? } }，抛 detail.message
 */
async function parseJson(res: Response, fallback: string): Promise<any> {
  let body: any
  try {
    body = await res.json()
  } catch {
    throw new Error(fallback)
  }
  // HTTPException 包了一层 detail
  const payload = body?.detail && typeof body.detail === 'object' ? body.detail : body
  if (res.status === 401 || res.status === 403) {
    throw new Error(payload?.message || '无权操作')
  }
  if (!res.ok || payload?.success === false) {
    throw new Error(payload?.message || fallback)
  }
  return payload
}

function buildQuery(params: Record<string, any>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.append(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

// ==================== 1. 客户端注册 / 列表 / 密钥轮换 ====================

export async function registerClient(req: RegisterClientReq): Promise<RegisterClientResult> {
  const res = await fetch(`${API_BASE}/clients`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(req),
  })
  const body = await parseJson(res, '注册客户端失败')
  return body.data
}

export async function listClients(): Promise<RpaClientSummary[]> {
  const res = await fetch(`${API_BASE}/clients`, {
    headers: getSaasAuthHeader(),
  })
  const body = await parseJson(res, '获取客户端列表失败')
  return body.data ?? []
}

export async function listClientAccounts(clientId: string): Promise<RpaAccount[]> {
  const res = await fetch(`${API_BASE}/clients/${encodeURIComponent(clientId)}/accounts`, {
    headers: getSaasAuthHeader(),
  })
  const body = await parseJson(res, '获取账号列表失败')
  return body.data ?? []
}

export async function rotateClientSecret(clientId: string): Promise<RotateSecretResult> {
  const res = await fetch(`${API_BASE}/clients/${encodeURIComponent(clientId)}/rotate-secret`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
  })
  const body = await parseJson(res, '轮换密钥失败')
  return body.data
}

// ==================== 2. 会话绑定查询 / 复核 ====================

export async function listBindings(params: ListBindingsParams = {}): Promise<RpaBinding[]> {
  const res = await fetch(`${API_BASE}/bindings${buildQuery(params)}`, {
    headers: getSaasAuthHeader(),
  })
  const body = await parseJson(res, '获取绑定列表失败')
  return body.data ?? []
}

export async function confirmBinding(bindingId: string): Promise<{ binding_id: string; status: string }> {
  const res = await fetch(`${API_BASE}/bindings/${encodeURIComponent(bindingId)}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
  })
  const body = await parseJson(res, '确认绑定失败')
  return body.data
}

// ==================== 3. 暂停 / 恢复（tenant / account / conversation 三级，幂等） ====================

export async function pause(req: PauseResumeReq): Promise<PauseResumeResult> {
  const res = await fetch(`${API_BASE}/pause`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(req),
  })
  const body = await parseJson(res, '暂停失败')
  return body.data
}

export async function resume(req: PauseResumeReq): Promise<PauseResumeResult> {
  const res = await fetch(`${API_BASE}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(req),
  })
  const body = await parseJson(res, '恢复失败')
  return body.data
}

// ==================== 4. 审计日志 ====================

export async function listAudit(params: ListAuditParams = {}): Promise<RpaAudit[]> {
  const res = await fetch(`${API_BASE}/audit${buildQuery(params)}`, {
    headers: getSaasAuthHeader(),
  })
  const body = await parseJson(res, '获取审计日志失败')
  return body.data ?? []
}
