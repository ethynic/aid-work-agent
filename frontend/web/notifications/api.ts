import { getAuthHeader } from '@/api/auth'

export interface NotificationTarget {
  target_id: string
  installation_ref: string
  binding_version: number
  state: string
  enabled_since: string | null
}
export interface NotificationCapabilities {
  schema_version: number
  enabled: boolean
  web_push: { enabled: boolean; public_key: string; key_id: string }
}
export interface NotificationPreference { category: string; channel: string; provider: string; enabled: boolean; enabled_since: string | null }
export class NotificationApiError extends Error {
  constructor(public status: number) {
    super(status === 409 ? '通知绑定已变化，请刷新后重试。' : status === 401 ? '请登录后查看通知。' : status === 403 ? '当前账号无权查看此通知。' : status === 404 ? '通知或原会话已不存在。' : '通知操作失败，请稍后重试。')
  }
}
const API = `${import.meta.env.VITE_API_BASE_URL || '/api'}/notifications`
export async function notificationRequest<T>(path: string, options: RequestInit = {}, auth = getAuthHeader()): Promise<T> {
  const controller = new AbortController()
  const deadline = setTimeout(() => controller.abort(), 15_000)
  try {
    const res = await fetch(`${API}${path}`, { ...options, signal: controller.signal, headers: { ...auth, 'Content-Type': 'application/json', ...options.headers } })
    if (!res.ok) throw new NotificationApiError(res.status)
    return res.status === 204 ? undefined as T : await res.json()
  } catch (error) {
    if (controller.signal.aborted) throw new Error('通知请求超时，请稍后重试。')
    throw error
  } finally { clearTimeout(deadline) }
}
export const notificationApi = {
  capabilities: (auth?: Record<string, string>) => notificationRequest<NotificationCapabilities>('/capabilities', {}, auth),
  targets: (auth?: Record<string, string>) => notificationRequest<{ targets: NotificationTarget[] }>('/targets', {}, auth),
  preferences: () => notificationRequest<{ preferences: NotificationPreference[] }>('/preferences'),
  enablePreference: (auth?: Record<string, string>) => notificationRequest('/preferences', { method: 'PUT', body: JSON.stringify({ category: 'task', channel: 'web_push', provider: 'web_push', enabled: true }) }, auth),
  saveTarget: (body: unknown, auth?: Record<string, string>) => notificationRequest<NotificationTarget>('/targets', { method: 'POST', body: JSON.stringify(body) }, auth),
  revokeTarget: (target: Pick<NotificationTarget, 'target_id' | 'binding_version'>, auth?: Record<string, string>) => notificationRequest<{ success: boolean; target: NotificationTarget }>(`/targets/${encodeURIComponent(target.target_id)}`, { method: 'DELETE', headers: { 'X-Notification-Binding-Version': String(target.binding_version) } }, auth),
  test: (target: NotificationTarget) => notificationRequest('/test', { method: 'POST', body: JSON.stringify({ target_id: target.target_id, binding_version: target.binding_version }) }),
}
