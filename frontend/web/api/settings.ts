/** 用户设置 API */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}`

export interface ProfileUpdateData {
  username?: string
  avatar_url?: string
}

/** 更新用户资料（显示名称、头像） */
export async function updateProfile(data: ProfileUpdateData): Promise<{ success: boolean; user?: { user_id: string; username: string; phone?: string; avatar_url?: string }; error?: string }> {
  const res = await fetch(`${API_BASE}/auth/profile`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('更新资料失败')
  return res.json()
}
