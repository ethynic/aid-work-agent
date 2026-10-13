import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/notifications`

export interface TenantTarget {
  target_id: string
  installation_ref: string
  binding_version: number
  state: 'active' | 'revoked' | 'invalid'
  enabled_since: string | null
  channel: string
  provider: string
}

export interface InboxItem {
  notification_ref: string
  event_type: string
  category: string
  priority: number
  title: string
  body: string
  link?: string | null
  read_at: string | null
  created_at: string
}

export interface InboxResponse {
  unread_count: number
  items: InboxItem[]
}

export type NotificationErrorCode =
  | 'NOTIFICATION_DISABLED'
  | 'NOTIFICATION_PROVIDER_UNAVAILABLE'
  | 'NOTIFICATION_TARGET_OWNED'
  | 'NOTIFICATION_BINDING_CHANGED'
  | 'NOTIFICATION_BINDING_CONFLICT'
  | 'NOTIFICATION_TEST_RATE_LIMITED'
  | 'NOTIFICATION_ENDPOINT_FORBIDDEN'
  | 'NOTIFICATION_INVALID_EMAIL'
  | 'NOTIFICATION_TENANT_DISABLED'

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...getAuthHeader(), ...(init.headers || {}) },
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new Error((data as any).error || `HTTP_${res.status}`) as Error & { code?: string }
    err.code = (data as any).error
    throw err
  }
  return data as T
}

export const notificationsAPI = {
  listTenantTargets: () => request<{ targets: TenantTarget[] }>('/tenant-targets'),

  registerTenantTarget: (body: {
    channel: string
    provider: string
    configuration: Record<string, string>
    expected_binding_version?: number
  }) =>
    request<TenantTarget>('/tenant-targets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  revokeTenantTarget: (targetId: string, bindingVersion: number) =>
    request<{ success: boolean }>(`/tenant-targets/${targetId}`, {
      method: 'DELETE',
      headers: { 'X-Notification-Binding-Version': String(bindingVersion) },
    }),

  tenantTest: (body: { target_id: string; binding_version: number }) =>
    request<{ notification_ref: string }>('/tenant-test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  inbox: (params?: { unread_only?: boolean; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.unread_only) q.set('unread_only', '1')
    if (params?.limit) q.set('limit', String(params.limit))
    const s = q.toString()
    return request<InboxResponse>(`/inbox${s ? '?' + s : ''}`)
  },

  markRead: (notificationRef: string) =>
    request<{ success: boolean }>(`/inbox/${notificationRef}/read`, { method: 'POST' }),
}
