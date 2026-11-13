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
  /** 规则级目标所属规则（规则引擎，设计 §10）；租户级目标缺省 */
  rule_id?: string | null
  /** 配置回显（webhook 脱敏如 https://open.feishu.cn/****ab12；邮箱完整），解密失败时缺省 */
  configuration_display?: string
}

export interface NotificationRule {
  rule_id: string
  event_type: string
  name: string
  enabled: boolean
  is_system: boolean
  /** scope=channel（全部渠道）/ kf_config（指定客服账号 kf_config_ids=config_id 列表）；
   *  scheduled.task 聚合规则用 events（任务事件子集，缺省全部） */
  conditions: { scope?: 'channel' | 'kf_config'; kf_config_ids?: string[]; events?: string[] }
  /** task_owner 仅用于 scheduled.task 规则（占位：站内信恒发任务属主） */
  recipient_mode: 'tenant_admins' | 'kf_assignee' | 'task_owner'
  created_by: string | null
  created_at: string
  updated_at: string
  active_targets: number
}

/** 定时任务可选用通知规则（GET /task-rules 普通用户只读）：规则 + 渠道目标脱敏摘要 */
export interface TaskNotifyRule {
  rule_id: string
  name: string
  conditions: { events?: string[] }
  targets: TenantTarget[]
}

export interface RuleSaveBody {
  name?: string
  enabled?: boolean
  conditions?: NotificationRule['conditions']
  recipient_mode?: NotificationRule['recipient_mode']
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
  | 'NOTIFICATION_WECOM_APP_UNAVAILABLE'
  | 'NOTIFICATION_BIND_STATE_INVALID'
  | 'NOTIFICATION_BIND_FAILED'
  | 'NOTIFICATION_BIND_NOT_IN_SCOPE'
  | 'NOTIFICATION_BIND_TAKEN'
  | 'NOTIFICATION_BIND_MISMATCH'
  | 'NOTIFICATION_BIND_PHONE_REQUIRED'

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
    const err = new Error((data as any).error || `HTTP_${res.status}`) as Error & { code?: string; status?: number }
    err.code = (data as any).error
    // 与 notifications/api.ts NotificationApiError 同口径：落地页按 status
    // 区分登录失效（401 → 提示重新登录）与业务失败
    err.status = res.status
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
    /** 目标挂载的规则（规则级外发目标，installation_ref 服务端生成） */
    rule_id: string
  }) =>
    request<TenantTarget>('/tenant-targets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  /** 通知规则列表（含每规则 active 外发目标数） */
  listRules: () => request<{ rules: NotificationRule[] }>('/rules'),

  createRule: (body: {
    event_type: string
    name: string
    conditions?: NotificationRule['conditions']
    recipient_mode: NotificationRule['recipient_mode']
    enabled?: boolean
  }) =>
    request<{ rule: NotificationRule }>('/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  updateRule: (ruleId: string, body: RuleSaveBody) =>
    request<{ rule: NotificationRule }>(`/rules/${ruleId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  /** 删除自定义规则（is_system 拒绝），级联撤销规则级目标 */
  deleteRule: (ruleId: string) =>
    request<{ rule_id: string; revoked_targets: number }>(`/rules/${ruleId}`, {
      method: 'DELETE',
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

  /** 定时任务可选用通知规则（普通用户只读）：本租户 enabled 的 scheduled.task
   *  规则 + 渠道目标脱敏摘要，供任务编辑弹窗选择 */
  taskRules: () =>
    request<{ rules: TaskNotifyRule[] }>('/task-rules'),

  /** 任务视图投递状态（设计 §8.4）：该任务全部通知事件的投递账 */
  taskDeliveries: (taskId: string) => {
    const q = new URLSearchParams({ task_id: taskId })
    return request<TaskDeliverySummary>(`/task-deliveries?${q.toString()}`)
  },

  /** ---------- 企微应用身份绑定（长任务企微通知 Phase 1，设计 §4.1；设置页 Phase 4） ---------- */

  /** OAuth 授权链接（企业内 oauth2/authorize + PC 扫码 qrConnect，共用同一 state） */
  wecomBindUrl: () =>
    request<WecomBindUrl>('/wecom/bind/url'),

  /** OAuth 回调确认（重定向落地页携 code+state 回调，需登录态；state 一次性消费）。
   *  auth：落地页路径 /notification/... 下的 token key 与发起页不同，显式传入
   *  发起页存于 localStorage 的额外请求头——Authorization，及发起租户上下文
   *  X-Tenant-Id（复审 3：平台管理员的 subject 租户由该头决定），覆盖 request
   *  内的路径推断 */
  wecomBindCallback: (code: string, state: string, auth?: Record<string, string>) => {
    const q = new URLSearchParams({ code, state })
    return request<WecomBindSuccess>(`/wecom/bind/callback?${q.toString()}`, {
      headers: auth,
    })
  },

  /** 绑定状态（Phase 4 设置页）：应用可用性 + 绑定态 + 姓名快照 + target 状态 */
  wecomBindStatus: () =>
    request<WecomBindStatus>('/wecom/bind/status'),

  /** 手机号预填（只读）：按当前登录用户手机号匹配企微通讯录 */
  wecomBindSuggest: () =>
    request<WecomBindSuggest>('/wecom/bind/suggest'),

  /** 确认绑定：服务端按手机号二次核实后才落库，不信任前端传的 userid */
  wecomBindConfirm: (userid: string) =>
    request<WecomBindSuccess>('/wecom/bind/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userid }),
    }),

  /** 解绑：清空 wecom_userid 并撤销既有 wecom_app target */
  wecomBindUnbind: () =>
    request<{ success: boolean }>('/wecom/bind', { method: 'DELETE' }),

  /** ---------- 通知偏好（Phase 4 企微任务通知开关，设计 §4.5） ---------- */

  /** 当前用户偏好列表（task 通知渠道开关状态） */
  preferences: () =>
    request<{ preferences: NotificationPreference[] }>('/preferences'),

  /** 企微任务通知开关：启用=创建/启用 wecom_app 偏好行，关闭=停用 */
  wecomSetPreference: (enabled: boolean) =>
    request<NotificationPreference>('/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: 'task', channel: 'wecom_app', provider: 'wecom_app', enabled }),
    }),
}

export interface WecomBindStatus {
  /** 通知应用可用（enabled + 完整凭据）；不可用时绑定与开关均不开放 */
  app_available: boolean
  bound: boolean
  /** 绑定成员姓名快照（绑定落库时缓存于 target 配置），未绑定/未登记为 null */
  name: string | null
  /** 最近 wecom_app target 状态；target_invalid 撤销后为 'invalid' 提示重绑 */
  target_state: 'active' | 'revoked' | 'invalid' | null
}

export interface NotificationPreference {
  category: string
  channel: string
  provider: string
  enabled: boolean
  enabled_since: string | null
}

export interface WecomBindUrl {
  success: boolean
  state: string
  /** 企业内浏览器 OAuth 授权链接 */
  oauth_url: string
  /** PC 扫码授权页链接（新窗口打开，手机扫码确认后该窗口重定向回落地页） */
  qr_url: string
}

export interface WecomBindSuggest {
  matched: boolean
  userid?: string
  name?: string
}

export interface WecomBindSuccess {
  success: boolean
  userid: string
  name: string
  /** 通知 target 是否登记成功；false 时绑定已落库但需回设置页重试登记 */
  target_registered: boolean
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
