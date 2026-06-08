/**
 * 子智能体定义管理 API
 */
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/admin/agent-definitions`

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

export interface AgentDefinition {
  id: string
  agent_id: string
  name: string
  description: string | null
  version: string
  author: string | null
  triggers: Record<string, any>
  tools: Record<string, any>
  skills: Record<string, any>
  context: Record<string, any>
  delegatable_to: string[]
  allow_delegation: boolean
  llm_provider: string | null
  reply_style: string | null
  business_pages: any[] | null
  knowledge_sources: { source_type: string; display_name: string }[]
  status: string
  created_by: string | null
  updated_by: string | null
  created_at: string
  updated_at: string
  // 关联的 prompt 信息
  prompt_id?: string
  latest_version?: number
  production_version?: number
}

export interface DefinitionListResult {
  total: number
  page: number
  page_size: number
  items: AgentDefinition[]
}

export interface PromptVersion {
  id: string
  prompt_id: string
  version: number
  content: string
  commit_message: string | null
  content_hash: string | null
  parent_version: number | null
  created_by: string | null
  created_at: string
}

export interface PromptLabel {
  label: string
  version_id: string
  version: number | null
  created_by: string | null
  updated_by: string | null
  created_at: string
  updated_at: string
}

export interface DiffResult {
  from: { version: number; content: string; created_at: string; created_by: string | null }
  to: { version: number; content: string; created_at: string; created_by: string | null }
}

// ============== 定义 CRUD ==============

export async function listDefinitions(params?: {
  status?: string; page?: number; page_size?: number
}): Promise<{ success: boolean; data: DefinitionListResult }> {
  const query = new URLSearchParams()
  if (params?.status) query.set('status', params.status)
  if (params?.page) query.set('page', String(params.page))
  if (params?.page_size) query.set('page_size', String(params.page_size))
  const qs = query.toString()
  const headers = getAuthHeaders()
  const response = await fetch(`${API_BASE}${qs ? '?' + qs : ''}`, { headers })
  return handleResponse(response)
}

export async function getDefinition(agentId: string): Promise<{ success: boolean; data: AgentDefinition }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function createDefinition(data: {
  agent_id: string
  name: string
  system_prompt: string
  description?: string
  tools?: Record<string, any>
  skills?: Record<string, any>
  context?: Record<string, any>
  llm_provider?: string
  reply_style?: string
}): Promise<{ success: boolean; data: any }> {
  const response = await fetch(`${API_BASE}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

export async function updateDefinition(agentId: string, data: Record<string, any>): Promise<{ success: boolean; data: any }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

export async function deleteDefinition(agentId: string): Promise<{ success: boolean }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  })
  return handleResponse(response)
}

// ============== System Prompt 管理 ==============

export async function updateSystemPrompt(agentId: string, data: {
  content: string; commit_message?: string
}): Promise<{ success: boolean; data: any }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/system-prompt`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

export async function listVersions(agentId: string, page = 1, pageSize = 20): Promise<{ success: boolean; data: { total: number; page: number; page_size: number; items: PromptVersion[] } }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/versions?page=${page}&page_size=${pageSize}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function getVersion(agentId: string, version: number): Promise<{ success: boolean; data: PromptVersion }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/versions/${version}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function diffVersions(agentId: string, v1: number, v2: number): Promise<{ success: boolean; data: DiffResult }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/versions/diff?v1=${v1}&v2=${v2}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function getDraft(agentId: string): Promise<{ success: boolean; data: any }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/draft`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function saveDraft(agentId: string, data: {
  content: string; base_version?: number
}): Promise<{ success: boolean; data: any }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/draft`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

export async function commitDraft(agentId: string, data: {
  commit_message?: string
}): Promise<{ success: boolean; data: any }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/draft/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

export async function listLabels(agentId: string): Promise<{ success: boolean; data: PromptLabel[] }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/labels`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function setLabel(agentId: string, label: string, version: number): Promise<{ success: boolean; data: PromptLabel }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/labels/${encodeURIComponent(label)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ version }),
  })
  return handleResponse(response)
}

// ============== Meta (Tools/Skills/ReplyStyles Pickers) ==============

export interface ToolMeta {
  id: string
  name: string
  description: string
}

export interface SkillMeta {
  id: string
  name: string
  description: string
}

export interface ReplyStyleMeta {
  id: string
  name: string
  description: string
}

export async function listToolsMeta(): Promise<{ success: boolean; data: ToolMeta[] }> {
  const response = await fetch(`${API_BASE}/meta/tools`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function listSkillsMeta(): Promise<{ success: boolean; data: SkillMeta[] }> {
  const response = await fetch(`${API_BASE}/meta/skills`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function listReplyStylesMeta(): Promise<{ success: boolean; data: ReplyStyleMeta[] }> {
  const response = await fetch(`${API_BASE}/meta/reply-styles`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

// ============== Sections Management ==============

export interface PromptSection {
  agent_id: string
  section_key: string
  content: string
  updated_by: string | null
  created_at?: string
  updated_at?: string
}

export async function getSectionKeys(agentId: string): Promise<{ success: boolean; data: string[] }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/sections/keys`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function getSections(agentId: string): Promise<{ success: boolean; data: PromptSection[] }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/sections`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function saveSection(
  agentId: string,
  sectionKey: string,
  content: string,
): Promise<{ success: boolean; data: PromptSection }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/sections/${encodeURIComponent(sectionKey)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ content, section_key: sectionKey }),
  })
  return handleResponse(response)
}

export async function optimizeSection(
  agentId: string,
  sectionKey: string,
  data: { content: string; agent_name?: string; agent_description?: string },
): Promise<{ success: boolean; data: { content: string } }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(agentId)}/sections/${encodeURIComponent(sectionKey)}/optimize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ ...data, section_key: sectionKey }),
  })
  return handleResponse(response)
}
