/**
 * 客户跟进智能体 — API 接口
 */

const API_BASE = import.meta.env.VITE_API_BASE || ''

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('token')
  const tenantId = localStorage.getItem('tenantId') || ''
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (tenantId) headers['X-Tenant-Id'] = tenantId
  headers['Content-Type'] = 'application/json'
  return headers
}

// --- 类型定义 ---

export interface Lead {
  lead_id: string
  tenant_id?: string
  user_id?: string
  company_name: string
  contact_name: string
  phone: string
  email: string
  source: string
  industry: string
  region: string
  stage: string
  status: string
  score: number
  assigned_to?: string
  next_followup_at?: string
  followup_count: number
  tags: string[]
  created_at: string
  updated_at: string
  recent_records?: FollowupRecord[]
}

export interface FollowupRecord {
  record_id: string
  lead_id: string
  user_id: string
  followup_type: string
  content: string
  outcome?: string
  quality_score?: number
  followup_at: string
  duration_minutes?: number
  call_sentiment?: string
  call_id?: string
  call_transcript?: string
  call_summary?: string
  next_action?: string
  next_followup_at?: string
  company_name?: string
  contact_name?: string
}

export interface SalesRep {
  rep_id: string
  user_id: string
  name: string
  department?: string
  role: string
  active_lead_count: number
  max_leads: number
  is_active: boolean
  skills: string[]
  region?: string
  created_at: string
}

export interface AssignRule {
  rule_id: string
  name: string
  rule_type: string
  priority: number
  is_active: boolean
  conditions: Record<string, any>
  target_rep_ids: string[]
  auto_assign: boolean
  created_at: string
}

export interface LeadListResponse {
  total: number
  page: number
  page_size: number
  items: Lead[]
}

export interface DashboardData {
  total_active: number
  by_stage: Record<string, number>
  by_source: Record<string, number>
  overdue_followups: number
}

// --- API 函数 ---

export const followupAPI = {
  // 线索管理
  listLeads: async (params: {
    user_id?: string
    stage?: string
    status?: string
    assigned_to?: string
    keyword?: string
    page?: number
    page_size?: number
  }): Promise<LeadListResponse> => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') query.set(k, String(v)) })
    const res = await fetch(`${API_BASE}/api/followup/leads?${query}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询线索失败')
    return json.data
  },

  getLead: async (leadId: string): Promise<Lead> => {
    const res = await fetch(`${API_BASE}/api/followup/leads/${leadId}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询线索详情失败')
    return json.data
  },

  createLead: async (data: Partial<Lead> & { user_id: string }): Promise<{ lead_id: string }> => {
    const query = new URLSearchParams()
    query.set('user_id', data.user_id!)
    const res = await fetch(`${API_BASE}/api/followup/leads?${query}`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '创建线索失败')
    return json.data
  },

  updateLead: async (leadId: string, data: Partial<Lead>): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/followup/leads/${leadId}`, {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '更新线索失败')
  },

  updateStage: async (leadId: string, stage: string, note?: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/followup/leads/${leadId}/stage`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ stage, note }),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '变更阶段失败')
  },

  deleteLead: async (leadId: string, reason?: string): Promise<void> => {
    const query = reason ? `?reason=${encodeURIComponent(reason)}` : ''
    const res = await fetch(`${API_BASE}/api/followup/leads/${leadId}${query}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '删除线索失败')
  },

  assignLead: async (leadId: string, assignedTo: string, rule?: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/followup/leads/${leadId}/assign`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({ assigned_to: assignedTo, rule }),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '分配线索失败')
  },

  // 跟进记录
  listRecords: async (params: {
    lead_id?: string
    user_id?: string
    date_from?: string
    date_to?: string
    limit?: number
  }): Promise<{ items: FollowupRecord[] }> => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') query.set(k, String(v)) })
    const res = await fetch(`${API_BASE}/api/followup/records?${query}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询跟进记录失败')
    return json.data
  },

  createRecord: async (data: {
    lead_id: string
    followup_type: string
    content: string
    outcome?: string
    next_action?: string
    next_followup_at?: string
    duration_minutes?: number
  }, userId: string): Promise<{ record_id: string }> => {
    const query = new URLSearchParams()
    query.set('user_id', userId)
    const res = await fetch(`${API_BASE}/api/followup/records?${query}`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '创建跟进记录失败')
    return json.data
  },

  // 仪表板
  getDashboard: async (): Promise<DashboardData> => {
    const res = await fetch(`${API_BASE}/api/followup/dashboard`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '获取仪表板数据失败')
    return json.data
  },

  // 销售人员
  listReps: async (): Promise<{ items: SalesRep[]; total: number }> => {
    const res = await fetch(`${API_BASE}/api/followup/reps`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询销售人员失败')
    return json.data
  },

  createRep: async (data: {
    user_id: string
    name: string
    department?: string
    role?: string
    max_leads?: number
    region?: string
    skills?: string[]
  }): Promise<{ rep_id: string }> => {
    const res = await fetch(`${API_BASE}/api/followup/reps`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '创建销售人员失败')
    return json.data
  },

  updateRep: async (repId: string, data: Partial<SalesRep>): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/followup/reps/${repId}`, {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '更新销售人员失败')
  },

  // 分配规则
  listAssignRules: async (): Promise<{ items: AssignRule[]; total: number }> => {
    const res = await fetch(`${API_BASE}/api/followup/assign-rules`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询分配规则失败')
    return json.data
  },

  createAssignRule: async (data: {
    name: string
    rule_type: string
    priority?: number
    conditions?: Record<string, any>
    target_rep_ids?: string[]
    auto_assign?: boolean
  }): Promise<{ rule_id: string }> => {
    const res = await fetch(`${API_BASE}/api/followup/assign-rules`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '创建分配规则失败')
    return json.data
  },

  updateAssignRule: async (ruleId: string, data: Partial<AssignRule>): Promise<void> => {
    const res = await fetch(`${API_BASE}/api/followup/assign-rules/${ruleId}`, {
      method: 'PUT',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '更新分配规则失败')
  },

  // 批量分配
  batchAssign: async (data: {
    rule: string
    unassigned_only?: boolean
  }): Promise<{ total: number; assigned: number; skipped: number }> => {
    const res = await fetch(`${API_BASE}/api/followup/leads/batch-assign`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '批量分配失败')
    return json.data
  },

  // AI 外呼
  callLead: async (leadId: string, data: {
    call_purpose?: string
    script_hint?: string
    max_duration?: number
  }): Promise<any> => {
    const res = await fetch(`${API_BASE}/api/followup/leads/${leadId}/call`, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(data),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '发起外呼失败')
    return json.data
  },

  listCallRecords: async (params?: {
    lead_id?: string
    limit?: number
  }): Promise<{ items: FollowupRecord[] }> => {
    const query = new URLSearchParams()
    if (params) {
      Object.entries(params).forEach(([k, v]) => { if (v !== undefined) query.set(k, String(v)) })
    }
    const res = await fetch(`${API_BASE}/api/followup/call-records?${query}`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '查询外呼记录失败')
    return json.data
  },

  // 质量评估
  evaluateRecord: async (recordId: string): Promise<{ record_id: string; quality_score: number }> => {
    const res = await fetch(`${API_BASE}/api/followup/records/${recordId}/evaluate`, {
      method: 'POST',
      headers: getAuthHeaders(),
    })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '评估失败')
    return json.data
  },

  // 逾期跟进
  getOverdue: async (): Promise<{ items: Lead[]; total: number }> => {
    const res = await fetch(`${API_BASE}/api/followup/overdue`, { headers: getAuthHeaders() })
    const json = await res.json()
    if (!json.success) throw new Error(json.error || '获取逾期跟进失败')
    return json.data
  },
}
