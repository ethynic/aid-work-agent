/**
 * 网页端会话 API（管理员查看 web 端聊天记录）
 */

import { getSaasAuthHeader } from './externalCustomers'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/web-sessions`

// 获取有网页端会话的用户列表
export async function listWebSessionUsers(params: {
  keyword?: string
  page?: number
  page_size?: number
}): Promise<{
  success: boolean
  users?: any[]
  total?: number
  page?: number
  page_size?: number
  message?: string
}> {
  const searchParams = new URLSearchParams()
  if (params.keyword) searchParams.set('keyword', params.keyword)
  if (params.page) searchParams.set('page', params.page.toString())
  if (params.page_size) searchParams.set('page_size', params.page_size.toString())

  const res = await fetch(`${API_BASE}/users?${searchParams}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用户列表失败')
  return res.json()
}

// 获取用户网页端会话中出现过的智能体去重列表（下拉框选项）
export async function listUserSessionAgents(user_id: string): Promise<{
  success: boolean
  agents?: { agent_id: string; agent_name: string; session_count: number }[]
  message?: string
}> {
  const res = await fetch(`${API_BASE}/users/${encodeURIComponent(user_id)}/agents`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取智能体列表失败')
  return res.json()
}

// 获取用户的网页端会话列表
export async function getUserWebSessions(params: {
  user_id: string
  subagent_id?: string
  page?: number
  page_size?: number
}): Promise<{
  success: boolean
  sessions?: any[]
  total?: number
  page?: number
  page_size?: number
  message?: string
}> {
  const searchParams = new URLSearchParams()
  if (params.subagent_id) searchParams.set('subagent_id', params.subagent_id)
  if (params.page) searchParams.set('page', params.page.toString())
  if (params.page_size) searchParams.set('page_size', params.page_size.toString())

  const res = await fetch(`${API_BASE}/users/${encodeURIComponent(params.user_id)}/sessions?${searchParams}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取会话列表失败')
  return res.json()
}

// 获取网页端会话的消息列表
export async function getWebSessionMessages(params: {
  session_id: string
  page?: number
  page_size?: number
}): Promise<{
  success: boolean
  messages?: any[]
  total?: number
  page?: number
  page_size?: number
  message?: string
}> {
  const searchParams = new URLSearchParams()
  if (params.page) searchParams.set('page', params.page.toString())
  if (params.page_size) searchParams.set('page_size', params.page_size.toString())

  const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(params.session_id)}/messages?${searchParams}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取聊天记录失败')
  return res.json()
}
