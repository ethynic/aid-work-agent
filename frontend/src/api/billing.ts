/**
 * 租户积分计费 API 封装（#37 租户积分充值与计费）
 *
 * - 平台管理员侧：/api/saas/billing/recharges/* （充值 CRUD + 汇总）
 * - 租户侧：/api/saas/billing/balance|usage|recharges（余额/用量/充值记录只读）
 *
 * 平台管理员调用时需手动注入 X-Tenant-Id（用于 /portal 路径下代管理场景）；
 * 租户侧调用走 getAuthHeader()（来自 @/api/auth），自动注入 X-Tenant-Id。
 */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/billing`

// ==================== 类型 ====================

export interface RechargeItem {
  id: number
  tenant_id: string
  tenant_name?: string
  amount_yuan: number
  credits: number
  rate: number
  source: string
  payment_order_id?: string | null
  operator_id?: string | null
  operator_name?: string | null
  remark?: string | null
  created_at: string
}

export interface RechargeListResponse {
  success: boolean
  items?: RechargeItem[]
  total?: number
  page?: number
  page_size?: number
  message?: string
  debug?: string
}

export interface RechargeCreateRequest {
  tenant_id: string
  amount_yuan: number
  credits: number
  created_at?: string
  remark?: string
}

export interface RechargeCreateResponse {
  success: boolean
  recharge?: RechargeItem
  message?: string
  debug?: string
}

export interface RechargeStats {
  total_amount_yuan: number
  total_credits: number
  total_count: number
  recent_7d_trend: Array<{
    date: string
    amount_yuan: number
    credits: number
    count: number
  }>
}

export interface RechargeStatsResponse {
  success: boolean
  stats?: RechargeStats
  message?: string
}

export interface BalanceInfo {
  credit_balance: number
  daily_avg_cost_7d: number
  estimated_days_left: number | null
}

export interface BalanceResponse {
  success: boolean
  balance?: BalanceInfo
  message?: string
}

export interface UsageItem {
  date: string
  credit_cost: number
  session_count: number
  message_count: number
}

export interface UsageSummary {
  total_credit_cost: number
  total_session_count: number
  total_message_count: number
}

export interface UsageResponse {
  success: boolean
  items?: UsageItem[]
  total?: number
  page?: number
  page_size?: number
  summary?: UsageSummary
  message?: string
}

// ==================== 平台管理员：充值管理 ====================

/**
 * 获取平台管理员认证头
 * /portal 路径下 getAuthHeader 会自动注入 portal_token，但不带 X-Tenant-Id
 * （平台管理员代管理场景需手动指定 tenantId 参数）
 */
function getPlatformAuthHeader(tenantId?: string): Record<string, string> {
  const headers: Record<string, string> = { ...getAuthHeader() }
  if (tenantId) {
    headers['X-Tenant-Id'] = tenantId
  }
  return headers
}

export async function listRecharges(params: {
  tenant_id?: string
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}): Promise<RechargeListResponse> {
  const sp = new URLSearchParams()
  if (params.tenant_id) sp.append('tenant_id', params.tenant_id)
  if (params.date_from) sp.append('date_from', params.date_from)
  if (params.date_to) sp.append('date_to', params.date_to)
  if (params.page) sp.append('page', String(params.page))
  if (params.page_size) sp.append('page_size', String(params.page_size))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/recharges/list${qs ? '?' + qs : ''}`, {
    headers: getPlatformAuthHeader()
  })
  if (!res.ok) throw new Error('获取充值记录列表失败')
  return res.json()
}

export async function createRecharge(req: RechargeCreateRequest): Promise<RechargeCreateResponse> {
  const res = await fetch(`${API_BASE}/recharges/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getPlatformAuthHeader() },
    body: JSON.stringify(req)
  })
  if (!res.ok) throw new Error('创建充值记录失败')
  return res.json()
}

export async function deleteRecharge(rechargeId: number): Promise<{ success: boolean; message?: string }> {
  const res = await fetch(`${API_BASE}/recharges/${rechargeId}`, {
    method: 'DELETE',
    headers: getPlatformAuthHeader()
  })
  if (!res.ok) throw new Error('删除充值记录失败')
  return res.json()
}

export async function getRechargeStats(tenantId?: string): Promise<RechargeStatsResponse> {
  const qs = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ''
  const res = await fetch(`${API_BASE}/recharges/stats${qs}`, {
    headers: getPlatformAuthHeader(tenantId)
  })
  if (!res.ok) throw new Error('获取充值汇总失败')
  return res.json()
}

// ==================== 租户侧：余额/用量/充值记录 ====================

export async function getTenantBalance(): Promise<BalanceResponse> {
  const res = await fetch(`${API_BASE}/balance`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取积分余额失败')
  return res.json()
}

export async function getTenantUsage(params: {
  date_from?: string
  date_to?: string
  session_id?: string
  model?: string
  page?: number
  page_size?: number
}): Promise<UsageResponse> {
  const sp = new URLSearchParams()
  if (params.date_from) sp.append('date_from', params.date_from)
  if (params.date_to) sp.append('date_to', params.date_to)
  if (params.session_id) sp.append('session_id', params.session_id)
  if (params.model) sp.append('model', params.model)
  if (params.page) sp.append('page', String(params.page))
  if (params.page_size) sp.append('page_size', String(params.page_size))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/usage${qs ? '?' + qs : ''}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取用量明细失败')
  return res.json()
}

export async function listTenantRecharges(params: {
  page?: number
  page_size?: number
}): Promise<RechargeListResponse> {
  const sp = new URLSearchParams()
  if (params.page) sp.append('page', String(params.page))
  if (params.page_size) sp.append('page_size', String(params.page_size))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/recharges${qs ? '?' + qs : ''}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取充值记录失败')
  return res.json()
}

// ==================== 平台管理员：每日用量明细下钻 ====================

export interface DailyUsageDetailItem {
  record_id: string
  session_id: string
  session_title: string
  user_display: string
  source_type: string
  user_message: string
  assistant_message: string
  prompt_tokens: number
  cached_input_tokens: number
  completion_tokens: number
  credit_cost: number
  created_at: string
}

export interface DailyUsageDetailResponse {
  success: boolean
  date?: string
  items?: DailyUsageDetailItem[]
  total?: number
  page?: number
  page_size?: number
  message?: string
  debug?: string
}

/**
 * 获取某日 chat_records 明细（仅平台管理员可访问）
 * 平台管理员需通过 X-Tenant-Id 代管理目标租户（getAuthHeader 会自动注入）
 */
export async function getDailyUsageDetail(params: {
  date: string
  page?: number
  page_size?: number
}): Promise<DailyUsageDetailResponse> {
  const sp = new URLSearchParams()
  sp.append('date', params.date)
  if (params.page) sp.append('page', String(params.page))
  if (params.page_size) sp.append('page_size', String(params.page_size))
  const res = await fetch(`${API_BASE}/usage/daily-detail?${sp.toString()}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取对话用量明细失败')
  return res.json()
}
