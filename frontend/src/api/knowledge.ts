/**
 * 知识库 API
 */
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/knowledge`

export interface DocumentResponse {
  id: number
  title: string
  source_type: string
  file_type: string
  file_path: string | null
  file_size: number | null
  total_chunks: number
  created_at: string
  summary: string | null
}

export interface UploadResponse {
  document_id: number
  title: string
  total_chunks: number
  status: string
  message: string
}

export interface SearchResultItem {
  doc_id: number
  chunk_id: number
  text: string
  title: string
  file_type: string
  file_path: string | null
  score: number
}

export interface SearchResponse {
  success: boolean
  results: SearchResultItem[]
  count: number
  error?: string
  debug?: string
}

export interface ApiResponse<T = any> {
  success: boolean
  data?: T
  error?: string
  debug?: string
}

export interface DocumentListResponse {
  items: DocumentResponse[]
  total: number
}

/**
 * 获取知识库文档列表（分页）
 */
export async function listDocuments(limit = 100, offset = 0): Promise<DocumentListResponse> {
  const response = await fetch(`${API_BASE}/documents?limit=${limit}&offset=${offset}`, {
    headers: { ...getAuthHeader() }
  })
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
    method: 'DELETE',
    headers: { ...getAuthHeader() }
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
    headers: { ...getAuthHeader() },
    body: formData
  })
  let result: any
  try {
    result = await response.json()
  } catch (e) {
    throw new Error('服务器响应异常')
  }
  // 检查 HTTP 状态码和业务字段
  // 注意：后端成功时返回 UploadResponse（无 success 字段，有 document_id）
  //       失败时返回 { success: false, error: "..." }
  const isSuccess = response.ok && (result.success !== false || result.document_id)
  if (!isSuccess) {
    throw new Error(result.error || result.detail || result.message || '上传失败')
  }
  return result
}

/**
 * 获取文档分块（用于调试）
 */
export async function getDocumentChunks(docId: number): Promise<any> {
  const response = await fetch(`${API_BASE}/documents/${docId}/chunks`, {
    headers: { ...getAuthHeader() }
  })
  return response.json()
}

/**
 * 获取文档下载/预览 URL
 */
export function getDocumentDownloadUrl(docId: number): string {
  return `${API_BASE}/documents/${docId}/download`
}

/**
 * 搜索知识库文档（混合检索：向量 + FTS5 + RRF）
 */
export async function searchDocuments(query: string, top_k = 10): Promise<SearchResponse> {
  const response = await fetch(`${API_BASE}/search_documents`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({ query, top_k })
  })
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.error || '搜索失败')
  }
  return response.json()
}
