/**
 * 外部接待客户 API
 */

import { getTenantScopedKey } from './tenantStorage'
import { credentialGet } from '@/platform/credentialStore'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/external-customers`

export function getSaasAuthHeader(): Record<string, string> {
  const headers: Record<string, string> = {}

  // 根据路由获取对应的 token（租户前台按 tenant_id 隔离，portal 共用）
  const path = window.location.pathname
  let tokenKey: string
  if (import.meta.env.VITE_DESKTOP_TARGET !== 'true' && path.startsWith('/portal')) {
    tokenKey = 'portal_token'
  } else if (path.startsWith('/t/')) {
    tokenKey = getTenantScopedKey('saas_token')
  } else {
    tokenKey = 'saas_token'
  }
  const token = credentialGet(tokenKey)
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  // 从 URL 路径获取 tenant_id，添加到 Header
  const match = path.match(/^\/t\/([^/]+)/)
  const tenantId = match ? match[1] : null
  if (tenantId) {
    headers['X-Tenant-Id'] = tenantId
  }

  return headers
}

// 获取外部用户列表
export async function listExternalUsers(params: {
  username?: string
  source?: string
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
  if (params.username) searchParams.set('username', params.username)
  if (params.source) searchParams.set('source', params.source)
  if (params.page) searchParams.set('page', params.page.toString())
  if (params.page_size) searchParams.set('page_size', params.page_size.toString())

  const res = await fetch(`${API_BASE}/users?${searchParams}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取外部用户列表失败')
  return res.json()
}

// 获取用户的会话列表
export async function getUserSessions(params: {
  user_id: string
  instance_id?: string
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
  if (params.instance_id) searchParams.set('instance_id', params.instance_id)
  if (params.page) searchParams.set('page', params.page.toString())
  if (params.page_size) searchParams.set('page_size', params.page_size.toString())

  const res = await fetch(`${API_BASE}/users/${params.user_id}/sessions?${searchParams}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取用户会话列表失败')
  return res.json()
}

// 获取会话的消息列表
export async function getSessionMessages(params: {
  session_id: string
  content_search?: string
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
  if (params.content_search) searchParams.set('content_search', params.content_search)
  if (params.page) searchParams.set('page', params.page.toString())
  if (params.page_size) searchParams.set('page_size', params.page_size.toString())

  const res = await fetch(`${API_BASE}/sessions/${params.session_id}/messages?${searchParams}`, {
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('获取会话消息列表失败')
  return res.json()
}
