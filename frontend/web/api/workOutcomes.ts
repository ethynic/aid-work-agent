/**
 * 工作成果 API
 *
 * 对应后端 src/api/work_outcomes.py
 * 详见 docs/system/work-outcome-record-design.md §7
 */
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/work-outcomes`

/** 成果类型 */
export type OutcomeType = 'file' | 'action' | 'decision' | 'other'

/** 成果来源 */
export type OutcomeSource = 'cp_realtime' | 'scheduled_review' | 'manual'

/** 工作成果数据 */
export interface WorkOutcome {
  outcome_id: string
  tenant_id: string
  user_id: string
  subagent_id: string | null
  session_id: string
  channel: string | null
  summary: string
  outcome_type: OutcomeType
  importance: string
  file_id: string | null
  file_name: string | null
  file_path: string | null
  metadata: Record<string, unknown>
  source: OutcomeSource
  chat_record_id: number | null
  review_batch_id: string | null
  review_confidence: number | null
  created_at: string
}

/** 列表查询参数 */
export interface ListParams {
  user_id?: string
  subagent_id?: string
  outcome_type?: OutcomeType
  source?: OutcomeSource
  channel?: string
  start_date?: string
  end_date?: string
  keyword?: string
  min_confidence?: number
  page?: number
  page_size?: number
}

/** 列表查询返回 */
export interface ListResult {
  items: WorkOutcome[]
  total: number
  page: number
  page_size: number
}

/** 统计返回 */
export interface OutcomeStats {
  total: number
  by_type: Record<string, number>
  by_subagent: Record<string, number>
  by_source: Record<string, number>
  by_channel: Record<string, number>
  review_stats: {
    last_batch_id: string | null
    last_batch_outcomes_count: number
    avg_confidence: number | null
  }
  time_range: { start: string | null; end: string | null }
}

/** 手动触发复盘返回 */
export interface ReviewRunResult {
  review_batch_id: string
  target_date: string
}

/**
 * 工作成果列表
 */
export async function listOutcomes(params: ListParams = {}): Promise<{ data: ListResult }> {
  const search = new URLSearchParams()
  if (params.user_id) search.set('user_id', params.user_id)
  if (params.subagent_id) search.set('subagent_id', params.subagent_id)
  if (params.outcome_type) search.set('outcome_type', params.outcome_type)
  if (params.source) search.set('source', params.source)
  if (params.channel) search.set('channel', params.channel)
  if (params.start_date) search.set('start_date', params.start_date)
  if (params.end_date) search.set('end_date', params.end_date)
  if (params.keyword) search.set('keyword', params.keyword)
  if (params.min_confidence !== undefined) search.set('min_confidence', String(params.min_confidence))
  search.set('page', String(params.page || 1))
  search.set('page_size', String(params.page_size || 20))

  const res = await fetch(`${API_BASE}?${search}`, {
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `查询工作成果列表失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 统计聚合
 */
export async function getStats(params: {
  user_id?: string
  start_date?: string
  end_date?: string
} = {}): Promise<{ data: OutcomeStats }> {
  const search = new URLSearchParams()
  if (params.user_id) search.set('user_id', params.user_id)
  if (params.start_date) search.set('start_date', params.start_date)
  if (params.end_date) search.set('end_date', params.end_date)

  const res = await fetch(`${API_BASE}/stats?${search}`, {
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `查询工作成果统计失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 单条详情
 */
export async function getOutcome(outcomeId: string): Promise<{ data: WorkOutcome }> {
  const res = await fetch(`${API_BASE}/${outcomeId}`, {
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `查询工作成果详情失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 删除单条（仅管理员）
 */
export async function deleteOutcome(outcomeId: string): Promise<{ data: { outcome_id: string; deleted: boolean } }> {
  const res = await fetch(`${API_BASE}/${outcomeId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `删除工作成果失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 手动触发复盘任务（仅管理员）
 */
export async function runReviewManually(targetDate?: string): Promise<{ data: ReviewRunResult }> {
  const search = new URLSearchParams()
  if (targetDate) search.set('target_date', targetDate)

  const res = await fetch(`${API_BASE}/review/run?${search}`, {
    method: 'POST',
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `手动触发复盘失败: ${res.status}`)
  }
  return res.json()
}
