import type { ChatMessage } from '@/types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

export interface TaskResultShareView {
  state: 'ready'
  task_name: string
  task_status: string
  runner_status: 'completed' | 'failed'
  finished_at: string
  tenant_id: string
  task_id: string
  runner_id: string
  message: ChatMessage
  access_ticket: string
  viewer_is_owner: boolean
}

export class TaskResultShareError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

export async function viewTaskResultShare(shareKey: string, token?: string, tenantId?: string): Promise<TaskResultShareView> {
  const response = await fetch(API_BASE + '/public/task-result-shares/' + encodeURIComponent(shareKey), {
    credentials: 'omit',
    headers: token ? { Authorization: `Bearer ${token}`, 'X-Tenant-Id': tenantId || '' } : {},
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : body?.detail?.error || body?.error
    throw new TaskResultShareError(detail || '请求未成功，请稍后重试。', response.status)
  }
  return body as TaskResultShareView
}

export function taskResultShareFileUrl(shareKey: string, alias: string, ticket: string, download = false): string {
  return API_BASE + '/public/task-result-shares/' + encodeURIComponent(shareKey) + '/files/' + encodeURIComponent(alias)
    + '?ticket=' + encodeURIComponent(ticket) + (download ? '&download=1' : '')
}
