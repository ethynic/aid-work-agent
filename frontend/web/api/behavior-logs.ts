/**
 * 用户行为审计日志查询 API（Phase 4）
 *
 * - listBehaviorLogs：租户视角（/api/admin/behavior-logs，租户管理员/平台管理员代租户）
 * - listBehaviorLogsPlatform：平台全局视角（/api/saas/behavior-logs，仅平台管理员）
 */

import { getAuthHeader } from '@/api/auth'
import { getSaasAuthHeader } from '@/api/saasTenant'

const API_BASE = import.meta.env.VITE_API_BASE || ''

/** 行为日志条目（user_behavior_logs 完整行） */
export interface BehaviorLogItem {
  id: number
  tenant_id: string | null
  user_id: string | null
  user_role: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  resource_name: string | null
  detail: Record<string, unknown> | null
  client_ip: string | null
  user_agent: string | null
  success: boolean
  error_msg: string | null
  entry: string | null
  login_method: string | null
  channel: string | null
  channel_user_id: string | null
  token_id: string | null
  request_id: string | null
  http_method: string | null
  path: string | null
  device_type: string | null
  device_info: string | null
  created_at: string
}

export interface BehaviorLogListResponse {
  success: boolean
  data: BehaviorLogItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
  message?: string
}

export interface BehaviorLogQueryParams {
  action?: string
  resource_type?: string
  success?: string      // 'true' / 'false' / ''（空为全部）
  keyword?: string
  time_range?: string   // today / 7d / 30d / ''（空为全部）
  tenant_id?: string    // 仅平台视角有效
  page?: number
  page_size?: number
}

/** 查询参数 -> URL query（空值不拼） */
function buildQuery(params: BehaviorLogQueryParams): string {
  const search = new URLSearchParams()
  if (params.action) search.set('action', params.action)
  if (params.resource_type) search.set('resource_type', params.resource_type)
  if (params.success === 'true' || params.success === 'false') search.set('success', params.success)
  if (params.keyword) search.set('keyword', params.keyword)
  if (params.time_range) search.set('time_range', params.time_range)
  if (params.tenant_id) search.set('tenant_id', params.tenant_id)
  search.set('page', String(params.page ?? 1))
  search.set('page_size', String(params.page_size ?? 20))
  return search.toString()
}

/**
 * 租户视角查询行为日志（租户由后端从 X-Tenant-Id / token 解析）
 */
export async function listBehaviorLogs(
  params: BehaviorLogQueryParams = {}
): Promise<BehaviorLogListResponse> {
  const response = await fetch(`${API_BASE}/api/admin/behavior-logs?${buildQuery(params)}`, {
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() }
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }
  return response.json()
}

/**
 * 平台全局视角查询行为日志（可按 tenant_id 筛选）
 */
export async function listBehaviorLogsPlatform(
  params: BehaviorLogQueryParams = {}
): Promise<BehaviorLogListResponse> {
  const response = await fetch(`${API_BASE}/api/saas/behavior-logs?${buildQuery(params)}`, {
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() }
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }
  return response.json()
}
