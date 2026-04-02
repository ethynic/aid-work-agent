/**
 * 知识库 API
 */
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/knowledge`

export interface DocumentResponse {
  id: number
  title: string
  source_type: string
  file_type: string
  file_size: number | null
  total_chunks: number
  created_at: string
}

export interface UploadResponse {
  document_id: number
  title: string
  total_chunks: number
  status: string
  message: string
}

export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  error?: string
  debug?: string
}

/**
 * 获取知识库文档列表
 */
export async function listDocuments(limit = 100, offset = 0): Promise<DocumentResponse[]> {
  const response = await fetch(`${API_BASE}/documents?limit=${limit}&offset=${offset}`)
  if (!response.ok) {
    throw new Error(`获取文档列表失败: ${response.status}`)
  }
  return response.json()
}

/**
 * 删除知识库文档
 */
export async function deleteDocument(docId: number): Promise<ApiResponse> {
  const response = await fetch(`${API_BASE}/documents/${docId}`, {
    method: 'DELETE'
  })
  return response.json()
}

/**
 * 上传知识库文档
 */
export async function uploadDocument(file: File): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    body: formData
  })
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.error || '上传失败')
  }
  return response.json()
}

/**
 * 获取文档分块（用于调试）
 */
export async function getDocumentChunks(docId: number): Promise<any> {
  const response = await fetch(`${API_BASE}/documents/${docId}/chunks`)
  return response.json()
}
