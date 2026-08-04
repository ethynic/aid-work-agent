/**
 * 视频生成工具 API 客户端（MVP 抽卡式）。
 * 后端路由 /api/video-gen/*，设计文档 mvp-design.md §9、§12.1。
 */
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/video-gen`
const FILE_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}`

export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
  debug?: string
}

export interface SceneItem {
  scene_id: string
  name: string
  description: string
}

export interface GenCard {
  card_id: string
  session_id: string
  variant_idx: number
  seed?: number
  variant_prompt?: string
  provider_task_id?: string | null
  provider_status: string   // PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN
  output_fid?: string | null
  output_duration?: number | null
  kept: boolean
  parent_card_id?: string | null
  error_msg?: string | null
  created_at?: string
}

export interface GenSession {
  session_id: string
  scene_id: string
  scene_name?: string
  product_image_fid: string
  model_image_fid?: string | null   // 模特图（可选，作 first_frame）
  copywriting: string
  expanded_prompt?: string
  card_count: number
  status: string            // generating/done/failed
  created_at?: string
  cards?: GenCard[]
}

export interface UploadResult {
  success: boolean
  file_id: string
  name?: string
  size?: number
  mime_type?: string
}

async function request<T>(path: string, options: RequestInit = {}): Promise<ApiResponse<T>> {
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeader(),
    ...(options.headers || {}),
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  return res.json()
}

export const videoGenAPI = {
  /** 场景列表 */
  listScenes: () => request<{ items: SceneItem[] }>('/scenes'),

  /** 创建抽卡会话 */
  createSession: (body: {
    scene_id: string
    product_image_fid: string
    copywriting: string
    model_image_fid?: string
    card_count?: number
    expanded_prompt?: string
  }) => request<GenSession>('/sessions', {
    method: 'POST',
    body: JSON.stringify(body),
  }),

  /** 会话历史 */
  listSessions: (limit = 20) => request<{ items: GenSession[] }>(`/sessions?limit=${limit}`),

  /** 会话详情（含 cards 状态） */
  getSession: (sessionId: string) => request<GenSession>(`/sessions/${sessionId}`),

  /** 标记留用/取消 */
  setKept: (cardId: string, kept: boolean) => request<{ card_id: string; kept: boolean }>(`/cards/${cardId}/kept`, {
    method: 'PATCH',
    body: JSON.stringify({ kept }),
  }),

  /** 重新生成（精修） */
  regenerate: (cardId: string, body: { prompt_override?: string; seed_override?: number }) =>
    request<GenCard>(`/cards/${cardId}/regenerate`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** 获取成片下载 URL */
  getDownloadUrl: (cardId: string) => request<{ download_url: string; file_id: string }>(`/cards/${cardId}/download-url`),

  /** 上传产品图（multipart，复用 POST /api/upload） */
  uploadImage: async (file: File): Promise<UploadResult> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${FILE_BASE}/upload`, {
      method: 'POST',
      headers: { ...getAuthHeader() },
      body: form,
    })
    return res.json()
  },
}

/** 成片预览/下载 URL（复用 /api/files/{file_id}） */
export function fileUrl(fileId: string, download = false): string {
  return `${FILE_BASE}/files/${fileId}${download ? '/download' : ''}`
}
