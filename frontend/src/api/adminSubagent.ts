/**
 * 数字员工管理 API（管理后台专用）
 * 包含完整的 CRUD + AI 完善功能
 */
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/admin/subagents`

function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('portal_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function handleResponse<T>(response: Response): Promise<T> {
  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.error || `请求失败: ${response.status}`)
  }
  return data
}

// ============== 类型定义 ==============

export interface BusinessPage {
  id: string
  title: string
  icon: string
  route: string
}

export interface SubagentListItem {
  agent_id: string
  name: string
  description: string
  capabilities: string[]
  type: 'builtin' | 'custom'
  business_pages?: BusinessPage[]
}

export interface SubagentDetail {
  agent_id: string
  name: string
  description: string
  version: string
  author: string
  capabilities: string[]
  triggers: Record<string, any>
  tools: Record<string, any>
  skills: Record<string, any>
  context: Record<string, any>
  system_prompt: string
  type: 'builtin' | 'custom'
  business_pages?: BusinessPage[]
}

export interface CreateSubagentRequest {
  agent_id: string
  name: string
  description?: string
  capabilities?: string[]
  triggers?: Record<string, any>
  tools?: Record<string, any>
  skills?: Record<string, any>
  context?: Record<string, any>
  system_prompt?: string
}

export interface DuplicateRequest {
  new_agent_id: string
  new_name: string
}

export interface AiEnhanceResponse {
  original_content: string
  enhanced_content: string
}

// ============== API 方法 ==============

/**
 * 获取所有数字员工列表
 */
export async function listSubagents(): Promise<{ success: boolean; data: SubagentListItem[] }> {
  const response = await fetch(`${API_BASE}`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 获取单个数字员工详情
 */
export async function getSubagentDetail(agentId: string): Promise<{ success: boolean; data: SubagentDetail }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 获取 SUBAGENT.md 原始内容
 */
export async function getSubagentContent(agentId: string): Promise<{ success: boolean; data: string }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/content`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 获取可选 skills 列表
 */
export async function listAvailableSkills(): Promise<{ success: boolean; data: string[] }> {
  const response = await fetch(`${API_BASE}/skills`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 获取可选 tools 列表
 */
export async function listAvailableTools(): Promise<{
  success: boolean
  data: Array<{ name: string; description: string; display_name: string }>
}> {
  const response = await fetch(`${API_BASE}/tools`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 创建定制数字员工
 */
export async function createSubagent(data: CreateSubagentRequest): Promise<{ success: boolean; data?: any; error?: string }> {
  const response = await fetch(`${API_BASE}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

/**
 * 更新定制数字员工
 */
export async function updateSubagent(agentId: string, data: CreateSubagentRequest): Promise<{ success: boolean; data?: any; error?: string }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

/**
 * 删除定制数字员工
 */
export async function deleteSubagent(agentId: string): Promise<{ success: boolean; message?: string; error?: string }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 另存为
 */
export async function duplicateSubagent(agentId: string, data: DuplicateRequest): Promise<{ success: boolean; data?: any; error?: string }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/duplicate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

/**
 * AI 完善（同步请求）
 */
export async function aiEnhanceSubagent(agentId: string, content: string): Promise<{ success: boolean; data?: AiEnhanceResponse; error?: string }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/ai-enhance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ content }),
  })
  return handleResponse(response)
}
