/**
 * 会话管理 API
 */

import { getAuthHeader as getNormalAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/sessions`

// 从 URL 路径提取 tenant_id
function getCurrentTenantId(): string | null {
  const path = window.location.pathname
  const match = path.match(/^\/t\/([^/]+)/)
  return match ? match[1] : null
}

// 根据路由获取正确的认证头（包含 X-Tenant-Id）
function getAuthHeader(): Record<string, string> {
  const path = window.location.pathname
  let headers: Record<string, string> = {}

  // 租户/平台路由使用 saas_token 或 portal_token
  if (path.startsWith('/t/')) {
    const saasToken = localStorage.getItem('saas_token')
    if (saasToken) headers['Authorization'] = `Bearer ${saasToken}`
  } else if (path.startsWith('/portal')) {
    const portalToken = localStorage.getItem('portal_token')
    if (portalToken) headers['Authorization'] = `Bearer ${portalToken}`
  } else {
    // 普通路由使用 demo_token
    headers = getNormalAuthHeader()
  }

  // 租户路由下传递 X-Tenant-Id，用于租户隔离
  const tenantId = getCurrentTenantId()
  if (tenantId) {
    headers['X-Tenant-Id'] = tenantId
  }

  return headers
}

export interface ChatSession {
  session_id: string
  user_id: string
  tenant_id?: string
  subagent_id?: string
  instance_id?: string
  title: string
  context_data?: Record<string, any>
  created_at: string
  updated_at: string
}

export interface ChatMessageRecord {
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
  subagent_id?: string
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
  messages: ChatMessageRecord[]
}

/**
 * 获取当前用户的所有会话（分页）
 */
export async function listSessions(page: number = 1, pageSize: number = 20): Promise<{
  sessions: ChatSession[],
  total: number,
  page: number,
  page_size: number
}> {
  const res = await fetch(`${API_BASE}?page=${page}&page_size=${pageSize}`, {
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
export async function updateSession(sessionId: string, data: { title?: string, context_data?: Record<string, any>, subagent_id?: string }): Promise<ChatSession> {
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
export async function getSessionMessages(sessionId: string): Promise<{ messages: ChatMessageRecord[] }> {
  const res = await fetch(`${API_BASE}/${sessionId}/messages`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch messages')
  return res.json()
}

/**
 * 添加消息到会话
 */
export async function addSessionMessage(sessionId: string, role: string, content: string, metadata?: Record<string, any>): Promise<ChatMessageRecord> {
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

// ============== 会话记录相关 ==============

export interface ChatRecord {
  record_id: string
  session_id: string
  tenant_id: string
  user_id: string
  user_message: string
  assistant_message: string
  total_token_count: number
  prompt_tokens: number
  completion_tokens: number
  cached_input_tokens: number
  model: string
  provider: string
  execution_details: {
    tool_executions: Array<{
      tool_name: string
      tool_args: Record<string, any>
      result: string
      success: boolean
      error: string
      duration_ms: number
    }>
    total_iterations: number
    subagent_calls: Array<any>
    plan_id: string
    plan_steps: Array<any>
  }
  agent_iterations: number
  status: 'completed' | 'failed'
  error_message: string
  duration_ms: number
  created_at: string
}

export interface TokenUsage {
  session_id: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
}

/**
 * 获取当前用户的最近会话
 */
export async function getLatestSession(): Promise<{ session: ChatSession | null }> {
  const res = await fetch(`${API_BASE}/latest`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch latest session')
  return res.json()
}

/**
 * 获取会话的所有记录
 */
export async function getSessionRecords(sessionId: string, limit: number = 100): Promise<{ records: ChatRecord[] }> {
  const res = await fetch(`${API_BASE}/${sessionId}/records?limit=${limit}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch records')
  return res.json()
}

/**
 * 获取会话的Token消耗统计
 */
export async function getSessionTokenUsage(sessionId: string): Promise<TokenUsage> {
  const res = await fetch(`${API_BASE}/${sessionId}/token-usage`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch token usage')
  return res.json()
}
