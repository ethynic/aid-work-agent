/**
 * 企微会话旁路日志与日报 API（/api/wecom-conversations 前缀）
 * 会话列表与聊天时间线、日报计划与日报工作台；无人工记录开关/持续采集/
 * 处理状态编辑/附件原件入口（设计 §6/§7）。任务状态沿现有公开 Runner 网关轮询。
 */

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/wecom-conversations`

import { getTenantScopedKey } from './tenantStorage'
import { credentialGet } from '@/platform/credentialStore'

function getAuthHeaders(): Record<string, string> {
  const path = window.location.pathname
  let tokenKey: string
  if (path.startsWith('/t/')) {
    tokenKey = getTenantScopedKey('saas_token')
  } else {
    tokenKey = 'saas_token'
  }
  const token = credentialGet(tokenKey)
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const match = path.match(/^\/t\/([^/]+)/)
  if (match) headers['X-Tenant-Id'] = match[1]
  headers['Content-Type'] = 'application/json'
  return headers
}

/** 后端统一返回 {success:true, data:{...}}；此处解包 data。 */
async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: getAuthHeaders(),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const value = await response.json().catch(() => ({}))
  if (!response.ok || value.success === false) {
    throw new Error(value.error || value.detail || `请求失败（${response.status}）`)
  }
  return (value.data ?? {}) as T
}

// --- 类型定义 ---

export interface WecomConversation {
  id: number
  source_account_id: string
  source_conversation_id: string
  session_locator: string
  name: string | null
  kind: string | null
  peer_platform: string | null
  timezone: string
  device_id: string | null
  enroll_reasons: string[] | null
  daily_plan_task_id: string | null
  last_archived_at: string | null
  coverage: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface WecomMessage {
  id: number
  conversation_id: number
  source_generation: string
  source_message_id: number
  server_id: string | null
  sender_id: string | null
  sender_name: string | null
  name_source: string | null
  sender_platform: 'wechat' | 'wecom' | 'unknown'
  sender_side: 'self' | 'peer' | 'timeline'
  kind: string | null
  text: string | null
  text_format: string | null
  system: boolean
  parse_trusted: boolean
  baseline: boolean
  sent_at: string | null
  source_time_text: string | null
  attachment_ref: AttachmentIndex | null
  send_origin: string | null
  origin_evidence: Record<string, unknown> | null
  send_invocation_id: string | null
}

export interface AttachmentIndex {
  session?: string
  account?: string
  source_generation?: string
  device_id?: string | null
  message_id?: number
  meta?: {
    kind?: string
    status?: string
    name?: string
    mime_type?: string
    size_bytes?: number
    width?: number
    height?: number
  }
}

export interface DailySummaryBody {
  overview?: string
  /** 旧版已保存报告仍可能含以下字段。 */
  categories?: SummaryItem[]
  handled?: SummaryItem[]
  pending_human?: SummaryItem[]
  noteworthy?: SummaryItem[]
  anomalies?: SummaryItem[]
  notes?: string
}

export interface SummaryItem {
  topic?: string
  detail?: string
  count?: number
  evidence?: number[]
}

export interface DailyPlan {
  task_id: string
  name: string
  status: string
  profile_id: string
  schedule_type: string
  cron_expression: string | null
  interval_seconds: number | null
  last_run_at: string | null
  last_status: string | null
  conversation_count: number
  notification_rule_id: string | null
  next_due_at: string | null
  config: { timezone?: string; device?: string; account?: string; target_day?: 'today' | 'yesterday'; scope_mode?: 'all' | 'selected' }
}

export interface DailyReportEntry {
  conversation_id: number
  name: string | null
  timezone: string
  daily_plan_task_id: string | null
  summary: DailySummaryBody | null
  coverage: Record<string, unknown> | null
  snapshot_at: string | null
  generated_at: string | null
  source_runner_id: string | null
  report_updated_at: string | null
}

export interface RunnerAccept {
  runner_id: string | null
  created?: boolean
  status?: string
  session_id?: string
}

// --- 会话与时间线 ---

export function listConversations(options: { limit?: number; offset?: number } = {}): Promise<{ conversations: WecomConversation[] }> {
  const query = new URLSearchParams()
  if (options.limit) query.set('limit', String(options.limit))
  if (options.offset) query.set('offset', String(options.offset))
  return request('GET', `/conversations${query.size ? `?${query}` : ''}`)
}

export function getConversation(id: number): Promise<{ conversation: WecomConversation }> {
  return request('GET', `/conversations/${id}`)
}

export interface MessagePage {
  messages: WecomMessage[]
  has_more: boolean
}

/** 无日期记录排在显示顺序末尾，keyset 尾键用 'infinity' 表示。 */
export function messagePageKey(message: WecomMessage | null): { sent: string; id: number } | null {
  if (!message) return null
  return { sent: message.sent_at ?? 'infinity', id: message.id }
}

export function listMessages(conversationId: number, params: { before?: { sent: string; id: number } | null; limit?: number } = {}): Promise<MessagePage> {
  const query = new URLSearchParams()
  if (params.before) {
    query.set('before_id', String(params.before.id))
    query.set('before_sent', params.before.sent)
  }
  if (params.limit) query.set('limit', String(params.limit))
  query.set('order', 'desc')
  return request('GET', `/conversations/${conversationId}/messages?${query.toString()}`)
}

// --- 日报计划与工作台 ---

export function listDailyPlans(): Promise<{ plans: DailyPlan[] }> {
  return request('GET', '/daily-plans')
}

export interface DirectoryConversation {
  session: string
  name: string | null
  kind: string
  peer_platform: string
  same_name?: boolean
  added?: boolean
  conversation_id?: number
}

export interface DirectoryPage {
  device_id: string
  self_user_id: string
  source_generation: string
  conversations: DirectoryConversation[]
  next_cursor: string | null
}

export function listDirectory(options: { query?: string; cursor?: string; limit?: number } = {}): Promise<DirectoryPage> {
  const query = new URLSearchParams()
  if (options.query) query.set('query', options.query)
  if (options.cursor) query.set('cursor', options.cursor)
  query.set('limit', String(options.limit ?? 30))
  return request('GET', `/directory?${query}`)
}

export function previewDirectory(session: string): Promise<{ device_id: string; self_user_id: string; source_generation: string; messages: { text?: string | null; sender_name?: string | null }[] }> {
  return request('GET', `/directory/preview?${new URLSearchParams({ session })}`)
}

export function addConversations(sessions: string[], expected?: { expected_device_id: string; expected_source_generation: string }): Promise<{ conversations: WecomConversation[]; added_count: number }> {
  return request('POST', '/conversations/add', { sessions, ...expected })
}

export interface DailyPlanWrite {
  target_day: 'today' | 'yesterday'
  scope_mode: 'all' | 'selected'
  conversation_ids: number[]
  hour?: number
  minute?: number
  interval_hours?: number
}

export function getDailyPlan(): Promise<{ plan: DailyPlan | null }> {
  return request('GET', '/daily-plan')
}

export function saveDailyPlan(body: DailyPlanWrite, exists: boolean): Promise<{ plan: DailyPlan }> {
  return request(exists ? 'PUT' : 'POST', exists ? '/daily-plan' : '/daily-plans', body)
}

export function pauseDailyPlan(): Promise<{ status: string }> {
  return request('POST', '/daily-plan/pause')
}

export function resumeDailyPlan(): Promise<{ plan: DailyPlan }> {
  return request('POST', '/daily-plan/resume')
}

export interface DailyExecutionTarget {
  conversation_id: number
  name: string | null
  status: string
  phase: string
  reason_label: string | null
  started_at: string | null
  finished_at: string | null
  error_code: string | null
  diagnostic: string
}

export interface DailyExecution {
  runner_id: string
  status: string
  accepted_at: string
  finished_at: string | null
  business_date: string
  trigger: string
  task_id: string | null
  target_count: number
  error_code: string | null
  targets?: DailyExecutionTarget[]
}

export function dailyReports(businessDate: string, options: { conversationIds?: number[]; taskId?: string; cursor?: number; limit?: number } = {}): Promise<{ business_date: string; reports: DailyReportEntry[]; has_more: boolean; next_cursor: number | null; last_generation: DailyExecution | null }> {
  const query = new URLSearchParams({ business_date: businessDate })
  if (options.conversationIds?.length) query.set('conversation_ids', options.conversationIds.join(','))
  if (options.taskId) query.set('task_id', options.taskId)
  if (options.cursor != null) query.set('cursor', String(options.cursor))
  query.set('limit', String(options.limit ?? 200))
  return request('GET', `/daily-reports?${query.toString()}`)
}

export function listDailyExecutions(businessDate: string, cursor?: string): Promise<{ executions: DailyExecution[]; has_more: boolean; next_cursor: string | null }> {
  const query = new URLSearchParams({ business_date: businessDate, limit: '30' })
  if (cursor) query.set('cursor', cursor)
  return request('GET', `/daily-reports/executions?${query}`)
}

export function getDailyExecution(runnerId: string): Promise<{ execution: DailyExecution }> {
  return request('GET', `/daily-reports/executions/${encodeURIComponent(runnerId)}`)
}

export function listDailySummaries(conversationId: number, beforeDate?: string, limit = 30): Promise<{ summaries: unknown[] }> {
  const query = new URLSearchParams()
  if (beforeDate) query.set('before_date', beforeDate)
  query.set('limit', String(limit))
  return request('GET', `/conversations/${conversationId}/daily-summaries?${query.toString()}`)
}

export function generateDailyReport(body: {
  conversation_ids: number[]
  client_request_id?: string
}): Promise<RunnerAccept & { business_date: string }> {
  return request('POST', '/daily-reports/generate', body)
}

// --- Runner 状态轮询（沿现有公开网关） ---

export interface RunnerState {
  runner_id?: string
  status?: string
}

export async function getRunner(runnerId: string): Promise<RunnerState | null> {
  const headers = getAuthHeaders()
  const response = await fetch(`/api/chat/runners/${encodeURIComponent(runnerId)}`, { headers })
  const value = await response.json().catch(() => ({}))
  if (!response.ok) return null
  return value.runner ?? null
}
