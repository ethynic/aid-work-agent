/**
 * 可观测性追踪查看 API
 */

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/monitor`

function getAuthHeader(): Record<string, string> {
  const token = localStorage.getItem('portal_token')
  const headers: Record<string, string> = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

export interface SessionSummary {
  session_id: string
  tenant_id: string | null
  user_id: string | null
  trace_count: number
  total_tokens: number
  error_count: number
  last_trace_at: string | null
  first_input: string | null
  first_content: string | null
  source_type: string | null
  subagent_id: string | null
}

export interface TraceSummary {
  trace_id: string
  session_id: string | null
  input: string | null
  output: string | null
  status: string
  duration_ms: number
  total_tokens: number
  tool_calls_count: number
  agent_iterations: number
  tags: string[]
  source_type: string
  created_at: string | null
}

export interface TraceDetail {
  trace_id: string
  session_id: string | null
  tenant_id: string | null
  user_id: string | null
  subagent_id: string | null
  input: string | null
  output: string | null
  status: string
  duration_ms: number
  total_tokens: number
  model: string | null
  provider: string | null
  agent_iterations: number
  tool_calls_count: number
  tags: string[]
  source_type: string
  error_message: string | null
  created_at: string | null
  channel_info?: {
    title: string | null
    username: string | null
    channel_type: string | null
    channel_user_id: string | null
    channel_chat_id: string | null
  } | null
}

export interface SpanDetail {
  span_id: string
  span_type: string
  name: string
  input: string | null
  output: string | null
  metadata: Record<string, any> | null
  model: string | null
  prompt_tokens: number
  completion_tokens: number
  duration_ms: number
  status: string
  start_time: string | null
}

export async function getTracedSessions(params: {
  page?: number
  page_size?: number
  tenant_id?: string
  time_range?: string
  status?: string
  source_type?: string
  search?: string
}): Promise<{ success: boolean; data: SessionSummary[]; total: number; page: number; page_size: number; total_pages: number }> {
  const query = new URLSearchParams()
  if (params.page) query.set('page', String(params.page))
  if (params.page_size) query.set('page_size', String(params.page_size))
  if (params.tenant_id) query.set('tenant_id', params.tenant_id)
  if (params.time_range) query.set('time_range', params.time_range)
  if (params.status) query.set('status', params.status)
  if (params.source_type) query.set('source_type', params.source_type)
  if (params.search) query.set('search', params.search)

  const res = await fetch(`${API_BASE}/sessions?${query.toString()}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function getSessionTraces(sessionId: string): Promise<{ success: boolean; traces: TraceSummary[] }> {
  const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/traces`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function getTraces(params: {
  page?: number
  page_size?: number
  status?: string
  tenant_id?: string
  time_range?: string
}): Promise<{ success: boolean; data: TraceSummary[]; total: number; page: number; page_size: number; total_pages: number }> {
  const query = new URLSearchParams()
  if (params.page) query.set('page', String(params.page))
  if (params.page_size) query.set('page_size', String(params.page_size))
  if (params.status) query.set('status', params.status)
  if (params.tenant_id) query.set('tenant_id', params.tenant_id)
  if (params.time_range) query.set('time_range', params.time_range)

  const res = await fetch(`${API_BASE}/traces?${query.toString()}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function getTraceDetail(traceId: string): Promise<{
  success: boolean
  trace: TraceDetail | null
  spans: SpanDetail[]
  message?: string
}> {
  const res = await fetch(`${API_BASE}/traces/${encodeURIComponent(traceId)}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}
