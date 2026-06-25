/**
 * 投诉处理智能体 — API 接口
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
  // 租户ID从URL中提取
  const match = path.match(/^\/t\/([^/]+)/)
  if (match) headers['X-Tenant-Id'] = match[1]
  headers['Content-Type'] = 'application/json'
  return headers
}

// --- 类型定义 ---

export interface Complaint {
  complaint_id: string
  tenant_id?: string
  user_id?: string
  session_id?: string
  order_id?: string
  customer_name?: string
  contact_info?: string
  category: string
  sub_category?: string
  tags?: string[]
  customer_emotion?: string
  emotion_intensity?: number
  urgency: string
  status: string
  escalation_level?: number
  description: string
  resolution?: string
  escalated_to?: string
  escalation_reason?: string
  escalated_at?: string
  created_at: string
  updated_at?: string
  resolved_at?: string
  first_response_at?: string
  interaction_count?: number
  description_short?: string
  interactions?: Interaction[]
  followups?: Followup[]
}

export interface Interaction {
  id: number
  interaction_type: string
  sender_type: string
  sender_name?: string
  content: string
  created_at?: string
}

export interface Followup {
  id: number
  action: string
  assigned_to?: string
  due_date?: string
  status: string
  notes?: string
  created_at?: string
}

export interface ComplaintListResponse {
  total: number
  page: number
  page_size: number
  items: Complaint[]
}

export interface ComplaintStats {
  total: number
  status_distribution: Record<string, number>
  category_distribution: { name: string; count: number }[]
  urgency_distribution: Record<string, number>
  daily_trend: { date: string; count: number }[]
  avg_resolution_hours: number
  period: string
}

// --- API 函数 ---

export const complaintAPI = {
  listComplaints: async (params: {
    status?: string
    category?: string
    urgency?: string
    keyword?: string
    page?: number
    page_size?: number
  }): Promise<ComplaintListResponse> => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') query.set(k, String(v)) })
    const res = await fetch(`${API_BASE}/api/complaints/list?${query}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询投诉列表失败')
    return json.data
  },

  getComplaint: async (complaintId: string): Promise<Complaint> => {
    const res = await fetch(`${API_BASE}/api/complaints/detail/${complaintId}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询投诉详情失败')
    return json.data
  },

  getStats: async (period: string = '30d'): Promise<ComplaintStats> => {
    const res = await fetch(`${API_BASE}/api/complaints/stats?period=${period}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询统计失败')
    return json.data
  },

  getInteractions: async (complaintId: string, limit: number = 50): Promise<{ items: Interaction[] }> => {
    const res = await fetch(`${API_BASE}/api/complaints/interactions/${complaintId}?limit=${limit}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询交互记录失败')
    return json.data
  },
}
