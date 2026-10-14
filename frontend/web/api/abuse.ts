/**
 * 防滥用中心 API Client
 * 覆盖 /api/saas/abuse/* 与用户停用/启用（/api/saas/users/{id}/status）
 */

import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas`

// ==================== 类型 ====================

export interface AbuseRuleParamField {
  key: string
  label: string
  type: 'enum' | 'number'
  options?: string[]
  default?: string | number | null
  required?: boolean
  min?: number
  /** 大于 1 表示未来阶段的预留参数，编辑器不展示 */
  phase?: number
  description?: string
}

export interface AbuseRule {
  rule_id: string
  name: string
  type: 'hard' | 'soft'
  description: string
  params_schema: AbuseRuleParamField[]
  default_params: Record<string, string | number>
}

export interface AbusePolicyEntry {
  rule_id: string
  params: Record<string, string | number>
}

export interface AbusePolicyData {
  success: boolean
  exists: boolean
  tenant_id?: string
  scope_type: string
  scope_id: string
  mode: 'observe' | 'enforce'
  policies: AbusePolicyEntry[]
  is_platform_default?: boolean
  updated_by?: string
  updated_at?: string
}

export interface AbuseEvent {
  id: number
  tenant_id: string
  scope_type: string
  scope_id: string
  user_id: string | null
  rule_id: string
  action: string
  session_id: string | null
  channel_type: string | null
  detail: Record<string, any> | null
  created_at: string
}

export interface AbuseUsageSummary {
  user_id: string
  username: string
  nickname: string
  channel_source: string | null
  user_status: string | null
  total_credits: number
  request_count: number
  last_request_at: string
}

export interface AbuseUsageDetail {
  id: number
  session_id: string
  user_message: string
  assistant_message: string | null
  credit_cost: number
  model: string | null
  source_type: string
  created_at: string
}

export interface AbuseSuspendedUser {
  user_id: string
  username: string | null
  nickname: string | null
  phone: string | null
  role: string
  status: string
  source: string | null
  created_at: string
  updated_at: string
}

// ==================== 规则目录 ====================

export async function getAbusePresets(): Promise<{ success: boolean; rules: AbuseRule[] }> {
  const res = await fetch(`${API_BASE}/abuse/presets`, { headers: getSaasAuthHeader() })
  if (!res.ok) throw new Error('获取规则目录失败')
  return res.json()
}

// ==================== 绑定读写 ====================

export async function getAbusePolicies(
  scopeType: 'tenant' | 'user' | 'channel',
  scopeId?: string
): Promise<AbusePolicyData> {
  const params = new URLSearchParams({ scope_type: scopeType })
  if (scopeId) params.set('scope_id', scopeId)
  const res = await fetch(`${API_BASE}/abuse/policies?${params}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取防滥用绑定失败')
  return res.json()
}

export async function putAbusePolicies(
  scopeType: 'tenant' | 'user' | 'channel',
  scopeId: string | undefined,
  data: { mode: 'observe' | 'enforce'; policies: AbusePolicyEntry[] }
): Promise<{ success: boolean; detail?: string }> {
  const params = new URLSearchParams({ scope_type: scopeType })
  if (scopeId) params.set('scope_id', scopeId)
  const res = await fetch(`${API_BASE}/abuse/policies?${params}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data)
  })
  const result = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(result.detail || '保存防滥用绑定失败')
  }
  return result
}

// ==================== 触发记录 ====================

export async function listAbuseEvents(params: {
  scope_type?: string
  scope_id?: string
  user_id?: string
  page?: number
  page_size?: number
}): Promise<{ success: boolean; items: AbuseEvent[]; total: number }> {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  })
  const res = await fetch(`${API_BASE}/abuse/events?${qs}`, { headers: getSaasAuthHeader() })
  if (!res.ok) throw new Error('获取触发记录失败')
  return res.json()
}

// ==================== 用量监控 ====================

export async function getAbuseUsageSummary(
  window: 'hour' | 'day'
): Promise<{ success: boolean; window: string; items: AbuseUsageSummary[] }> {
  const res = await fetch(`${API_BASE}/abuse/usage?window=${window}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用量汇总失败')
  return res.json()
}

export async function getAbuseUsageDetail(
  window: 'hour' | 'day',
  userId: string
): Promise<{
  success: boolean
  window: string
  user_id: string
  total_credits: number
  items: AbuseUsageDetail[]
}> {
  const res = await fetch(
    `${API_BASE}/abuse/usage?window=${window}&user_id=${encodeURIComponent(userId)}`,
    { headers: getSaasAuthHeader() }
  )
  if (!res.ok) throw new Error('获取用量明细失败')
  return res.json()
}

// ==================== 用户停用/启用（复用 users 列表 + status 接口） ====================

export async function listSuspendedUsers(params: {
  status?: string
  source?: 'all' | 'channel' | 'internal'
  page?: number
  page_size?: number
}): Promise<{ success: boolean; users: AbuseSuspendedUser[]; total: number }> {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  })
  const res = await fetch(`${API_BASE}/users?${qs}`, { headers: getSaasAuthHeader() })
  if (!res.ok) throw new Error('获取用户列表失败')
  return res.json()
}

export async function updateUserStatus(
  userId: string,
  status: 'active' | 'suspended'
): Promise<{ success: boolean; message?: string }> {
  const res = await fetch(
    `${API_BASE}/users/${encodeURIComponent(userId)}/status`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
      body: JSON.stringify({ status })
    }
  )
  const result = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(result.detail || '更新用户状态失败')
  if (result.success === false) throw new Error(result.message || '更新用户状态失败')
  return result
}
