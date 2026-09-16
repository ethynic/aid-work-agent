/**
 * 知识库 API
 */
import { getAuthHeader } from './auth'
import { triggerNativeDownload } from '@/utils/download'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/knowledge`

export interface DocumentResponse {
  id: number
  title: string
  source_type: string
  sub_category: string | null
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

export interface BatchUploadError {
  filename: string
  error: string
}

export interface BatchUploadResponse {
  success: boolean
  results: UploadResponse[]
  errors: BatchUploadError[]
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
export async function listDocuments(limit = 100, offset = 0, sourceType?: string, subCategory?: string): Promise<DocumentListResponse> {
  let url = `${API_BASE}/documents?limit=${limit}&offset=${offset}`
  if (sourceType) {
    url += `&source_type=${encodeURIComponent(sourceType)}`
  }
  if (subCategory) {
    url += `&sub_category=${encodeURIComponent(subCategory)}`
  }
  const response = await fetch(url, {
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

export interface MoveDocumentsResponse {
  success: boolean
  moved: number
  skipped: number
  error?: string
  debug?: string
}

/**
 * 批量移动知识库文档到目标分类
 * @param docIds 文档 ID 列表
 * @param sourceType 目标顶级分类代号
 * @param subCategory 目标直接所属子分类代号（顶级分类下传 null）
 */
export async function moveDocuments(
  docIds: number[],
  sourceType: string,
  subCategory?: string | null
): Promise<MoveDocumentsResponse> {
  const response = await fetch(`${API_BASE}/documents/move`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({
      doc_ids: docIds,
      source_type: sourceType,
      sub_category: subCategory ?? null
    })
  })
  const result = await response.json()
  if (!response.ok || result.success === false) {
    throw new Error(result.error || '移动文档失败')
  }
  return result
}

/**
 * 上传知识库文档（单文件）
 */
export async function uploadDocument(file: File, sourceType?: string, subCategory?: string): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append('file', file)
  if (sourceType) {
    formData.append('source_type', sourceType)
  }
  if (subCategory) {
    formData.append('sub_category', subCategory)
  }

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
 * 批量上传知识库文档（多文件）
 */
export async function uploadDocumentsBatch(files: File[], sourceType?: string, subCategory?: string): Promise<BatchUploadResponse> {
  const formData = new FormData()
  files.forEach(file => {
    formData.append('files', file)
  })
  if (sourceType) {
    formData.append('source_type', sourceType)
  }
  if (subCategory) {
    formData.append('sub_category', subCategory)
  }

  const response = await fetch(`${API_BASE}/upload/batch`, {
    method: 'POST',
    headers: { ...getAuthHeader() },
    body: formData
  })
  if (!response.ok) {
    throw new Error('批量上传失败')
  }
  return response.json()
}

/**
 * 分块详情
 */
export interface ChunkResponse {
  chunk_id: number
  index: number
  text: string
  tokens: number
  metadata: Record<string, any>
  has_vector: boolean
  vector_text: string | null
}

export interface ChunkListResponse {
  success: boolean
  doc_id: number
  chunks: ChunkResponse[]
  count: number
}

/**
 * 获取文档分块（含向量数据）
 */
export async function getDocumentChunks(docId: number): Promise<ChunkListResponse> {
  const response = await fetch(`${API_BASE}/documents/${docId}/chunks`, {
    headers: { ...getAuthHeader() }
  })
  return response.json()
}

/**
 * 下载文档原文（浏览器原生下载，带进度条）
 *
 * 走「下载票据 + 原生下载」：先经认证换取短期票据，再用票据直链触发下载，
 * 浏览器下载管理器从第一秒起即可见下载进度。
 *
 * 外部来源文档（公众号文章等）没有本地文件，票据端点返回 200 + external +
 * original_url：直接新开页面打开原文链接。
 */
export async function downloadDocument(docId: number, fallbackName: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/documents/${docId}/download_ticket`, {
    method: 'POST',
    headers: { ...getAuthHeader() }
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail || body?.error || `下载失败: ${resp.status}`)
  }
  const { ticket, external, original_url: originalUrl } = await resp.json()
  if (external) {
    if (!originalUrl) throw new Error('外部来源文档缺少原文链接')
    window.open(originalUrl as string, '_blank', 'noopener')
    return
  }
  if (!ticket) throw new Error('获取下载票据失败')
  triggerNativeDownload(
    `${API_BASE}/documents/${docId}/download?ticket=${encodeURIComponent(ticket)}`,
    fallbackName
  )
}

/**
 * 搜索知识库文档（混合检索：向量 + FTS5 + RRF）
 * @param source_type 顶级分类代号，限定搜索范围（含其下所有子级）
 * @param sub_category 直接选中分类代号，后端展开为含其所有子级；不传时全分类搜索
 */
export async function searchDocuments(
  query: string,
  top_k = 10,
  source_type?: string,
  sub_category?: string
): Promise<SearchResponse> {
  const response = await fetch(`${API_BASE}/search_documents`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({ query, top_k, source_type, sub_category })
  })
  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.error || '搜索失败')
  }
  return response.json()
}

// ========== 分类管理 ==========

export interface CategoryResponse {
  id: number
  source_type: string
  display_name: string | null
  parent_id: number | null
  document_count: number
  created_at: string | null
}

export interface CategoryListResponse {
  items: CategoryResponse[]
}

/**
 * 获取知识库分类列表
 */
export async function listCategories(): Promise<CategoryListResponse> {
  const response = await fetch(`${API_BASE}/categories`, {
    headers: { ...getAuthHeader() }
  })
  if (!response.ok) {
    throw new Error(`获取分类列表失败: ${response.status}`)
  }
  return response.json()
}

/**
 * 创建知识库分类（英文代号由后端自动生成）
 */
export async function createCategory(displayName: string, parentId?: number | null): Promise<any> {
  const response = await fetch(`${API_BASE}/categories`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({ display_name: displayName, parent_id: parentId ?? null })
  })
  const result = await response.json()
  if (!response.ok) {
    throw new Error(result.error || '创建分类失败')
  }
  return result
}

/**
 * 更新分类名称
 */
export async function updateCategory(categoryId: number, displayName: string): Promise<any> {
  const response = await fetch(`${API_BASE}/categories/${categoryId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({ display_name: displayName })
  })
  const result = await response.json()
  if (!response.ok) {
    throw new Error(result.error || '更新分类失败')
  }
  return result
}

/**
 * 删除分类（不删除文档）
 */
export async function deleteCategory(categoryId: number): Promise<ApiResponse> {
  const response = await fetch(`${API_BASE}/categories/${categoryId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() }
  })
  return response.json()
}
