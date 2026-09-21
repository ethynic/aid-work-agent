/**
 * 宏陶商城产品知识库同步 API Client（P2.2）
 *
 * 租户端：/api/saas/hongtao-shop/*（require_admin；X-Tenant-Id 由 getSaasAuthHeader 自动携带）
 *
 * 排队语义：立即运行只受理入队（同租户串行处理），接口返回 run 供查询进度；
 * 在队去重：已有 queued/running 时返回既有 run（deduped=true）。
 */

import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/hongtao-shop`

// ==================== 类型 ====================

export interface HongtaoSource {
  enabled: boolean
  sync_interval_hours: number
  selection_mode: 'all' | 'ids'
  selected_ids: string[]
  last_sync_at: string | null
  last_error: string | null
}

export interface HongtaoRun {
  id: number
  trigger_type: string
  status: string
  new_count: number | null
  updated_count: number | null
  skipped_count: number | null
  deleted_count: number | null
  restored_count: number | null
  failed_count: number | null
  vl_parsed_count: number | null
  vl_billed_count: number | null
  embedding_tokens: number | null
  credits_charged: number | null
  fetch_complete: boolean | null
  total_reported: number | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface HongtaoRunItem {
  native_id: string
  action: string | null
  status: string | null
  error_code: string | null
  error_message: string | null
  vl_images: number | null
  vl_billed: number | null
  embedding_tokens: number | null
  billing_status: string | null
  credits_charged: number | null
}

export interface HongtaoProduct {
  native_id: string
  name: string | null
  model: string | null
  procode: string | null
  status: string | null
  has_doc: boolean
  user_deleted: boolean
  selected: boolean
  processing_status: string | null
}

export interface SourcePatchPayload {
  enabled?: boolean
  sync_interval_hours?: number
  selection_mode?: 'all' | 'ids'
  selected_ids?: string[]
}

// ==================== 请求 ====================

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...getSaasAuthHeader(),
      ...(init?.headers || {}),
    },
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const message = (data as { detail?: string }).detail || `请求失败（${response.status}）`
    const error = new Error(message) as Error & { status: number }
    error.status = response.status
    throw error
  }
  return data as T
}

export async function getSource(): Promise<{ success: boolean; source: HongtaoSource }> {
  return request('/source')
}

export async function patchSource(payload: SourcePatchPayload): Promise<{ success: boolean; source: HongtaoSource }> {
  return request('/source', { method: 'PATCH', body: JSON.stringify(payload) })
}

export async function triggerSource(): Promise<{
  success: boolean
  run: { run_id: number; status: string; deduped?: boolean }
}> {
  return request('/source/trigger', { method: 'POST' })
}

export async function getRuns(limit = 20, offset = 0): Promise<{
  success: boolean
  runs: HongtaoRun[]
}> {
  return request(`/runs?limit=${limit}&offset=${offset}`)
}

export async function getRun(runId: number): Promise<{
  success: boolean
  run: HongtaoRun
  items: HongtaoRunItem[]
}> {
  return request(`/runs/${runId}`)
}

export async function getProducts(params: {
  keyword?: string
  selected?: boolean
  page?: number
  page_size?: number
}): Promise<{
  success: boolean
  products: HongtaoProduct[]
  total: number
  page: number
  page_size: number
}> {
  const query = new URLSearchParams()
  if (params.keyword) query.set('keyword', params.keyword)
  if (params.selected !== undefined) query.set('selected', String(params.selected))
  if (params.page) query.set('page', String(params.page))
  if (params.page_size) query.set('page_size', String(params.page_size))
  return request(`/products?${query.toString()}`)
}

// ==================== 平台管理员：数据源开通/停用（portal 企业管理） ====================

export interface AdminSourceState {
  granted: boolean
  source: HongtaoSource | null
}

export async function getAdminSource(tenantId: string): Promise<AdminSourceState> {
  const data = await request<{ success: boolean; granted: boolean; source: HongtaoSource | null }>(
    `/admin/source?tenant_id=${encodeURIComponent(tenantId)}`,
  )
  return { granted: data.granted, source: data.source }
}

export async function grantSource(tenantId: string): Promise<AdminSourceState> {
  const data = await request<{ success: boolean; granted: boolean; source: HongtaoSource | null }>(
    '/admin/grant',
    { method: 'POST', body: JSON.stringify({ tenant_id: tenantId }) },
  )
  return { granted: data.granted, source: data.source }
}

export async function revokeSource(tenantId: string): Promise<{ granted: boolean }> {
  const data = await request<{ success: boolean; granted: boolean }>(
    '/admin/revoke',
    { method: 'POST', body: JSON.stringify({ tenant_id: tenantId }) },
  )
  return { granted: data.granted }
}
