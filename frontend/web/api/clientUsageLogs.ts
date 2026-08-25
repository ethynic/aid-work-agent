/**
 * 协会客户端运行日志 API（平台管理员用，跨租户）
 *
 * 后端：/api/saas/client-usage-logs（src/saas/api/client_usage_mgmt.py）
 * 数据来自 client_usage_logs 表，同表含 LLM 计费行 + 客户端遥测/日志行。
 *
 * /portal 路径下 getAuthHeader() 自动注入 portal_token；跨租户浏览不带 X-Tenant-Id。
 */
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/client-usage-logs`

// ==================== 类型 ====================

export interface ClientUsageLogItem {
  id: number
  tenant_id: string
  tenant_name?: string
  binding_id: string
  session_id?: string | null
  association_name?: string | null
  stage?: string | null
  status?: string | null
  model?: string | null
  provider?: string | null
  total_tokens?: number
  raw_credit_cost?: number
  credit_cost?: number
  error_code?: string | null
  detail?: any
  created_at: string
}

export interface ClientUsageLogListResponse {
  success: boolean
  items?: ClientUsageLogItem[]
  total?: number
  page?: number
  page_size?: number
  message?: string
  debug?: string
}

export interface ClientUsageLogQuery {
  tenant_id?: string
  binding_id?: string
  session_id?: string
  stage?: string
  status?: string
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}

// ==================== API ====================

export async function listClientUsageLogs(params: ClientUsageLogQuery): Promise<ClientUsageLogListResponse> {
  const sp = new URLSearchParams()
  if (params.tenant_id) sp.append('tenant_id', params.tenant_id)
  if (params.binding_id) sp.append('binding_id', params.binding_id)
  if (params.session_id) sp.append('session_id', params.session_id)
  if (params.stage) sp.append('stage', params.stage)
  if (params.status) sp.append('status', params.status)
  if (params.date_from) sp.append('date_from', params.date_from)
  if (params.date_to) sp.append('date_to', params.date_to)
  if (params.page) sp.append('page', String(params.page))
  if (params.page_size) sp.append('page_size', String(params.page_size))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/list${qs ? '?' + qs : ''}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取客户端运行日志失败')
  return res.json()
}

export async function getRecentClientErrors(params: {
  hours?: number
  limit?: number
  tenant_id?: string
}): Promise<ClientUsageLogListResponse> {
  const sp = new URLSearchParams()
  if (params.hours) sp.append('hours', String(params.hours))
  if (params.limit) sp.append('limit', String(params.limit))
  if (params.tenant_id) sp.append('tenant_id', params.tenant_id)
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/recent-errors${qs ? '?' + qs : ''}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取客户端近期错误失败')
  return res.json()
}
