/**
 * 会话管理 API
 */

import { getAuthHeader } from './auth'

const API_BASE = '/api/sessions'

export interface ChatSession {
  session_id: string
  user_id: string
  title: string
  context_data?: Record<string, any>
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  message_id: string
  session_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  metadata?: Record<string, any>
  created_at: string
}

export interface CreateSessionRequest {
  title?: string
  context_data?: Record<string, any>
}

export interface SessionContext {
  user_info: {
    user_id: string
    username: string
    phone?: string
  }
  session_info: {
    session_id: string
    title: string
    created_at: string
  }
  messages: ChatMessage[]
}

/**
 * 获取当前用户的所有会话
 */
export async function listSessions(): Promise<{ sessions: ChatSession[] }> {
  const res = await fetch(API_BASE, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch sessions')
  return res.json()
}

/**
 * 创建新会话
 */
export async function createSession(data?: CreateSessionRequest): Promise<ChatSession> {
  const res = await fetch(API_BASE, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(data || {})
  })
  if (!res.ok) throw new Error('Failed to create session')
  return res.json()
}

/**
 * 获取会话详情
 */
export async function getSession(sessionId: string): Promise<ChatSession> {
  const res = await fetch(`${API_BASE}/${sessionId}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch session')
  return res.json()
}

/**
 * 更新会话
 */
export async function updateSession(sessionId: string, data: { title?: string, context_data?: Record<string, any> }): Promise<ChatSession> {
  const res = await fetch(`${API_BASE}/${sessionId}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('Failed to update session')
  return res.json()
}

/**
 * 删除会话
 */
export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/${sessionId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to delete session')
}

/**
 * 获取会话消息
 */
export async function getSessionMessages(sessionId: string): Promise<{ messages: ChatMessage[] }> {
  const res = await fetch(`${API_BASE}/${sessionId}/messages`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch messages')
  return res.json()
}

/**
 * 添加消息到会话
 */
export async function addSessionMessage(sessionId: string, role: string, content: string, metadata?: Record<string, any>): Promise<ChatMessage> {
  const res = await fetch(`${API_BASE}/${sessionId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({ role, content, metadata })
  })
  if (!res.ok) throw new Error('Failed to add message')
  return res.json()
}

/**
 * 获取会话上下文（用户信息+聊天历史）
 */
export async function getSessionContext(sessionId: string): Promise<SessionContext> {
  const res = await fetch(`${API_BASE}/${sessionId}/context`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch context')
  return res.json()
}
