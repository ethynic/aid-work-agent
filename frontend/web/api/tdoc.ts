/**
 * 腾讯文档知识库导入 API
 *
 * 后端路由：src/api/knowledge_tdoc.py（/api/knowledge/tdoc）
 * token 等敏感凭据仅保存不下发，状态接口返回脱敏视图
 */
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/knowledge/tdoc`

export interface TdocAuthStatus {
  success: boolean
  bound: boolean
  status: 'none' | 'active' | 'expired'
  client_id_masked?: string
  open_id_masked?: string
  updated_at?: string | null
  error?: string
}

export interface TdocAuthSaveParams {
  client_id: string
  access_token: string
  open_id: string
  refresh_token?: string
}

export interface TdocFileItem {
  file_id: string
  content_id: string
  title: string
  type: string
  last_modify_time: number | null
  is_owner: boolean
  imported: boolean
  unchanged: boolean
}

export interface TdocFileListResponse {
  success: boolean
  items: TdocFileItem[]
  next: number | null
  error?: string
  detail?: string
}

export interface TdocImportFileInput {
  file_id: string
  title: string
  doc_type: string
  last_modify_time?: number | null
}

export interface TdocImportCreateResult {
  success: boolean
  batch_id?: string
  queued?: number
  skipped?: number
  error?: string
  detail?: string
}

export interface TdocImportBatch {
  batch_id: string
  tenant_id: string
  user_id: string
  status: 'queued' | 'running' | 'succeeded' | 'partial' | 'failed'
  total_count: number
  success_count: number
  fail_count: number
  skip_count: number
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface TdocImportItem {
  id: number
  batch_id: string
  tdoc_file_id: string
  title: string | null
  doc_type: string | null
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'
  document_id: number | null
  error_message: string | null
  retry_count: number
  created_at: string
  updated_at: string
}

export interface TdocImportListResponse {
  success: boolean
  items: TdocImportBatch[]
  total: number
  page: number
  page_size: number
}

export interface TdocImportDetailResponse {
  success: boolean
  batch: TdocImportBatch
  items: TdocImportItem[]
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...getAuthHeader(),
      ...(init.headers || {})
    }
  })
  return response.json()
}

// ========== 凭据绑定 ==========

export async function getTdocAuthStatus(): Promise<TdocAuthStatus> {
  return request<TdocAuthStatus>('/auth')
}

export async function saveTdocAuth(params: TdocAuthSaveParams): Promise<TdocAuthStatus> {
  return request<TdocAuthStatus>('/auth', { method: 'POST', body: JSON.stringify(params) })
}

export async function deleteTdocAuth(): Promise<{ success: boolean; deleted: boolean }> {
  return request('/auth', { method: 'DELETE' })
}

// ========== 目录浏览 ==========

export async function listTdocFiles(folderId = '', start = 0): Promise<TdocFileListResponse> {
  const params = new URLSearchParams({ folder_id: folderId, start: String(start), limit: '50' })
  return request<TdocFileListResponse>(`/files?${params.toString()}`)
}

// ========== 导入批次 ==========

export async function createTdocImport(files: TdocImportFileInput[]): Promise<TdocImportCreateResult> {
  return request<TdocImportCreateResult>('/imports', {
    method: 'POST',
    body: JSON.stringify({ files })
  })
}

export async function listTdocImports(page = 1, pageSize = 20): Promise<TdocImportListResponse> {
  return request<TdocImportListResponse>(`/imports?page=${page}&page_size=${pageSize}`)
}

export async function getTdocImport(batchId: string): Promise<TdocImportDetailResponse> {
  return request<TdocImportDetailResponse>(`/imports/${encodeURIComponent(batchId)}`)
}

export async function retryTdocImport(batchId: string): Promise<{ success: boolean; reset_count?: number; error?: string }> {
  return request(`/imports/${encodeURIComponent(batchId)}/retry`, { method: 'POST' })
}
