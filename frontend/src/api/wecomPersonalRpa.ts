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
  /** 租户 id（list_all_bindings 返回，普通 list_bindings 不含） */
  tenant_id?: string | null
  /** 租户名（list_all_bindings 返回，普通 list_bindings 不含） */
  tenant_name?: string | null
  /** 关联客户端 id（list_all_bindings 返回，普通 list_bindings 可能为 null） */
  client_id?: string | null
  /** 关联客户端名称 */
  client_name?: string | null
  /** 客户端回填的服务端生产地址（运维排查用） */
  agent_base_url?: string | null
  /** 最近一次客户端心跳时间（来自 clients.last_seen_at） */
  last_heartbeat_at?: string | null
}

/**
 * 平台后台「RPA 绑定管理」列表行（一行一 client，聚合视图）。
 *
 * 自 2026-06 改造后 ``GET /all_bindings`` 以 wecom_rpa_clients 为基准返回，
 * 确保创建 client 后即可看到，无需等待 client 真正连上来生成 binding。
 */
export interface RpaClientRow {
  /** 客户端 id（主键） */
  client_id: string
  /** 租户 id */
  tenant_id: string
  /** 客户端显示名称 */
  client_name: string | null
  /** 客户端状态：active / disabled（来自 wecom_rpa_clients.status） */
  client_status: string
  /** 客户端回填的服务端生产地址（运维排查用） */
  agent_base_url: string | null
  /** 最近一次客户端心跳时间（来自 clients.last_seen_at，NULL 表示从未连上来过） */
  last_heartbeat_at: string | null
  /** 允许继续托管的最小客户端版本 */
  min_version: string | null
  created_at: string | null
  updated_at: string | null
  /** 该 client 下的账号数（0 表示客户端从未注册过任何企微账号） */
  account_count: number
  /** 该 client 下的会话绑定总数 */
  binding_count: number
  /** 最近一个账号的 display_name（用于列表快速预览，可能为 null） */
  last_account_name: string | null
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

export async function registerClient(
  req: RegisterClientReq,
  opts?: {
    /**
     * 平台管理员代管理时手动指定目标租户（平台后台路径 /portal/* 不会自动注入 X-Tenant-Id）。
     * 不传则走 getSaasAuthHeader 的默认逻辑（/t/* 路径自动从 URL 提取）。
     */
    tenantId?: string
  },
): Promise<RegisterClientResult> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...getSaasAuthHeader(),
  }
  if (opts?.tenantId) {
    headers['X-Tenant-Id'] = opts.tenantId
  }
  const res = await fetch(`${API_BASE}/clients`, {
    method: 'POST',
    headers,
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

export async function rotateClientSecret(
  clientId: string,
  opts?: {
    /**
     * 平台管理员代管理时手动指定目标租户（平台后台路径 /portal/* 不会自动注入 X-Tenant-Id）。
     * 不传则走 getSaasAuthHeader 的默认逻辑（/t/* 路径自动从 URL 提取）。
     * 租户后台 WecomPersonalRpaManager.vue 不需要传此参数。
     */
    tenantId?: string
  },
): Promise<RotateSecretResult> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...getSaasAuthHeader(),
  }
  if (opts?.tenantId) {
    headers['X-Tenant-Id'] = opts.tenantId
  }
  const res = await fetch(`${API_BASE}/clients/${encodeURIComponent(clientId)}/rotate-secret`, {
    method: 'POST',
    headers,
  })
  const body = await parseJson(res, '轮换密钥失败')
  return body.data
}

/** 暂停客户端（active → disabled） */
export async function pauseClient(
  clientId: string,
  opts?: { tenantId?: string },
): Promise<{ client_id: string; client_status: string }> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...getSaasAuthHeader(),
  }
  if (opts?.tenantId) {
    headers['X-Tenant-Id'] = opts.tenantId
  }
  const res = await fetch(`${API_BASE}/clients/${encodeURIComponent(clientId)}/pause`, {
    method: 'POST',
    headers,
  })
  const body = await parseJson(res, '暂停客户端失败')
  return body.data
}

/** 恢复客户端（disabled → active） */
export async function resumeClient(
  clientId: string,
  opts?: { tenantId?: string },
): Promise<{ client_id: string; client_status: string }> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...getSaasAuthHeader(),
  }
  if (opts?.tenantId) {
    headers['X-Tenant-Id'] = opts.tenantId
  }
  const res = await fetch(`${API_BASE}/clients/${encodeURIComponent(clientId)}/resume`, {
    method: 'POST',
    headers,
  })
  const body = await parseJson(res, '恢复客户端失败')
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

/**
 * 平台管理员视角：跨租户列出所有 RPA 客户端（一行一 client，聚合 account/binding）。
 * 仅 platform_admin 可访问；普通租户管理员调用会被后端 403 拒绝。
 *
 * 注意：自 2026-06 改造后 status 参数语义为 client.status（不再是 binding.status）；
 * 传 'needs_review_only' 会排除 active client。
 */
export async function listAllBindings(
  params: { status?: string; tenant_id?: string } = {},
): Promise<RpaClientRow[]> {
  const res = await fetch(`${API_BASE}/all_bindings${buildQuery(params)}`, {
    headers: getSaasAuthHeader(),
  })
  const body = await parseJson(res, '获取客户端列表失败')
  return body.data ?? []
}

/**
 * 更新客户端回填的 agent_base_url（运维排查用）。
 * 传入空字符串或 null 清除字段。
 */
export async function updateClientAgentBaseUrl(
  clientId: string,
  agentBaseUrl: string | null,
): Promise<{ client_id: string; agent_base_url: string | null }> {
  const res = await fetch(`${API_BASE}/clients/${encodeURIComponent(clientId)}/agent_base_url`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify({ agent_base_url: agentBaseUrl }),
  })
  const body = await parseJson(res, '更新 agent_base_url 失败')
  return body.data
}

export async function confirmBinding(bindingId: string): Promise<{ binding_id: string; status: string }> {
  const res = await fetch(`${API_BASE}/bindings/${encodeURIComponent(bindingId)}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
  })
  const body = await parseJson(res, '确认绑定失败')
  return body.data
}

export async function updateBinding(
  bindingId: string,
  payload: { display_name?: string },
): Promise<RpaBinding> {
  const res = await fetch(`${API_BASE}/bindings/${encodeURIComponent(bindingId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(payload),
  })
  const body = await parseJson(res, '更新会话绑定失败')
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

// ==================== 5. 指标 / 告警 ====================

export interface RpaMetrics {
  window_hours: number
  generated_at: string
  clients: { total: number; online: number; online_rate: number }
  accounts: { total: number; online: number; online_rate: number; by_status: Record<string, number> }
  actions: {
    by_status: Record<string, number>
    succeeded: number
    failed: number
    success_rate: number
  }
  audit_counts: Record<string, number>
  bindings: { needs_review: number; by_status: Record<string, number> }
}

export interface RpaAlert {
  rule: string
  severity: 'danger' | 'warning' | 'info'
  entity_type: 'client' | 'account' | 'binding'
  entity_id: string
  entity_name: string
  message: string
}

export async function getMetrics(windowHours = 24): Promise<RpaMetrics> {
  const res = await fetch(`${API_BASE}/metrics${buildQuery({ window_hours: windowHours })}`, {
    headers: getSaasAuthHeader(),
  })
  const body = await parseJson(res, '获取指标失败')
  return body.data
}

export async function getAlerts(): Promise<RpaAlert[]> {
  const res = await fetch(`${API_BASE}/alerts`, {
    headers: getSaasAuthHeader(),
  })
  const body = await parseJson(res, '获取告警失败')
  return body.data ?? []
}
