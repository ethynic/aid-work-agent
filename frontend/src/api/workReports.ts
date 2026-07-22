/**
 * 工作日报 API
 *
 * 对应后端 src/api/work_reports.py
 * 详见 docs/research/ai-agent-experience-daily-report-research.md
 */
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/reports`

/** 报告范围 */
export type ReportScope = 'personal' | 'team'

/** 报告类型 */
export type ReportType = 'daily' | 'weekly' | 'monthly'

/** 个人日报数据 */
export interface PersonalReport {
  report_id: string
  scope: 'personal'
  report_type: ReportType
  report_date: string
  target_user_id: string
  metrics: {
    dialog_count: number
    credit_cost: number
    saved_minutes: number
    subagent_distribution: Record<string, number>
    tool_distribution: Record<string, number>
    source_distribution: Record<string, number>
    time_range: { start: string; end: string }
  }
  summary_text: string
  highlights?: Array<Record<string, unknown>> | null
  suggestions?: Array<Record<string, unknown>> | null
  model?: string
  token_cost?: number
  credit_cost: number
  generated_at?: string
  regenerated_count?: number
  cached?: boolean
}

/** 推送配置 */
export interface ReportPreferences {
  tenant_id: string
  user_id: string
  personal_report_enabled: boolean
  personal_report_types: ReportType[]
  personal_push_channels: string[]
  personal_push_time: string
  team_report_enabled: boolean
  team_report_types: ReportType[]
  team_push_channels: string[]
  team_push_time: string
  updated_at: string | null
}

/** 更新推送配置的请求体 */
export interface UpdatePreferencesRequest {
  personal_report_enabled?: boolean
  personal_report_types?: ReportType[]
  personal_push_channels?: string[]
  personal_push_time?: string
  team_report_enabled?: boolean
  team_report_types?: ReportType[]
  team_push_channels?: string[]
  team_push_time?: string
}

/**
 * 获取今日个人日报（不存在则自动生成）
 */
export async function getPersonalToday(reportType: ReportType = 'daily'): Promise<{ data: PersonalReport | null; cached: boolean }> {
  const res = await fetch(`${API_BASE}/personal/today?report_type=${reportType}`, {
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    throw new Error(`获取今日日报失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 获取指定日期个人日报（不自动生成）
 */
export async function getPersonalByDate(reportDate: string, reportType: ReportType = 'daily'): Promise<{ data: PersonalReport | null; cached: boolean }> {
  const res = await fetch(`${API_BASE}/personal/${reportDate}?report_type=${reportType}`, {
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    throw new Error(`获取日报失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 重新生成个人日报（扣积分）
 */
export async function regeneratePersonal(reportDate: string, reportType: ReportType = 'daily'): Promise<{ data: PersonalReport }> {
  const res = await fetch(`${API_BASE}/personal/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify({ report_date: reportDate, report_type: reportType }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `重新生成失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 历史日报列表
 */
export async function listPersonalReports(params: {
  report_type?: ReportType
  limit?: number
  offset?: number
} = {}): Promise<{ data: PersonalReport[]; total: number }> {
  const search = new URLSearchParams()
  if (params.report_type) search.set('report_type', params.report_type)
  if (params.limit) search.set('limit', String(params.limit))
  if (params.offset) search.set('offset', String(params.offset))
  const res = await fetch(`${API_BASE}/personal/list?${search}`, {
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    throw new Error(`获取历史日报列表失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 获取推送配置
 */
export async function getPreferences(): Promise<{ data: ReportPreferences }> {
  const res = await fetch(`${API_BASE}/preferences`, {
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) {
    throw new Error(`获取推送配置失败: ${res.status}`)
  }
  return res.json()
}

/**
 * 更新推送配置
 */
export async function updatePreferences(body: UpdatePreferencesRequest): Promise<{ data: ReportPreferences }> {
  const res = await fetch(`${API_BASE}/preferences`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `更新推送配置失败: ${res.status}`)
  }
  return res.json()
}
