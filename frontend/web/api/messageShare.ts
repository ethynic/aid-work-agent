import type { ChatMessage } from '@/types'
import { getAuthHeader } from './auth'

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api'

export type SharedReplyResult =
  | { state: 'password_required' }
  | { state: 'ready'; message: ChatMessage; access_ticket: string }

async function result<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof body?.detail === 'string' ? body.detail : body?.detail?.error || body?.error
    throw new Error(detail || '请求未成功，请稍后重试。')
  }
  return body as T
}

export async function createMessageShare(sessionId: string, messageId: string, password?: string) {
  return result<{ share_key: string; share_path: string }>(await fetch(
    API_BASE + '/sessions/' + encodeURIComponent(sessionId) + '/messages/' + encodeURIComponent(messageId) + '/share',
    { method: 'POST', headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
      body: JSON.stringify(password ? { password } : {}) },
  ))
}

export async function viewMessageShare(shareKey: string, password?: string): Promise<SharedReplyResult> {
  return result<SharedReplyResult>(await fetch(API_BASE + '/public/reply-shares/' + encodeURIComponent(shareKey) + '/view', {
    method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(password ? { password } : {}),
  }))
}

export function sharedReplyFileUrl(shareKey: string, fileId: string, ticket: string, download = false): string {
  return API_BASE + '/public/reply-shares/' + encodeURIComponent(shareKey) + '/files/' + encodeURIComponent(fileId)
    + '?ticket=' + encodeURIComponent(ticket) + (download ? '&download=1' : '')
}
