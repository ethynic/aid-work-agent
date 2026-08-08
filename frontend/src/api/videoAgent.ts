/**
 * 视频创作智能体知识中心 API 客户端（Phase 6.2.2）
 *
 * 设计依据：docs/plans/plan-video-agent-phase1.md §4
 *
 * 三套 CRUD API 封装：
 * - 素材库 (asset_library)
 * - 视频库 (work_outcomes where outcome_type='file' AND subagent_id='video-agent')
 * - 提示词库 (prompt_library)
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
  data?: { id?: number; promoted_from_kept_id?: number }
  error?: string
}

// ============== 1. 素材库 ==============

export interface AssetItem {
  id: number
  tenant_id?: string
  user_id?: string
  file_id: string
  display_name: string
  mime_type: string
  size_bytes: number
  source: string
  scene?: string
  width?: number
  height?: number
  created_at?: string
}

export interface ManualUploadAssetRequest {
  file_id: string
  display_name: string
  mime_type: string
  size_bytes: number
  scene?: string
  width?: number
  height?: number
}

export async function listAssets(params: {
  page?: number
  page_size?: number
  scene?: string
  source?: string
} = {}): Promise<ApiListResponse<AssetItem>> {
  const search = new URLSearchParams()
  if (params.page) search.set('page', String(params.page))
  if (params.page_size) search.set('page_size', String(params.page_size))
  if (params.scene) search.set('scene', params.scene)
  if (params.source) search.set('source', params.source)
  const res = await fetch(`${API_BASE}/video-agent/assets?${search.toString()}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function getAsset(assetId: number): Promise<ApiDetailResponse<AssetItem>> {
  const res = await fetch(`${API_BASE}/video-agent/assets/${assetId}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function deleteAsset(assetId: number): Promise<ApiMutationResponse> {
  const res = await fetch(`${API_BASE}/video-agent/assets/${assetId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function manualUploadAsset(req: ManualUploadAssetRequest): Promise<ApiMutationResponse> {
  const res = await fetch(`${API_BASE}/video-agent/assets/manual`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(req),
  })
  return res.json()
}

// ============== 2. 视频库 ==============

export interface VideoItem {
  id: number
  tenant_id?: string
  user_id?: string
  subagent_id: string
  outcome_type: string
  file_id: string
  file_name?: string
  summary?: string
  metadata?: Record<string, any>
  created_at?: string
  /** 详情接口附带：源提示词 */
  source_prompt?: PromptItem | null
}

export async function listVideos(params: { page?: number; page_size?: number } = {}): Promise<ApiListResponse<VideoItem>> {
  const search = new URLSearchParams()
  if (params.page) search.set('page', String(params.page))
  if (params.page_size) search.set('page_size', String(params.page_size))
  const res = await fetch(`${API_BASE}/video-agent/videos?${search.toString()}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function getVideo(videoId: number): Promise<ApiDetailResponse<VideoItem>> {
  const res = await fetch(`${API_BASE}/video-agent/videos/${videoId}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function deleteVideo(videoId: number): Promise<ApiMutationResponse> {
  const res = await fetch(`${API_BASE}/video-agent/videos/${videoId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

// ============== 3. 提示词库 ==============

export type PromptCategory = 'kept' | 'blacklist' | 'template'

export interface PromptItem {
  id: number
  tenant_id?: string
  user_id?: string
  category: PromptCategory
  business_prompt: string
  craft_prompt: string
  model_params?: Record<string, any>
  metadata?: Record<string, any>
  industry_tag?: string
  scene_tag?: string
  source_video_file_id?: string
  source_chat_session_id?: string
  promoted_from_kept_id?: number
  promoted_by_user_id?: string
  promoted_at?: string
  created_at?: string
}

export async function listPrompts(params: {
  page?: number
  page_size?: number
  category?: PromptCategory
  scene_tag?: string
} = {}): Promise<ApiListResponse<PromptItem>> {
  const search = new URLSearchParams()
  if (params.page) search.set('page', String(params.page))
  if (params.page_size) search.set('page_size', String(params.page_size))
  if (params.category) search.set('category', params.category)
  if (params.scene_tag) search.set('scene_tag', params.scene_tag)
  const res = await fetch(`${API_BASE}/video-agent/prompts?${search.toString()}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function getPrompt(promptId: number): Promise<ApiDetailResponse<PromptItem>> {
  const res = await fetch(`${API_BASE}/video-agent/prompts/${promptId}`, {
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function promotePrompt(promptId: number): Promise<ApiMutationResponse> {
  const res = await fetch(`${API_BASE}/video-agent/prompts/${promptId}/promote`, {
    method: 'POST',
    headers: { ...getAuthHeader() },
  })
  return res.json()
}

export async function deletePrompt(promptId: number): Promise<ApiMutationResponse> {
  const res = await fetch(`${API_BASE}/video-agent/prompts/${promptId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  return res.json()
}
