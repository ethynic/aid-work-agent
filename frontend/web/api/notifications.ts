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
  /** 配置回显（webhook 脱敏如 https://open.feishu.cn/****ab12；邮箱完整），解密失败时缺省 */
  configuration_display?: string
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
  const headers: Record<string, string> = { ...getAuthHeader(), ...(init.headers as Record<string, string> || {}) }
  // 租户上下文通知（定时任务等）存 tenant scope，平台管理员必须带 X-Tenant-Id
  // 才能匹配到租户域通知（普通租户用户后端自行从 users.tenant_id 解析）
  const m = window.location.pathname.match(/^\/t\/([^/]+)/)
  if (m) headers['X-Tenant-Id'] = m[1]
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
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

  /** 任务通知目标选项（非管理员可用，设计 §8.3）：个人 + 租户级目标，无凭据 */
  selectableTargets: () =>
    request<{ targets: TenantTarget[] }>('/selectable-targets'),

  /** 任务视图投递状态（设计 §8.4）：该任务全部通知事件的投递账 */
  taskDeliveries: (taskId: string) => {
    const q = new URLSearchParams({ task_id: taskId })
    return request<TaskDeliverySummary>(`/task-deliveries?${q.toString()}`)
  },
}

export interface TaskDeliveryRecord {
  target_id: string
  channel: string
  provider: string
  /** pending / delivered / failed 等 */
  state: string
  attempts: number
  error_class?: string | null
  accepted_at?: string | null
  delivered_at?: string | null
}

export interface TaskDeliveryEvent {
  notification_ref: string
  event_type: string
  created_at: string
  /** 消息展开状态（Phase 5 二轮评审第 5 项）：pending=消息已生成但异步展开
   *  未完成（此时无 deliveries 不代表未配置外发）；expanded=已展开，无
   *  deliveries 才是确认无外发目标 */
  expansion_state?: string
  deliveries: TaskDeliveryRecord[]
}

export interface TaskDeliverySummary {
  task_id: string
  events: TaskDeliveryEvent[]
}
