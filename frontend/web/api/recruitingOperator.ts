/**
 * 招聘操作智能体简历库 API 客户端
 *
 * 对应后端 src/api/recruiting_operator.py（prefix /api/recruiting-operator）：
 * - 简历列表（分页 + keyword/job_name/status/日期区间筛选）
 * - 职位下拉（distinct job_name）
 * - 创建（file_id 引用 + base64 直传两路）/ 详情 / 更新 / 删除
 */
import { getAuthHeader } from './auth'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

// ============== 通用类型 ==============

export interface ApiListResponse<T> {
  success: boolean
  data?: {
    items: T[]
    total: number
    page: number
    page_size: number
  }
  error?: string
}

export interface ApiDetailResponse<T> {
  success: boolean
  data?: T
  error?: string
}

export interface ApiMutationResponse {
  success: boolean
  data?: { id?: number }
  error?: string
}

// ============== 简历库类型 ==============

/** 状态：new新简历/viewed已查看/shortlisted有意向/interviewed已约面/rejected不合适 */
export type ResumeStatus = 'new' | 'viewed' | 'shortlisted' | 'interviewed' | 'rejected'

/** 来源：boss=CLI 入库 / manual=页面补录 */
export type ResumeSource = 'boss' | 'manual'

/** 简历图片（file_id 引用，展示走 /api/files/{file_id}） */
export interface ResumeImage {
  file_id: string
  name?: string
}

/** 列表项（轻量，不含 ocr_text） */
export interface ResumeListItem {
  id: number
  tenant_id?: string
  user_id?: string
  candidate_name?: string
  job_name?: string
  candidate_info?: Record<string, any>
  images?: ResumeImage[]
  source: ResumeSource
  status: ResumeStatus
  remark?: string
  fetched_at?: string
  created_at?: string
  updated_at?: string
}

/** 详情（含 OCR 全文） */
export interface ResumeDetail extends ResumeListItem {
  ocr_text?: string
}

export interface CreateResumeRequest {
  candidate_name: string
  job_name?: string
  candidate_info?: Record<string, any>
  ocr_text?: string
  images?: ResumeImage[]
  images_base64?: { data: string; name?: string; mime_type: string }[]
  source?: ResumeSource
  fetched_at?: string
  remark?: string
}

export interface UpdateResumeRequest {
  candidate_name?: string
  job_name?: string
  candidate_info?: Record<string, any>
  status?: ResumeStatus
  remark?: string
}

// ============== API 封装 ==============

export async function listResumes(params: {
  page?: number
  page_size?: number
  keyword?: string
  job_name?: string
  status?: ResumeStatus
  fetched_at_from?: string
  fetched_at_to?: string
} = {}): Promise<ApiListResponse<ResumeListItem>> {
  const search = new URLSearchParams()
  if (params.page) search.set('page', String(params.page))
  if (params.page_size) search.set('page_size', String(params.page_size))
  if (params.keyword) search.set('keyword', params.keyword)
  if (params.job_name) search.set('job_name', params.job_name)
  if (params.status) search.set('status', params.status)
  if (params.fetched_at_from) search.set('fetched_at_from', params.fetched_at_from)
  if (params.fetched_at_to) search.set('fetched_at_to', params.fetched_at_to)
  const res = await fetch(`${API_BASE}/recruiting-operator/resumes?${search.toString()}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function listResumeJobs(): Promise<{ success: boolean; data?: { jobs: string[] }; error?: string }> {
  const res = await fetch(`${API_BASE}/recruiting-operator/resumes/jobs`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function createResume(req: CreateResumeRequest): Promise<ApiDetailResponse<ResumeDetail>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/resumes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(req),
  })
  return res.json()
}

export async function getResume(resumeId: number): Promise<ApiDetailResponse<ResumeDetail>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/resumes/${resumeId}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function updateResume(resumeId: number, req: UpdateResumeRequest): Promise<ApiDetailResponse<ResumeDetail>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/resumes/${resumeId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(req),
  })
  return res.json()
}

export async function deleteResume(resumeId: number): Promise<ApiMutationResponse> {
  const res = await fetch(`${API_BASE}/recruiting-operator/resumes/${resumeId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  return res.json()
}
