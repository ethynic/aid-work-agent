/**
 * 就业/创业数据源同步 API Client（lyjy_employment + sccy_policy，接口同构）
 *
 * 租户端：/api/saas/{modulePath}/*（require_admin；X-Tenant-Id 由 getSaasAuthHeader 自动携带）
 *
 * 排队语义：立即运行只受理入队（同租户串行处理），接口返回 run 供查询进度；
 * 在队去重：已有 queued/running 时返回既有 run（deduped=true）。
 */

import { getSaasAuthHeader } from './saasTenant'

// 模块路径 ↔ 模块名（后端 module 列值）
export const EMPLOYMENT_MODULE_PATH = 'lyjy-employment'
export const SCCY_POLICY_MODULE_PATH = 'sccy-policy'
export const EMPLOYMENT_MODULE = 'lyjy_employment'
export const SCCY_POLICY_MODULE = 'sccy_policy'

// lyjy 同步范围可选项（六分类映射表；与后端 /source/options 同源）
export const SYNC_TABLE_OPTIONS = [
  { id: 'qs_jobs', label: '在招职位' },
  { id: 'qs_guide', label: '就业指导' },
  { id: 'qs_article', label: '政策资讯' },
  { id: 'qs_notice', label: '通知公告' },
  { id: 'qs_explain', label: '办事指南' },
]

// ==================== 类型 ====================

export interface SyncSource {
  enabled: boolean
  sync_interval_hours: number
  selection_mode: 'all' | 'ids'
  selected_ids: string[]
  connection_configured: boolean
  last_sync_at: string | null
  last_error: string | null
}

export interface SyncRun {
  id: number
  trigger_type: string
  status: string
  new_count: number | null
  updated_count: number | null
  skipped_count: number | null
  deleted_count: number | null
  restored_count: number | null
  failed_count: number | null
  embedding_tokens: number | null
  credits_charged: number | null
  fetch_complete: boolean | null
  total_reported: number | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export interface SyncRunItem {
  native_id: string
  action: string | null
  status: string | null
  error_code: string | null
  error_message: string | null
  embedding_tokens: number | null
  billing_status: string | null
  credits_charged: number | null
}

export interface SyncSourcePatchPayload {
  enabled?: boolean
  sync_interval_hours?: number
  selection_mode?: 'all' | 'ids'
  selected_ids?: string[]
}

export interface AdminSyncSourceState {
  granted: boolean
  source: SyncSource | null
}

export interface DbConnectionPayload {
  db_host: string
  db_port: number
  db_database: string
  db_user: string
  db_password: string
}

// ==================== 请求 ====================

async function request<T>(modulePath: string, path: string, init?: RequestInit): Promise<T> {
  const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/${modulePath}`
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

// ==================== 租户端 ====================

export function getSource(modulePath: string): Promise<{ success: boolean; source: SyncSource }> {
  return request(modulePath, '/source')
}

export function patchSource(
  modulePath: string,
  payload: SyncSourcePatchPayload,
): Promise<{ success: boolean; source: SyncSource }> {
  return request(modulePath, '/source', { method: 'PATCH', body: JSON.stringify(payload) })
}

export function triggerSource(modulePath: string): Promise<{
  success: boolean
  run: { run_id: number; status: string; deduped?: boolean }
}> {
  return request(modulePath, '/source/trigger', { method: 'POST' })
}

export function getRuns(modulePath: string, limit = 20, offset = 0): Promise<{
  success: boolean
  runs: SyncRun[]
}> {
  return request(modulePath, `/runs?limit=${limit}&offset=${offset}`)
}

export function getRun(modulePath: string, runId: number): Promise<{
  success: boolean
  run: SyncRun
  items: SyncRunItem[]
}> {
  return request(modulePath, `/runs/${runId}`)
}

// ==================== 平台管理员：数据源开通/停用（portal 企业管理） ====================

export function getAdminSource(modulePath: string, tenantId: string): Promise<AdminSyncSourceState> {
  return request(
    modulePath,
    `/admin/source?tenant_id=${encodeURIComponent(tenantId)}`,
  ).then((data: any) => ({ granted: data.granted, source: data.source }))
}

export function grantSource(
  modulePath: string,
  tenantId: string,
  connection: DbConnectionPayload,
): Promise<AdminSyncSourceState> {
  return request(modulePath, '/admin/grant', {
    method: 'POST',
    body: JSON.stringify({ tenant_id: tenantId, ...connection }),
  }).then((data: any) => ({ granted: data.granted, source: data.source }))
}

export function revokeSource(
  modulePath: string,
  tenantId: string,
): Promise<{ granted: boolean; cancelled_runs: number }> {
  return request(modulePath, '/admin/revoke', {
    method: 'POST',
    body: JSON.stringify({ tenant_id: tenantId }),
  })
}
