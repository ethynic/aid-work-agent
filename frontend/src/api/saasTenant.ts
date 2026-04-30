/**
 * SaaS 租户管理 API Client
 * 所有 /api/saas/* 调用的统一封装
 * 使用独立的 saas_token（与演示模式 demo_token 分离）
 */

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas`

// ==================== 租户公开信息（无需认证） ====================

export async function getTenantPublicInfo(tenantId: string): Promise<{
  success: boolean
  tenant?: { tenant_id: string; company_name: string }
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

export async function sendAdminSmsCode(phone: string): Promise<{ success: boolean; message?: string; expires_in?: number }> {
  const res = await fetch(`${API_BASE}/auth/sms/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone })
  })
  if (!res.ok) throw new Error('发送验证码失败')
  return res.json()
}

export async function adminLogin(phone: string, code: string): Promise<{
  success: boolean
  token?: string
  user?: { user_id: string; phone: string; username: string; role: string }
  tenant?: { tenant_id: string; company_name: string; plan: string; status: string }
  message?: string
}> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone, code })
  })
  if (!res.ok) throw new Error('登录失败')
  return res.json()
}

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
  const res = await fetch(`${API_BASE}/auth/login/password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request)
  })
  if (!res.ok) throw new Error('登录失败')
  return res.json()
}

export async function adminLogout(): Promise<void> {
  const path = window.location.pathname
  let tokenKey: string
  if (path.startsWith('/portal')) {
    tokenKey = 'portal_token'
  } else if (path.startsWith('/t/')) {
    tokenKey = 'saas_token'
  } else {
    tokenKey = 'saas_token'
  }

  const adminKey = tokenKey.replace('token', 'admin')
  const tenantKey = tokenKey.replace('token', 'tenant')

  const token = localStorage.getItem(tokenKey)
  if (token) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` }
    })
  }
  localStorage.removeItem(tokenKey)
  localStorage.removeItem(adminKey)
  localStorage.removeItem(tenantKey)
}

export async function getAdminInfo(tenantId?: string): Promise<{
  user?: { user_id: string; phone: string; username: string; role: string }
  tenant?: { tenant_id: string; company_name: string; plan: string; status: string }
} | null> {
  const path = window.location.pathname
  let tokenKey: string
  if (path.startsWith('/portal')) {
    tokenKey = 'portal_token'
  } else if (path.startsWith('/t/')) {
    tokenKey = 'saas_token'
  } else {
    tokenKey = 'saas_token'
  }

  const token = localStorage.getItem(tokenKey)
  if (!token) return null
  const url = tenantId ? `${API_BASE}/auth/me?tenant_id=${encodeURIComponent(tenantId)}` : `${API_BASE}/auth/me`
  const res = await fetch(url, {
    headers: { 'Authorization': `Bearer ${token}` }
  })
  if (!res.ok) return null
  return res.json()
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

export async function getTenantStats(): Promise<{
  success: boolean
  stats: { total_instances: number; active_instances: number; total_users: number; total_tokens_used: number }
}> {
  const res = await fetch(`${API_BASE}/tenants/me/stats`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取统计数据失败')
  return res.json()
}

export async function listTenants(): Promise<{ success: boolean; tenants: any[] }> {
  const res = await fetch(`${API_BASE}/tenants/list`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取租户列表失败')
  return res.json()
}

// ==================== 租户管理（平台管理员） ====================

export interface TenantFormData {
  company_name: string
  contact_name?: string
  contact_phone?: string
  initial_admin_name?: string
  initial_admin_phone?: string
  plan?: string
  max_instances?: number
  max_users?: number
  expire_at?: string
}

export async function getTenantById(tenantId: string): Promise<{ success: boolean; tenant?: any; error?: string; debug?: string }> {
  const res = await fetch(`${API_BASE}/tenants/${encodeURIComponent(tenantId)}`, {
    headers: getSaasAuthHeader()
  })
  const data = await res.json()
  if (!res.ok) throw new Error(data.error || '获取租户详情失败')
  return data
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

export async function createChannel(data: { channel_type: string; config: Record<string, string> }): Promise<any> {
  const res = await fetch(`${API_BASE}/channels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('创建渠道失败')
  return res.json()
}

export async function updateChannel(configId: string, data: { config: Record<string, string> }): Promise<any> {
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

export async function verifyChannel(configId: string): Promise<{ success: boolean; message?: string; verified?: boolean }> {
  const res = await fetch(`${API_BASE}/channels/${configId}/verify`, {
    method: 'POST',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('验证渠道失败')
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
  const res = await fetch(`${API_BASE}/users/batch`, {
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
  const res = await fetch(`${API_BASE}/reports/summary?period=${period}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用量摘要失败')
  return res.json()
}

export async function getTokenTrend(days: number = 30): Promise<{
  success: boolean; start_date: string; end_date: string; trend: { date: string; tokens: number }[]
}> {
  const res = await fetch(`${API_BASE}/reports/tokens?days=${days}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取 Token 趋势失败')
  return res.json()
}

export async function getUserUsage(days: number = 30): Promise<{
  success: boolean; users: { user_id: string; username: string; total_tokens: number; total_sessions: number; avg_tokens_per_session: number }[]
}> {
  const res = await fetch(`${API_BASE}/reports/users?days=${days}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用户用量失败')
  return res.json()
}

export async function exportReport(days: number = 30): Promise<any> {
  const res = await fetch(`${API_BASE}/reports/export?days=${days}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('导出报告失败')
  return res.json()
}

// ==================== 计费 ====================

export async function getPlans(): Promise<{
  success: boolean; plans: { name: string; display_name: string; price: number; token_quota: number; max_instances: number; max_users: number }[]
}> {
  const res = await fetch(`${API_BASE}/billing/plans`)
  if (!res.ok) throw new Error('获取套餐列表失败')
  return res.json()
}

export async function listSubscriptions(): Promise<{ success: boolean; subscriptions: any[] }> {
  const res = await fetch(`${API_BASE}/billing/subscriptions`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取订阅列表失败')
  return res.json()
}

export async function createSubscription(data: { plan: string; billing_cycle: string; subagent_type?: string }): Promise<any> {
  const res = await fetch(`${API_BASE}/billing/subscriptions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('创建订阅失败')
  return res.json()
}

export async function payOrder(orderId: string, paymentMethod: string = 'wechat'): Promise<{
  success: boolean; order_id: string; amount: number; payment_method: string; payment_url?: string
}> {
  const res = await fetch(`${API_BASE}/billing/pay/${orderId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify({ payment_method: paymentMethod })
  })
  if (!res.ok) throw new Error('支付失败')
  return res.json()
}

export async function getUsage(): Promise<{
  success: boolean; usage: { subscription_id: string; plan_name: string; token_quota: number; tokens_used: number; tokens_remaining: number; usage_percentage: number }[]
}> {
  const res = await fetch(`${API_BASE}/billing/usage`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用量信息失败')
  return res.json()
}

// ==================== 工具函数 ====================

// 根据当前路由获取对应的 token key
function getTokenKey(): string {
  const path = window.location.pathname
  if (path.startsWith('/portal')) {
    return 'portal_token'
  } else if (path.startsWith('/t/')) {
    return 'saas_token'
  }
  return 'saas_token'
}

// 获取当前 tenant_id（从 URL 路径 /t/:tenant_id 中提取）
function getCurrentTenantId(): string | null {
  const path = window.location.pathname
  const match = path.match(/^\/t\/([^/]+)/)
  return match ? match[1] : null
}

function getSaasAuthHeader(): Record<string, string> {
  const headers: Record<string, string> = {}

  // 根据路由获取对应的 token
  const tokenKey = getTokenKey()
  const token = localStorage.getItem(tokenKey)
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
