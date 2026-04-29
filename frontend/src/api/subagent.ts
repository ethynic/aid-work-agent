/**
 * 数字员工 API（只读）
 */
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/subagents`

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

// ============== API 方法（只读） ==============

/**
 * 获取所有数字员工列表
 */
export async function listSubagents(): Promise<{ success: boolean; data: SubagentListItem[]; error?: string }> {
  const response = await fetch(`${API_BASE}`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 获取单个数字员工详情
 */
export async function getSubagentDetail(agentId: string): Promise<{ success: boolean; data: SubagentDetail; error?: string }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 获取 SUBAGENT.md 原始内容
 */
export async function getSubagentContent(agentId: string): Promise<{ success: boolean; data: string; error?: string }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/content`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

/**
 * 获取可选 skills 列表
 */
export async function listAvailableSkills(): Promise<{ success: boolean; data: string[]; error?: string }> {
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
  error?: string
}> {
  const response = await fetch(`${API_BASE}/tools`, {
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}
