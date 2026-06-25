/**
 * 售后服务智能体 — API 接口
 */

import { getTenantScopedKey } from './tenantStorage'

const API_BASE = import.meta.env.VITE_API_BASE || ''

function getAuthHeaders(): Record<string, string> {
  const path = window.location.pathname
  let tokenKey: string
  if (path.startsWith('/t/')) {
    tokenKey = getTenantScopedKey('saas_token')
  } else if (path.startsWith('/portal')) {
    tokenKey = 'portal_token'
  } else {
    tokenKey = 'demo_token'
  }
  const token = localStorage.getItem(tokenKey)
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const match = path.match(/^\/t\/([^/]+)/)
  if (match) headers['X-Tenant-Id'] = match[1]
  headers['Content-Type'] = 'application/json'
  return headers
}

// --- 类型定义 ---

export interface Ticket {
  ticket_id: string
  tenant_id?: string
  user_id?: string
  session_id?: string
  order_id?: string
  category: string
  status: string
  priority: string
  description: string
  description_short?: string
  resolution?: string
  external_ticket_id?: string
  message_count?: number
  created_at: string
  updated_at?: string
  messages?: TicketMessage[]
}

export interface TicketMessage {
  id: number
  sender_type: string
  content: string
  created_at?: string
}

export interface ReturnRecord {
  return_id: string
  order_id: string
  type: string
  reason: string
  status: string
  items?: any
  refund_amount?: number
  created_at: string
  updated_at?: string
}

export interface TicketListResponse {
  total: number
  page: number
  page_size: number
  items: Ticket[]
}

export interface ReturnListResponse {
  total: number
  page: number
  page_size: number
  items: ReturnRecord[]
}

// --- API 函数 ---

export const afterSalesAPI = {
  listTickets: async (params: {
    status?: string
    category?: string
    priority?: string
    keyword?: string
    page?: number
    page_size?: number
  }): Promise<TicketListResponse> => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') query.set(k, String(v)) })
    const res = await fetch(`${API_BASE}/api/after-sales/tickets?${query}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询工单列表失败')
    return json.data
  },

  getTicket: async (ticketId: string): Promise<Ticket> => {
    const res = await fetch(`${API_BASE}/api/after-sales/tickets/${ticketId}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询工单详情失败')
    return json.data
  },

  listReturns: async (params: {
    status?: string
    return_type?: string
    keyword?: string
    page?: number
    page_size?: number
  }): Promise<ReturnListResponse> => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') query.set(k, String(v)) })
    const res = await fetch(`${API_BASE}/api/after-sales/returns?${query}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询退换货记录失败')
    return json.data
  },
}
