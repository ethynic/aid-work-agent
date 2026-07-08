/**
 * 外贸客户信息管理 API
 */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/trade-specialist`

export interface Customer {
  customer_id: string
  user_id: string
  session_id: string
  company_name: string
  contact_name: string
  email: string
  country: string
  language: string
  industry: string
  import_category: string
  company_size: string
  match_reason: string
  match_date: string
  created_at: string
}

export interface CustomerEmail {
  email_id: string
  customer_id: string
  user_id: string
  session_id: string
  email_subject: string
  email_body: string
  email_language: string
  send_time: string
  send_status: 'success' | 'failed' | 'pending'
  error_message?: string
  created_at: string
  company_name?: string
  contact_name?: string
}

export interface CustomerWithEmails extends Customer {
  emails: CustomerEmail[]
}

export interface CustomerStats {
  total_customers: number
  total_emails: number
  success_emails: number
  failed_emails: number
  recent_customers: number
  recent_emails: number
  country_distribution: Array<{ country: string; count: number }>
}

export interface CustomerListResponse {
  success: boolean
  data?: {
    count: number
    customers: Customer[]
  }
  error?: string
  debug?: string
}

export interface CustomerDetailResponse {
  success: boolean
  data?: CustomerWithEmails
  error?: string
  debug?: string
}

export interface EmailListResponse {
  success: boolean
  data?: {
    count: number
    emails: CustomerEmail[]
  }
  error?: string
  debug?: string
}

export interface StatsResponse {
  success: boolean
  data?: CustomerStats
  error?: string
  debug?: string
}

/**
 * 获取客户列表
 */
export async function listCustomers(userId: string, sessionId?: string): Promise<CustomerListResponse> {
  const params = new URLSearchParams({ user_id: userId })
  if (sessionId) {
    params.append('session_id', sessionId)
  }

  const res = await fetch(`${API_BASE}/customers?${params}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch customers')
  return res.json()
}

/**
 * 获取客户详情
 */
export async function getCustomer(customerId: string): Promise<CustomerDetailResponse> {
  const res = await fetch(`${API_BASE}/customers/${customerId}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch customer')
  return res.json()
}

/**
 * 获取邮件历史
 */
export async function listEmails(customerId?: string, userId?: string, limit: number = 100): Promise<EmailListResponse> {
  const params = new URLSearchParams()
  if (customerId) {
    params.append('customer_id', customerId)
  }
  if (userId) {
    params.append('user_id', userId)
  }
  params.append('limit', limit.toString())

  const res = await fetch(`${API_BASE}/emails?${params}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch emails')
  return res.json()
}

/**
 * 获取统计信息
 */
export async function getStats(userId: string): Promise<StatsResponse> {
  const res = await fetch(`${API_BASE}/stats?user_id=${userId}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch stats')
  return res.json()
}

/**
 * 获取会话的客户列表
 */
export async function getSessionCustomers(sessionId: string): Promise<CustomerListResponse> {
  const res = await fetch(`${API_BASE}/session/${sessionId}/customers`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('Failed to fetch session customers')
  return res.json()
}
