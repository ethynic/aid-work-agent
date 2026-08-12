/** 用户设置 API */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}`

export interface EmailSettings {
  email_address: string
  smtp_server: string
  smtp_port: number
  smtp_user: string
  smtp_password: string
  smtp_encryption: 'ssl' | 'tls' | 'none'
  imap_server: string
  imap_port: number
  imap_encryption: 'ssl' | 'tls' | 'none'
}

export interface EmailSettingsResponse {
  success: boolean
  data: EmailSettings | null
  bound: boolean
  error?: string
}

export interface ProfileUpdateData {
  username?: string
  avatar_url?: string
}

/** 获取当前用户的邮箱配置 */
export async function getEmailSettings(): Promise<EmailSettingsResponse> {
  const res = await fetch(`${API_BASE}/email-settings`, {
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) throw new Error('获取邮箱配置失败')
  return res.json()
}

/** 保存邮箱配置（后端会自动测试发送邮件） */
export async function saveEmailSettings(config: EmailSettings): Promise<{ success: boolean; message?: string; error?: string }> {
  const res = await fetch(`${API_BASE}/email-settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error('保存邮箱配置失败')
  return res.json()
}

/** 删除邮箱配置 */
export async function deleteEmailSettings(): Promise<{ success: boolean; message?: string; error?: string }> {
  const res = await fetch(`${API_BASE}/email-settings`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  if (!res.ok) throw new Error('删除邮箱配置失败')
  return res.json()
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
