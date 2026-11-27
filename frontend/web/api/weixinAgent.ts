import { getAuthHeader } from './auth'

const BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/local-tools/weixin-agent`

export type Preset = 'today' | '24h' | '3d' | '7d'
export const presets: ReadonlyArray<{ id: Preset; label: string }> = [
  { id: 'today', label: '今天' }, { id: '24h', label: '最近 24 小时' },
  { id: '3d', label: '最近 3 天' }, { id: '7d', label: '最近 7 天' },
]
export interface Group { id: string; name: string; deleted_at: string | null }
export interface GroupResult {
  group_id: string
  name: string
  state: string
  summary?: string | null
  message_count?: number
  partial?: boolean
  error_code?: string
}
export interface Batch {
  id: string
  device_id: string
  source: 'manual' | 'schedule'
  title: string | null
  retry_batch_id: string | null
  start_at: string
  end_at: string
  created_at: string
  state: 'queued' | 'running' | 'succeeded' | 'partial' | 'failed'
  groups: GroupResult[]
  notification_status?: 'queued' | 'sent' | 'failed' | 'skipped'
}
export type HistoryBatch = Batch | Omit<Batch, 'start_at' | 'end_at' | 'retry_batch_id'> & {
  legacy: true
  retry_batch_id?: never
}

export interface ScheduleDefinition {
  device_id: string
  title: string
  group_ids: string[]
  preset: Preset
  frequency: 'daily' | 'weekly'
  weekday: number | null
  hour: number
  minute: number
  notify: boolean
  notification_rule_id: string | null
}
export interface Schedule extends ScheduleDefinition {
  id: string
  next_due_at: string | null
  paused: boolean
  preview_at?: string
}
export interface RobotRule { rule_id: string; name: string; channels: string[] }

export class WeixinAgentError extends Error {
  constructor(message: string, public readonly code?: string) { super(message) }
}
async function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const form = body instanceof FormData
  const res = await fetch(BASE + path, {
    method, signal, headers: { ...getAuthHeader(), ...(body === undefined || form ? {} : { 'Content-Type': 'application/json' }) },
    body: form ? body : body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail
    throw new WeixinAgentError(detail?.error || '微信 Agent 请求未完成，请重试。', detail?.code)
  }
  return res.status === 204 ? undefined as T : await res.json()
}
export const listGroups = () => request<Group[]>('/groups')
export const saveGroup = (name: string, id?: string) => request<Group>(id ? `/groups/${id}` : '/groups', id ? 'PUT' : 'POST', { name })
export const removeGroup = (id: string) => request<void>(`/groups/${id}`, 'DELETE')
export const startBatch = (body: { device_id: string; group_ids: string[]; preset: Preset | 'custom'; start_at?: string; end_at?: string }) =>
  request<Batch>('/batches', 'POST', body)
export const getBatch = (id: string, signal?: AbortSignal) => request<Batch>(`/batches/${encodeURIComponent(id)}`, 'GET', undefined, signal)
export const listBatches = (groupId: string, offset: number) => {
  const query = new URLSearchParams({ offset: String(offset), ...(groupId ? { group_id: groupId } : {}) })
  return request<{ batches: HistoryBatch[]; has_more: boolean }>(`/batches?${query}`)
}
export const retryGroup = (batchId: string, groupId: string) =>
  request<Batch>(`/batches/${batchId}/retry/${groupId}`, 'POST')
export const listSchedules = () => request<Schedule[]>('/schedules')
export const listRobotRules = () => request<RobotRule[]>('/notification-rules')
export const saveSchedule = (body: ScheduleDefinition, id?: string) =>
  request<Schedule>(id ? `/schedules/${id}` : '/schedules', id ? 'PUT' : 'POST', body)
export const setSchedulePaused = (id: string, paused: boolean) =>
  request<Schedule>(`/schedules/${id}`, 'PATCH', { paused })

export interface FriendContact {
  name: string
  identifier: string
  verify_message: string
  selected: boolean
}
export interface FriendPreview {
  filename: string
  contacts: Array<FriendContact & { exclusion: string | null }>
}
export type FriendStatus = 'queued' | 'running' | 'request_sent' | 'friend_added' | 'already_requested' |
  'already_friend' | 'failed' | 'unknown' | 'skipped'
export interface FriendResult {
  name: string
  identifier: string
  status: FriendStatus
  reason: string | null
  submitted_at: string | null
}
export interface FriendBatchSummary {
  id: string
  filename: string
  created_at: string
  state: 'running' | 'completed'
  total: number
  counts: Partial<Record<FriendStatus, number>>
}
export interface FriendBatch extends FriendBatchSummary {
  contacts: FriendResult[]
}
export const previewFriends = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return request<FriendPreview>('/friends/preview', 'POST', form)
}
export const startFriendBatch = (body: {
  request_id: string; device_id: string; filename: string; verify_message: string; contacts: FriendContact[]
}) => request<FriendBatch>('/friends/batches', 'POST', body)
export const getFriendBatch = (id: string, signal?: AbortSignal) =>
  request<FriendBatch>(`/friends/batches/${encodeURIComponent(id)}`, 'GET', undefined, signal)
export const listFriendBatches = (offset = 0) =>
  request<{ batches: FriendBatchSummary[]; has_more: boolean }>(`/friends/batches?offset=${offset}`)
