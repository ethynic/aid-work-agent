/**
 * SaaS 租户管理 API Client
 * 所有 /api/saas/* 调用的统一封装
 * 使用独立的 saas_token（与演示模式 demo_token 分离）
 * 租户前台路由下，saas_token 按 tenant_id 隔离，避免平台管理员多 tab 串号
 */

import { getTenantScopedKey } from './tenantStorage'
import { credentialGet, credentialRemove } from '@/platform/credentialStore'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas`

// ==================== 租户公开信息（无需认证） ====================

export async function getTenantPublicInfo(tenantId: string): Promise<{
  success: boolean
  tenant?: {
    tenant_id: string
    company_name: string
    status: string  // 新增
    status_display?: string  // 新增
  }
  expire_info?: {
    is_expired: boolean
    expire_date: string | null
    days_remaining: number | null
    show_warning: boolean
  }
  message?: string
}> {
  const res = await fetch(`${API_BASE}/auth/tenant/${encodeURIComponent(tenantId)}`)
  if (!res.ok) throw new Error('获取租户信息失败')
  return res.json()
}

// ==================== 认证 ====================

export interface AdminPasswordLoginRequest {
  identifier: string
  password: string
  captcha_code: string
  captcha_id: string
  tenant_id?: string
  required_role?: string
}

export async function adminPasswordLogin(request: AdminPasswordLoginRequest): Promise<{
  success: boolean
  token?: string
  user?: { user_id: string; phone: string; username: string; role: string }
  tenant?: { tenant_id: string; company_name: string; plan: string; status: string; expire_at?: string }
  message?: string
  expire_warning?: string
}> {
  const res = await fetch(`${API_BASE}/auth/password_login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request)
  })
  if (!res.ok) throw new Error('登录失败')
  return res.json()
}

export async function adminLogout(): Promise<void> {
  // 直接按当前路由清理 localStorage 并通知后端登出
  // 注意：不能调用 useTenantAuth().logout()，因为后者会回调 adminLogout()，形成无限递归
  const path = window.location.pathname
  let tokenKey: string
  let adminKey: string
  let tenantKey: string
  if (import.meta.env.VITE_DESKTOP_TARGET !== 'true' && path.startsWith('/portal')) {
    tokenKey = 'portal_token'
    adminKey = 'portal_admin'
    tenantKey = 'portal_tenant'
  } else if (path.startsWith('/t/')) {
    tokenKey = getTenantScopedKey('saas_token')
    adminKey = getTenantScopedKey('saas_admin')
    tenantKey = getTenantScopedKey('saas_tenant')
  } else {
    tokenKey = 'saas_token'
    adminKey = 'saas_admin'
    tenantKey = 'saas_tenant'
  }

  const token = credentialGet(tokenKey)
  if (token) {
    try {
      await fetch(`${API_BASE}/auth/admin_logout`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
    } catch (e) {
      console.warn('admin_logout 调用失败，继续清理本地状态:', e)
    }
  }
  await credentialRemove(adminKey)
  await credentialRemove(tenantKey)
  await credentialRemove(tokenKey)
}

// ==================== 租户信息 ====================

export async function getTenantInfo(): Promise<any> {
  const res = await fetch(`${API_BASE}/tenants/me`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取租户信息失败')
  return res.json()
}

export async function updateTenantInfo(data: { company_name?: string; contact_name?: string; contact_phone?: string }): Promise<any> {
  const res = await fetch(`${API_BASE}/tenants/me`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('更新租户信息失败')
  return res.json()
}

export async function listTenants(params?: {
  page?: number
  page_size?: number
}): Promise<{ success: boolean; tenants: any[]; total?: number; page?: number; page_size?: number }> {
  const sp = new URLSearchParams()
  if (params?.page) sp.append('page', String(params.page))
  if (params?.page_size) sp.append('page_size', String(params.page_size))
  const qs = sp.toString()
  const url = `${API_BASE}/tenants/list_tenants${qs ? '?' + qs : ''}`
  const res = await fetch(url, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取租户列表失败')
  return res.json()
}

// ==================== 租户管理（平台管理员） ====================

export interface TenantFormData {
  company_name: string
  tenant_code: string
  contact_name?: string
  contact_phone?: string
  initial_admin_name?: string
  initial_admin_phone?: string
  plan?: string
  max_instances?: number
  max_users?: number
  expire_at?: string
}

export async function createTenant(data: TenantFormData): Promise<{
  success: boolean
  tenant?: any
  message?: string
  admin_account?: { phone: string; username: string }
  error?: string
  debug?: string
}> {
  const res = await fetch(`${API_BASE}/tenants/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  const result = await res.json()
  if (!res.ok || !result.success) {
    throw new Error(result.error || result.debug || '创建租户失败')
  }
  return result
}

export async function updateTenant(tenantId: string, data: Partial<TenantFormData & { status?: string }>): Promise<{
  success: boolean
  tenant?: any
  message?: string
  admin_account?: { phone: string; username: string }
  error?: string
  debug?: string
}> {
  const res = await fetch(`${API_BASE}/tenants/${encodeURIComponent(tenantId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  const result = await res.json()
  if (!res.ok || !result.success) {
    throw new Error(result.error || result.debug || '更新租户失败')
  }
  return result
}

export async function deleteTenant(tenantId: string): Promise<{ success: boolean; message?: string; error?: string; debug?: string }> {
  const res = await fetch(`${API_BASE}/tenants/${encodeURIComponent(tenantId)}`, {
    method: 'DELETE',
    headers: getSaasAuthHeader()
  })
  const result = await res.json()
  if (!res.ok || !result.success) {
    throw new Error(result.error || result.debug || '删除租户失败')
  }
  return result
}

// ==================== 智能体实例 ====================

export async function listInstances(): Promise<{ success: boolean; instances: any[] }> {
  const res = await fetch(`${API_BASE}/instances`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取实例列表失败')
  return res.json()
}

export async function createInstance(data: {
  subagent_type: string; display_name: string; plan: string; billing_cycle: string
  config?: any; bound_channel_type?: string; allowed_skills?: string[]
}): Promise<any> {
  const res = await fetch(`${API_BASE}/instances`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('创建实例失败')
  return res.json()
}

export async function updateInstance(instanceId: string, data: Record<string, any>): Promise<{ success: boolean; instance?: any }> {
  const res = await fetch(`${API_BASE}/instances/${instanceId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('更新实例失败')
  return res.json()
}

export async function startInstance(instanceId: string): Promise<{ success: boolean; message?: string }> {
  const res = await fetch(`${API_BASE}/instances/${instanceId}/start`, {
    method: 'POST',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('启动实例失败')
  return res.json()
}

export async function stopInstance(instanceId: string): Promise<{ success: boolean; message?: string }> {
  const res = await fetch(`${API_BASE}/instances/${instanceId}/stop`, {
    method: 'POST',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('停止实例失败')
  return res.json()
}

export async function deleteInstance(instanceId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/instances/${instanceId}`, {
    method: 'DELETE',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('删除实例失败')
  return res.json()
}

// ==================== 渠道管理 ====================

export async function listChannels(): Promise<{ success: boolean; channels: any[] }> {
  const res = await fetch(`${API_BASE}/channels`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取渠道列表失败')
  return res.json()
}

export async function createChannel(data: { channel_type: string; name?: string; config: Record<string, string>; subagent_type?: string }): Promise<any> {
  const res = await fetch(`${API_BASE}/channels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('创建渠道失败')
  return res.json()
}

export async function updateChannel(configId: string, data: { name?: string; config: Record<string, string>; subagent_type?: string }): Promise<any> {
  const res = await fetch(`${API_BASE}/channels/${configId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('更新渠道失败')
  return res.json()
}

export async function deleteChannel(configId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/channels/${configId}`, {
    method: 'DELETE',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('删除渠道失败')
  return res.json()
}

export async function getAvailableSubagents(opts?: {
  /**
   * 平台管理员代管理时手动指定目标租户（平台后台路径 /portal/* 不会自动注入 X-Tenant-Id）。
   * 不传则走 getSaasAuthHeader 的默认逻辑（/t/* 路径自动从 URL 提取）。
   */
  tenantId?: string
}): Promise<{ success: boolean; subagents: string[] }> {
  const headers: Record<string, string> = { ...getSaasAuthHeader() }
  if (opts?.tenantId) {
    headers['X-Tenant-Id'] = opts.tenantId
  }
  const res = await fetch(`${API_BASE}/channels/available-subagents`, {
    headers
  })
  if (!res.ok) throw new Error('获取数字员工列表失败')
  return res.json()
}

export async function verifyChannel(configId: string): Promise<{ success: boolean; message?: string; verified?: boolean }> {
  const res = await fetch(`${API_BASE}/channels/${configId}/verify`, {
    method: 'POST',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('验证渠道失败')
  return res.json()
}

/**
 * 为 wecom_personal_rpa 渠道生成 RSA 密钥对。
 *
 * 后端会在服务端生成 2048bit RSA 密钥对，私钥 Fernet 加密入库（不出 API），公钥返回前端展示。
 * 若已有私钥，会被覆盖（用户主动点生成就是想换）。
 *
 * @returns public_key 为 JSON 转义版本（\n 字面量），public_key_raw 为原始 PEM 文本
 */
export async function generateChannelKeypair(configId: string): Promise<{
  success: boolean
  public_key?: string
  public_key_raw?: string
  message?: string
}> {
  const res = await fetch(`${API_BASE}/channels/${configId}/generate-keypair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
  })
  if (!res.ok) throw new Error('生成密钥对失败')
  return res.json()
}

// ==================== 用户管理 ====================

export async function listTenantUsers(): Promise<{ success: boolean; users: any[] }> {
  const res = await fetch(`${API_BASE}/users`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用户列表失败')
  return res.json()
}

export async function createTenantUser(data: { phone: string; username: string; department?: string; role?: string; tenant_id?: string }): Promise<any> {
  const res = await fetch(`${API_BASE}/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  const result = await res.json()
  if (!res.ok || !result.success) {
    throw new Error(result.error || result.debug || '创建用户失败')
  }
  return result
}

export async function batchImportUsers(file: File): Promise<{
  success: boolean; imported: number; total: number; errors?: string[]
}> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}/users/batch_import_users`, {
    method: 'POST',
    headers: getSaasAuthHeader(),
    body: formData
  })
  const result = await res.json()
  if (!res.ok || !result.success) {
    throw new Error(result.error || result.debug || '批量导入失败')
  }
  return result
}

export async function removeTenantUser(userId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/users/${userId}`, {
    method: 'DELETE',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('删除用户失败')
  return res.json()
}

export async function updateTenantUser(userId: string, data: { username?: string; role?: string }): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/users/${userId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('更新用户信息失败')
  return res.json()
}

// ==================== Skill 管理 ====================

export async function listSkills(): Promise<{ success: boolean; skills: any[] }> {
  const res = await fetch(`${API_BASE}/skills`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取 Skill 列表失败')
  return res.json()
}

export async function uploadSkill(data: { name: string; content: string }): Promise<{ success: boolean; message?: string }> {
  const res = await fetch(`${API_BASE}/skills`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('上传 Skill 失败')
  return res.json()
}

export async function updateSkill(skillName: string, data: { content: string }): Promise<{ success: boolean; message?: string }> {
  const res = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('更新 Skill 失败')
  return res.json()
}

export async function deleteSkill(skillName: string): Promise<{ success: boolean; message?: string }> {
  const res = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillName)}`, {
    method: 'DELETE',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('删除 Skill 失败')
  return res.json()
}

// ==================== 用量报告 ====================

export async function getUsageSummary(period: string = 'month'): Promise<any> {
  const res = await fetch(`${API_BASE}/reports/get_usage_summary?period=${period}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用量摘要失败')
  return res.json()
}

export async function getTokenDetail(days: number = 30): Promise<{
  success: boolean; start_date: string; end_date: string;
  trend: { date: string; tokens: number; input_tokens: number; output_tokens: number; cached_tokens: number; sessions: number; conversations: number }[]
}> {
  const res = await fetch(`${API_BASE}/reports/tokens/detail?days=${days}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取 Token 明细失败')
  return res.json()
}

export async function getModelUsage(days: number = 30): Promise<{
  success: boolean; start_date: string; end_date: string;
  models: { model: string; provider: string; conversation_count: number; total_tokens: number; input_tokens: number; output_tokens: number; cached_tokens: number; total_duration_ms: number; avg_iterations: number }[]
}> {
  const res = await fetch(`${API_BASE}/reports/models?days=${days}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取模型用量失败')
  return res.json()
}

export async function getUserUsage(days: number = 30): Promise<{
  success: boolean; users: { user_id: string; username: string; total_tokens: number; input_tokens: number; output_tokens: number; cached_tokens: number; total_sessions: number; total_conversations: number; avg_tokens_per_session: number; last_active: string }[]
}> {
  const res = await fetch(`${API_BASE}/reports/users?days=${days}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用户用量失败')
  return res.json()
}

export async function exportReport(days: number = 30): Promise<any> {
  const res = await fetch(`${API_BASE}/reports/export_usage_report?days=${days}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('导出报告失败')
  return res.json()
}

/**
 * 获取租户Token消耗明细报表
 * @param month 月份，格式 YYYY-MM
 * @param page 页码，从1开始
 * @param pageSize 每页记录数，默认100
 * @returns 租户Token消耗明细数据
 */
export async function getTenantTokenDetails(month: string, page: number = 1, pageSize: number = 100): Promise<any> {
  const params = new URLSearchParams({
    month,
    page: page.toString(),
    page_size: pageSize.toString()
  })
  const res = await fetch(`${API_BASE}/reports/token-details?${params}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取Token消耗明细失败')
  return res.json()
}

// ==================== 工具函数 ====================

// 根据当前路由获取对应的 token key（租户前台按 tenant_id 隔离，portal 共用，演示模式保持）
function getTokenKey(): string {
  return getTenantScopedKey('saas_token')
}

// 获取当前 tenant_id（从 URL 路径 /t/:tenant_id 中提取）
function getCurrentTenantId(): string | null {
  const path = window.location.pathname
  const match = path.match(/^\/t\/([^/]+)/)
  return match ? match[1] : null
}

export function getSaasAuthHeader(): Record<string, string> {
  const headers: Record<string, string> = {}

  // 根据路由获取对应的 token
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  // 从 URL 路径获取 tenant_id，添加到 Header
  const tenantId = getCurrentTenantId()
  if (tenantId) {
    headers['X-Tenant-Id'] = tenantId
  }

  return headers
}
