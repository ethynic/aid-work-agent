/**
 * 租户级子智能体模板文件 API。
 * 对应后端 src/api/subagent_template_file.py，前缀 /api/saas/tenant/subagent-templates。
 */
import { getAuthHeader } from '@/api/auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/tenant/subagent-templates`

export interface TemplateFile {
  name: string
  file_id: string
  original_name?: string
  mime_type?: string
  size_bytes?: number
}

async function handleResponse<T>(response: Response): Promise<T> {
  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.detail || data.error || `请求失败: ${response.status}`)
  }
  return data
}

/**
 * 获取某子智能体的模板文件列表
 */
export async function listTemplates(
  subagentName: string,
): Promise<{ success: boolean; data: TemplateFile[]; error?: string }> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(subagentName)}`, {
    headers: getAuthHeader(),
  })
  return handleResponse(response)
}

/**
 * 上传一个模板文件（名称 + 文件），追加到该子智能体模板列表。
 * FormData 上传，getAuthHeader 仅含 Authorization，不干扰 multipart boundary。
 */
export async function uploadTemplate(
  subagentName: string,
  displayName: string,
  file: File,
): Promise<{ success: boolean; data: TemplateFile[]; error?: string }> {
  const formData = new FormData()
  formData.append('name', displayName)
  formData.append('file', file)
  const response = await fetch(`${API_BASE}/${encodeURIComponent(subagentName)}/upload`, {
    method: 'POST',
    headers: getAuthHeader(),
    body: formData,
  })
  return handleResponse(response)
}

/**
 * 删除一个模板文件
 */
export async function deleteTemplate(
  subagentName: string,
  fileId: string,
): Promise<{ success: boolean; data: TemplateFile[]; error?: string }> {
  const response = await fetch(
    `${API_BASE}/${encodeURIComponent(subagentName)}/${encodeURIComponent(fileId)}`,
    {
      method: 'DELETE',
      headers: getAuthHeader(),
    },
  )
  return handleResponse(response)
}
