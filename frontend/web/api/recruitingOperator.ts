/**
 * 招聘操作智能体 API 客户端（简历库 + 职位库）
 *
 * 对应后端 src/api/recruiting_operator.py（prefix /api/recruiting-operator）：
 * - 简历列表（分页 + keyword/job_name/status/日期区间筛选）
 * - 职位下拉（distinct job_name）
 * - 创建（file_id 引用 + base64 直传两路）/ 详情 / 更新 / 删除
 * - 职位库（职位 CRUD + 每职位常用沟通话术 CRUD，固定四分类）
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

/** 匹配状态（简历-职位匹配）：matched 达标 / unmatched 接近或未关联 / rejected 不匹配 */
export type MatchStatus = 'matched' | 'unmatched' | 'rejected'

/**
 * 匹配状态中文标签（null/未知 → 未评分）。
 * 注意语义：unmatched 涵盖 50-69 分「接近」与未关联职位两种情况（设计 §3 阈值规则）。
 */
export const MATCH_STATUS_LABELS: Record<string, string> = {
  matched: '匹配',
  unmatched: '接近',
  rejected: '不匹配',
}

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
  job_id?: string | null
  job_name?: string
  candidate_info?: Record<string, any>
  images?: ResumeImage[]
  source: ResumeSource
  status: ResumeStatus
  match_score?: number | null
  match_summary?: string | null
  match_status?: MatchStatus | null
  remark?: string
  fetched_at?: string
  created_at?: string
  updated_at?: string
}

/** 详情（含 OCR 全文 + 评分关键信息） */
export interface ResumeDetail extends ResumeListItem {
  ocr_text?: string
  key_info?: Record<string, any> | null
}

/** 重新评分结果（评分失败不报错，data 带 note 说明原因） */
export interface ReEvaluateResult {
  resume_id?: number
  match_score?: number | null
  match_status?: MatchStatus | null
  match_summary?: string | null
  key_info?: Record<string, any> | null
  note?: string
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

/** 重新评分（评分失败不报错：success=true + data.note 说明原因，库中原值保留） */
export async function reEvaluateResume(resumeId: number): Promise<ApiDetailResponse<ReEvaluateResult>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/resumes/${resumeId}/re-evaluate`, {
    method: 'POST',
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

// ============== 职位库类型 ==============

/** 话术分类（固定四值，顺序即展示顺序） */
export const JOB_SCRIPT_CATEGORIES = ['初次开场', '了解摸底', '追问细节', '邀约推进'] as const

export type JobScriptCategory = (typeof JOB_SCRIPT_CATEGORIES)[number]

/** 结构化职位要求（= BOSS 筛选项，值须为档位文本；字段可缺省，见设计 §2.1） */
export interface JobRequirements {
  experience?: string | null
  educations?: string[]
  salary?: string | null
  keywords?: string[]
  notes?: string | null
}

/** 职位要求档位候选（静态下拉候选；真值档位由 boss_filter_options 运行时校准兜底） */
export interface JobRequirementOptions {
  experience: string[]
  educations: string[]
  salary: string[]
}

/** 职位公共字段（列表与详情响应都保证的字段） */
interface JobBase {
  id: string
  tenant_id?: string
  job_name: string
  notes?: string
  status: 'active' | 'paused'
  match_threshold: number
  job_requirements?: JobRequirements | null
  script_count: number
  created_at?: string
  updated_at?: string
}

/**
 * 职位列表项（GET /jobs）：除公共字段外，另含已用分类与简历/匹配统计
 * （注意：这些统计仅列表接口返回，详情 getJob 响应不含，勿在详情视图误用）
 */
export interface JobListItem extends JobBase {
  categories: string[]
  resume_count: number
  matched_count: number
}

/** 职位话术 */
export interface JobScript {
  id: string
  tenant_id?: string
  job_id: string
  category: JobScriptCategory | string
  title: string
  content: string
  sort_order: number
  created_at?: string
  updated_at?: string
}

/** 职位详情（GET /jobs/{id}）：scripts 平铺 + script_groups 按分类分组（不含列表统计字段） */
export interface JobDetail extends JobBase {
  scripts: JobScript[]
  script_groups: { category: string; scripts: JobScript[] }[]
}

export interface CreateJobRequest {
  job_name: string
  notes?: string
  status?: 'active' | 'paused'
  match_threshold?: number
  job_requirements?: JobRequirements
}

export interface UpdateJobRequest {
  job_name?: string
  notes?: string
  status?: 'active' | 'paused'
  match_threshold?: number
  /** 传 {} 清空全部要求（存 NULL）；不传该键表示不修改 */
  job_requirements?: JobRequirements
}

export interface CreateJobScriptRequest {
  category: JobScriptCategory | string
  title: string
  content: string
  sort_order?: number
}

export interface UpdateJobScriptRequest {
  category?: JobScriptCategory | string
  title?: string
  content?: string
  sort_order?: number
}

// ============== 职位库 API 封装 ==============

export async function listJobs(): Promise<{ success: boolean; data?: { items: JobListItem[] }; error?: string }> {
  const res = await fetch(`${API_BASE}/recruiting-operator/jobs`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

/** 职位要求档位候选（静态：experience/educations/salary 下拉候选） */
export async function getRequirementOptions(): Promise<{ success: boolean; data?: JobRequirementOptions; error?: string }> {
  const res = await fetch(`${API_BASE}/recruiting-operator/jobs/requirement-options`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function createJob(req: CreateJobRequest): Promise<ApiDetailResponse<JobDetail>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(req),
  })
  return res.json()
}

export async function getJob(jobId: string): Promise<ApiDetailResponse<JobDetail>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/jobs/${jobId}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function updateJob(jobId: string, req: UpdateJobRequest): Promise<ApiDetailResponse<JobDetail>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/jobs/${jobId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(req),
  })
  return res.json()
}

export async function deleteJob(jobId: string): Promise<ApiMutationResponse> {
  const res = await fetch(`${API_BASE}/recruiting-operator/jobs/${jobId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function createJobScript(jobId: string, req: CreateJobScriptRequest): Promise<ApiDetailResponse<JobScript>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/jobs/${jobId}/scripts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(req),
  })
  return res.json()
}

export async function updateJobScript(scriptId: string, req: UpdateJobScriptRequest): Promise<ApiDetailResponse<JobScript>> {
  const res = await fetch(`${API_BASE}/recruiting-operator/scripts/${scriptId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(req),
  })
  return res.json()
}

export async function deleteJobScript(scriptId: string): Promise<ApiMutationResponse> {
  const res = await fetch(`${API_BASE}/recruiting-operator/scripts/${scriptId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  return res.json()
}
