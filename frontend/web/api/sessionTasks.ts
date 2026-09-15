import { getAuthHeader } from './auth'

export type CompletionRule = { mode: 'rounds'; rounds_target: number } | { mode: 'judged'; criteria: string[] } | { mode: 'peer_confirmed'; require_all: true; fields: { key: string; question: string; allowed_values: string[]; accepted_values: string[] }[] }
export interface TaskSpec {
  goal: string
  completion_rule: CompletionRule
  reply_policy: { style: string; allowed_facts: string[]; forbidden_commitments: string[] }
  limits: { max_replies: number; max_decisions: number; max_cost_units: number; expires_at: string; peer_wait_timeout_seconds: number }
  opening_text: string | null
  work_window: { start_hour_utc: number; end_hour_utc: number; weekdays_utc: number[] } | null
}
export interface Binding { id: string; device_id: string; account_binding_id: string; conversation_label: string; conversation_type: string; verification_status: string }
export interface SessionTask {
  id: string; status: string; version: number; input_version?: number; phase?: string; blocked_reason?: string; completion_reason?: string
  spec?: TaskSpec; draft_spec?: TaskSpec; goal_summary?: string; binding_label?: string; device_id: string; account_binding_id: string; conversation_binding_id: string
  device_online?: boolean; last_observed_at?: string; replies_count?: number; rounds_count?: number; decisions_count?: number; expires_at?: string; cost?: { settled: number; reserved: number }
}
export interface Timeline { batches: Record<string, any>[]; decisions: Record<string, any>[]; executions: Record<string, any>[]; messages?: Record<string, any>[] }
export class SessionTaskApiError extends Error {
  constructor(message: string, public code: string, public status: number) { super(message) }
}
const root = import.meta.env.VITE_API_BASE_URL || '/api'
async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal, key?: string): Promise<T> {
  const response = await fetch(`${root}${path}`, { method, signal, headers: { ...getAuthHeader(), 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  const result = await response.json()
  if (!response.ok || !result.success) throw new SessionTaskApiError(result.error || '请求失败', result.code || 'REQUEST_FAILED', response.status)
  return result.data
}
export const sessionTasksApi = {
  notifications: (signal?: AbortSignal) => request<{ items: { id: string; task_id: string; status: string; reason?: string; created_at: string }[]; total: number }>('/session-tasks/notifications', 'GET', undefined, signal),
  list: (status: string, offset: number, signal?: AbortSignal) => request<{ items: SessionTask[]; total: number }>(`/session-tasks?limit=20&offset=${offset}&status=${encodeURIComponent(status)}`, 'GET', undefined, signal),
  get: (id: string, signal?: AbortSignal) => request<SessionTask>(`/session-tasks/${id}`, 'GET', undefined, signal),
  timeline: (id: string, signal?: AbortSignal, offset = 0) => request<Timeline>(`/session-tasks/${id}/timeline?limit=100&offset=${offset}`, 'GET', undefined, signal),
  capabilities: (signal?: AbortSignal) => request<{ publish_enabled: boolean; reason?: string }>('/session-tasks/capabilities', 'GET', undefined, signal),
  bindings: (signal?: AbortSignal) => request<Binding[]>('/weixin-conversation/bindings?limit=200', 'GET', undefined, signal),
  create: (body: unknown, key: string, signal?: AbortSignal) => request<{ task_id: string; version: number; status: string }>('/session-tasks', 'POST', body, signal, key),
  save: (id: string, version: number, spec: TaskSpec, signal?: AbortSignal) => request<SessionTask>(`/session-tasks/${id}/draft`, 'PATCH', { expected_version: version, spec }, signal),
  confirm: (id: string, version: number, signal?: AbortSignal) => request<{ confirmation_id: string }>(`/session-tasks/${id}/confirm`, 'POST', { expected_version: version }, signal),
  publish: (id: string, version: number, confirmation_id: string, key: string, signal?: AbortSignal) => request<SessionTask>(`/session-tasks/${id}/publish`, 'POST', { expected_version: version, confirmation_id }, signal, key),
  control: (id: string, version: number, action: string, inputVersion?: number, signal?: AbortSignal) => request<SessionTask>(`/session-tasks/${id}/${action}`, 'POST', { expected_version: version, ...(action === 'stop' ? { reason_code: 'user_cancel' } : {}), ...(action === 'resume' ? { resume_from: { mode: 'fresh_baseline', expected_input_version: inputVersion } } : {}) }, signal),
}
