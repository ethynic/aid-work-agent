/**
 * 微信公众号内容管理 API Client（WP6）
 *
 * 租户端：/api/saas/wechat-mp/*（require_admin；X-Tenant-Id 由 getSaasAuthHeader 自动携带）
 * 平台端：/api/saas/wechat-mp/portal/*（仅 platform_admin，跨租户审计视图）
 *
 * 排队语义：导入/重试/复核只受理入队（同租户串行处理），接口返回 run_id 供查询进度。
 */

import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/wechat-mp`

// ==================== 类型 ====================

export interface RejectedUrl {
  url: string
  reason: string
}

export interface ImportUrlsResponse {
  success: boolean
  run_id: number | null
  accepted: number
  rejected: RejectedUrl[]
  duplicates: RejectedUrl[]
  message: string
}

export interface WechatMpRun {
  id: number
  tenant_id?: string
  config_id?: string | null
  trigger_type: string
  status: string
  total_count: number | null
  new_count: number | null
  updated_count: number | null
  deleted_count: number | null
  skipped_count: number | null
  failed_count: number | null
  credits_charged: number | null
  error_message: string | null
  created_at: string | null
  started_at: string | null
  completed_at: string | null
}

export interface WechatMpRunItem {
  id: number
  article_row_id: number
  action: string | null
  status: string | null
  error_code: string | null
  duplicate_of_item_id: number | null
  error_message: string | null
  billing_status: string | null
  billing_reference: string | null
  credits_charged: number | null
  created_at: string | null
  started_at: string | null
  completed_at: string | null
}

export interface WechatMpArticle {
  id: number
  tenant_id?: string
  external_id: string
  original_url: string | null
  title: string | null
  source_channel: string
  status: string
  processing_status: string
  doc_id: number | null
  error_message: string | null
  next_retry_at: string | null
  last_synced_at: string | null
  last_checked_at: string | null
  created_at: string | null
}

export interface RunListResponse {
  success: boolean
  runs: WechatMpRun[]
  total: number
  limit: number
  offset: number
}

export interface RunDetailResponse {
  success: boolean
  run: WechatMpRun
  items: WechatMpRunItem[]
}

export interface ArticleListResponse {
  success: boolean
  articles: WechatMpArticle[]
  total: number
  limit: number
  offset: number
}

export interface EnqueueResponse {
  success: boolean
  run_id: number
  trigger_type: string
  message: string
}

// ==================== 清单源（WP13 扫码绑定） ====================

export type ListSyncStatus = 'active' | 'expiring' | 'expired' | 'account_error'
export type ListSyncMode = 'auto_all' | 'manual'

export interface ListSessionStatus {
  success: boolean
  bound: boolean
  config_id?: string
  nickname?: string
  status?: ListSyncStatus
  expire_at?: string | null
  last_sync_at?: string | null
  sync_mode?: ListSyncMode
  sync_interval_hours?: number
  /** 首次回填子篇数上限（1~500，默认 100，WP13-r1） */
  max_articles?: number
  /** 首次回填是否已完成（false=下轮对账仍按上限回填） */
  backfill_done?: boolean
}

export interface ListScanStartResponse {
  success: boolean
  scan_id: string
  qr_data_url: string
  expires_in: number
}

export type ListScanPollStatus =
  | 'waiting'
  | 'scanned'
  | 'qr_expired'
  | 'expired'
  | 'failed'
  | 'confirmed'

export interface ListScanStatusResponse {
  success: boolean
  status: ListScanPollStatus
  nickname?: string
  config_id?: string
  reason_code?: 'login_failed' | 'account_error' | 'session_error' | 'bind_failed'
  reason?: string
}

export interface EnqueueManualResponse {
  success: boolean
  run_id: number | null
  enqueued: number
  skipped: number
  message: string
}

// ==================== 历史清单（WP13-r1，实时只读不落库） ====================

export interface ListHistoryItem {
  /** 子篇标题（源缺失时为 null） */
  title: string | null
  /** 发布时间（unix 秒，源缺失时为 null） */
  create_time: number | null
  /** 更新时间（unix 秒） */
  update_time: number
  /** 文章短链（复制后可走手动粘贴导入） */
  link: string
  /** 规范身份（mp:s:{token}） */
  external_id: string
  /** 是否已入库（articles 存在且未删除） */
  synced: boolean
}

export interface ListHistoryResponse {
  success: boolean
  items: ListHistoryItem[]
  /** 消息总数（非子篇数） */
  total_count: number
  /** 本次请求的消息偏移 */
  begin: number
  /** 本次请求的每页消息数 */
  count: number
}

// ==================== 内部工具 ====================

/** 统一构造错误：业务错误（400）取后端 detail 文案，其余用 fallback。 */
async function toError(res: Response, fallback: string): Promise<Error> {
  try {
    const data = await res.json()
    if (data && typeof data.detail === 'string' && data.detail) {
      return new Error(data.detail)
    }
  } catch {
    // 响应体非 JSON 时忽略，用 fallback
  }
  return new Error(fallback)
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  return { ...getSaasAuthHeader(), ...(extra || {}) }
}

// ==================== 租户端 ====================

/** 手动粘贴导入：返回 accepted/rejected 与排队说明（非法 URL 不拒整体）。 */
export async function importUrls(urls: string[]): Promise<ImportUrlsResponse> {
  const res = await fetch(`${API_BASE}/import-urls`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ urls })
  })
  if (!res.ok) throw await toError(res, '导入失败，请稍后重试')
  return res.json()
}

/** 运行记录列表（created_at DESC）。 */
export async function getRuns(params?: { limit?: number; offset?: number }): Promise<RunListResponse> {
  const sp = new URLSearchParams()
  if (params?.limit != null) sp.append('limit', String(params.limit))
  if (params?.offset != null) sp.append('offset', String(params.offset))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/runs${qs ? '?' + qs : ''}`, {
    headers: authHeaders()
  })
  if (!res.ok) throw await toError(res, '获取运行记录失败')
  return res.json()
}

/** 运行详情 + items 明细（失败原因可见）。 */
export async function getRun(runId: number): Promise<RunDetailResponse> {
  const res = await fetch(`${API_BASE}/runs/${runId}`, { headers: authHeaders() })
  if (!res.ok) throw await toError(res, '获取运行详情失败')
  return res.json()
}

/** 文章当前态列表（status/processing_status 过滤）。 */
export async function getArticles(params?: {
  status?: string
  processing_status?: string
  limit?: number
  offset?: number
}): Promise<ArticleListResponse> {
  const sp = new URLSearchParams()
  if (params?.status) sp.append('status', params.status)
  if (params?.processing_status) sp.append('processing_status', params.processing_status)
  if (params?.limit != null) sp.append('limit', String(params.limit))
  if (params?.offset != null) sp.append('offset', String(params.offset))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/articles${qs ? '?' + qs : ''}`, {
    headers: authHeaders()
  })
  if (!res.ok) throw await toError(res, '获取文章列表失败')
  return res.json()
}

/** 失败/可重试文章重入队（新 run，trigger_type='retry'）。 */
export async function retryArticle(articleRowId: number): Promise<EnqueueResponse> {
  const res = await fetch(`${API_BASE}/articles/${articleRowId}/retry`, {
    method: 'POST',
    headers: authHeaders()
  })
  if (!res.ok) throw await toError(res, '重试失败')
  return res.json()
}

/** 对 active 文章发起存活复核（新 run，trigger_type='recheck'）。 */
export async function recheckArticle(articleRowId: number): Promise<EnqueueResponse> {
  const res = await fetch(`${API_BASE}/articles/${articleRowId}/recheck`, {
    method: 'POST',
    headers: authHeaders()
  })
  if (!res.ok) throw await toError(res, '发起复核失败')
  return res.json()
}

// ==================== 清单源（WP13） ====================

const LIST_SESSION_BASE = `${API_BASE}/list-session`

/** 绑定状态查询（昵称/四态徽标/上次同步/模式/频率；凭据不回传）。 */
export async function getListSession(): Promise<ListSessionStatus> {
  const res = await fetch(LIST_SESSION_BASE, { headers: authHeaders() })
  if (!res.ok) throw await toError(res, '获取清单绑定状态失败')
  return res.json()
}

/** 发起扫码：返回二维码（data URL）与 scan_id。 */
export async function startListScan(): Promise<ListScanStartResponse> {
  const res = await fetch(`${LIST_SESSION_BASE}/scan`, { method: 'POST', headers: authHeaders() })
  if (!res.ok) throw await toError(res, '发起扫码失败')
  return res.json()
}

/** 轮询扫码状态（waiting/scanned/qr_expired/expired/failed/confirmed）。 */
export async function getListScanStatus(scanId: string): Promise<ListScanStatusResponse> {
  const res = await fetch(`${LIST_SESSION_BASE}/scan/status?scan_id=${encodeURIComponent(scanId)}`, {
    headers: authHeaders()
  })
  if (!res.ok) throw await toError(res, '查询扫码状态失败')
  return res.json()
}

/** 解绑清单源（回调配置与历史文章保留）。 */
export async function unbindListSession(): Promise<{ success: boolean; unbound: boolean }> {
  const res = await fetch(`${LIST_SESSION_BASE}/scan`, { method: 'DELETE', headers: authHeaders() })
  if (!res.ok) throw await toError(res, '解绑失败')
  return res.json()
}

/** 切换同步模式；manual→auto_all 自动补齐积压入队。 */
export async function switchListSyncMode(mode: ListSyncMode): Promise<{
  success: boolean
  config_id: string
  mode: ListSyncMode
  enqueued: number
}> {
  const res = await fetch(`${LIST_SESSION_BASE}/mode`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ mode })
  })
  if (!res.ok) throw await toError(res, '切换失败')
  return res.json()
}

/** manual 模式勾选同步：pending_manual 文章单篇/批量入队。 */
export async function enqueueManualArticles(articleRowIds: number[]): Promise<EnqueueManualResponse> {
  const res = await fetch(`${LIST_SESSION_BASE}/articles/enqueue-manual`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ article_row_ids: articleRowIds })
  })
  if (!res.ok) throw await toError(res, '同步失败')
  return res.json()
}

/**
 * 历史文章清单（WP13-r1，实时只读不落库）：分页参数 begin=消息偏移、
 * count=每页消息数（默认 5，上限 20）；未入库文章可复制链接走手动导入。
 */
export async function getListHistory(params?: { begin?: number; count?: number }): Promise<ListHistoryResponse> {
  const sp = new URLSearchParams()
  if (params?.begin != null) sp.append('begin', String(params.begin))
  if (params?.count != null) sp.append('count', String(params.count))
  const qs = sp.toString()
  const res = await fetch(`${LIST_SESSION_BASE}/history${qs ? '?' + qs : ''}`, {
    headers: authHeaders()
  })
  if (!res.ok) throw await toError(res, '获取历史清单失败')
  return res.json()
}

// ==================== 平台端（portal，仅 platform_admin） ====================

/** 跨租户运行记录列表（tenant_id 可选过滤）。 */
export async function portalGetRuns(params?: {
  tenant_id?: string
  limit?: number
  offset?: number
}): Promise<RunListResponse> {
  const sp = new URLSearchParams()
  if (params?.tenant_id) sp.append('tenant_id', params.tenant_id)
  if (params?.limit != null) sp.append('limit', String(params.limit))
  if (params?.offset != null) sp.append('offset', String(params.offset))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/portal/runs${qs ? '?' + qs : ''}`, {
    headers: authHeaders()
  })
  if (!res.ok) throw await toError(res, '获取运行记录失败')
  return res.json()
}

/** 跨租户文章列表（tenant_id 可选过滤）。 */
export async function portalGetArticles(params?: {
  tenant_id?: string
  status?: string
  processing_status?: string
  limit?: number
  offset?: number
}): Promise<ArticleListResponse> {
  const sp = new URLSearchParams()
  if (params?.tenant_id) sp.append('tenant_id', params.tenant_id)
  if (params?.status) sp.append('status', params.status)
  if (params?.processing_status) sp.append('processing_status', params.processing_status)
  if (params?.limit != null) sp.append('limit', String(params.limit))
  if (params?.offset != null) sp.append('offset', String(params.offset))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/portal/articles${qs ? '?' + qs : ''}`, {
    headers: authHeaders()
  })
  if (!res.ok) throw await toError(res, '获取文章列表失败')
  return res.json()
}
