/**
 * Prompt 版本管理 API（管理后台专用）
 */
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/admin/prompts`

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

export interface PromptListItem {
  id: string
  tenant_id: string | null
  scope: string
  scope_id: string
  prompt_type: string
  display_name: string | null
  description: string | null
  latest_version: number
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface VersionListResult {
  total: number
  page: number
  page_size: number
  items: PromptVersion[]
}

export interface DiffResult {
  from: { version: number; content: string; created_at: string; created_by: string | null }
  to: { version: number; content: string; created_at: string; created_by: string | null }
}

export interface SuggestConfigResult {
  tools: { inherit: boolean; additional: string[] }
  skills: { allowed: string[] }
  reason: string
}

// ============== API 方法 ==============

export async function listPrompts(params?: {
  scope?: string; tenant_id?: string; page?: number; page_size?: number
}): Promise<{ success: boolean; data: { total: number; page: number; page_size: number; items: PromptListItem[] } }> {
  const query = new URLSearchParams()
  if (params?.scope) query.set('scope', params.scope)
  if (params?.tenant_id) query.set('tenant_id', params.tenant_id)
  if (params?.page) query.set('page', String(params.page))
  if (params?.page_size) query.set('page_size', String(params.page_size))
  const qs = query.toString()
  const response = await fetch(`${API_BASE}${qs ? '?' + qs : ''}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function getPrompt(promptId: string): Promise<{ success: boolean; data: PromptListItem }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function getPromptByScope(scopeId: string): Promise<{ success: boolean; data: PromptListItem }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(scopeId)}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function listVersions(promptId: string, page = 1, pageSize = 20): Promise<{ success: boolean; data: VersionListResult }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/versions?page=${page}&page_size=${pageSize}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function getVersion(promptId: string, version: number): Promise<{ success: boolean; data: PromptVersion }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/versions/${version}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function diffVersions(promptId: string, v1: number, v2: number): Promise<{ success: boolean; data: DiffResult }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/versions/diff?v1=${v1}&v2=${v2}`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function commitVersion(promptId: string, data: {
  content: string; commit_message?: string
}): Promise<{ success: boolean; data: { version: PromptVersion | null; dedup: boolean } }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

export async function getDraft(promptId: string): Promise<{ success: boolean; data: any }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/draft`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function saveDraft(promptId: string, data: {
  content: string; base_version?: number
}): Promise<{ success: boolean; data: any }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/draft`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

export async function commitDraft(promptId: string, data: {
  commit_message?: string
}): Promise<{ success: boolean; data: { version: PromptVersion | null; dedup: boolean } }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/draft/commit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}

export async function listLabels(promptId: string): Promise<{ success: boolean; data: PromptLabel[] }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/labels`, { headers: getAuthHeaders() })
  return handleResponse(response)
}

export async function setLabel(promptId: string, label: string, version: number): Promise<{ success: boolean; data: PromptLabel }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(promptId)}/labels/${encodeURIComponent(label)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ version }),
  })
  return handleResponse(response)
}

export async function suggestConfig(data: {
  name: string; description: string
}): Promise<{ success: boolean; data: SuggestConfigResult }> {
  const base = `${import.meta.env.VITE_API_BASE_URL || '/api'}/admin/subagents`
  const response = await fetch(`${base}/suggest-config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  return handleResponse(response)
}
