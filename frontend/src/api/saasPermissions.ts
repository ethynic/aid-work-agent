/**
 * SaaS 数字员工授权 API Client
 * 所有 /api/saas/permissions/* 调用的统一封装
 */

// 复用 saasTenant 的 getSaasAuthHeader
function getTokenKey(): string {
  const path = window.location.pathname
  if (path.startsWith('/portal')) {
    return 'portal_token'
  } else if (path.startsWith('/t/')) {
    return 'saas_token'
  }
  return 'saas_token'
}

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

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/permissions`

export interface AgentItem {
  agent_id: string
  name: string
  description: string
  capabilities: string[]
  type: 'builtin' | 'custom'
  business_pages?: any[]
  // 实例相关字段（租户模式下返回实例时包含）
  instance_id?: string  // 实例ID，租户模式下必填
  display_name?: string  // 显示名称
  instance_name?: string // 实例名称（如"外贸获客智能体 - 实例1"）
  subagent_type?: string // 子智能体类型
  status?: string       // 实例状态：idle/busy/offline
  avatar?: string       // 头像
  [key: string]: any  // 允许其他字段
}

// ==================== 平台管理员 - 租户授权 ====================

export async function getTenantAgentPermissions(tenantId: string): Promise<{
  success: boolean
  data?: { agent_ids: string[]; count: number; agent_quotas?: Record<string, number> }
  message?: string
}> {
  const res = await fetch(`${API_BASE}/tenant/${encodeURIComponent(tenantId)}/agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取租户数字员工授权失败')
  return res.json()
}

export async function setTenantAgentPermissions(tenantId: string, agentIds: string[], agentQuotas: Record<string, number> = {}): Promise<{
  success: boolean
  message?: string
}> {
  const headers = getSaasAuthHeader()
  headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE}/tenant/${encodeURIComponent(tenantId)}/agents`, {
    method: 'POST',
    headers: headers,
    body: JSON.stringify({ agent_ids: agentIds, agent_quotas: agentQuotas }),
  })
  if (!res.ok) throw new Error('设置租户数字员工授权失败')
  return res.json()
}

export async function getAllAvailableAgents(): Promise<{
  success: boolean
  data?: AgentItem[]
}> {
  const res = await fetch(`${API_BASE}/available-agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取所有可用数字员工失败')
  return res.json()
}

// ==================== 租户管理员 - 用户授权 ====================

export async function getUserAgentPermissions(userId: string): Promise<{
  success: boolean
  data?: { agent_ids: string[]; count: number }
  message?: string
}> {
  const res = await fetch(`${API_BASE}/user/${encodeURIComponent(userId)}/agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取用户数字员工授权失败')
  return res.json()
}

export async function setUserAgentPermissions(userId: string, agentIds: string[]): Promise<{
  success: boolean
  message?: string
}> {
  const headers = getSaasAuthHeader()
  headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE}/user/${encodeURIComponent(userId)}/agents`, {
    method: 'POST',
    headers: headers,
    body: JSON.stringify({ agent_ids: agentIds }),
  })
  if (!res.ok) throw new Error('设置用户数字员工授权失败')
  return res.json()
}

export async function getTenantAvailableUserAgents(): Promise<{
  success: boolean
  data?: AgentItem[]
}> {
  const res = await fetch(`${API_BASE}/tenant/available-user-agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取租户可用数字员工失败')
  return res.json()
}

// ==================== 当前用户 - 获取可访问的数字员工 ====================

export async function getMyAllowedAgents(): Promise<{
  success: boolean
  data?: AgentItem[]
  count: number
}> {
  const res = await fetch(`${API_BASE}/my/allowed-agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取当前用户可访问数字员工失败')
  return res.json()
}

// ==================== 实例同步 ====================

export async function syncTenantInstances(tenantId: string): Promise<{
  success: boolean
  message?: string
  created?: number
  deleted?: number
}> {
  const headers = getSaasAuthHeader()
  headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE}/tenant/${encodeURIComponent(tenantId)}/sync-instances`, {
    method: 'POST',
    headers: headers,
  })
  if (!res.ok) throw new Error('同步租户实例失败')
  return res.json()
}
