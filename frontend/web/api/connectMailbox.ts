/** 连接邮箱 API（个人邮箱配置；原「设置 → 邮箱设置」迁移到连接中心） */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}`

export type MailboxEncryption = 'ssl' | 'tls' | 'none'

export type MailboxSaveErrorCode = 'auth' | 'connect' | 'timeout' | 'network' | 'invalid_recipient' | 'other'

/** 保存接口要求的 9 项配置 */
export interface EmailSettingsConfig {
  email_address: string
  smtp_server: string
  smtp_port: number
  smtp_user: string
  smtp_password: string
  smtp_encryption: MailboxEncryption
  imap_server: string
  imap_port: number
  imap_encryption: MailboxEncryption
}

export interface EmailSettingsResponse {
  success: boolean
  data: (EmailSettingsConfig & { updated_at?: string | null }) | null
  bound: boolean
  error?: string
}

export interface EmailSettingsSaveResponse {
  success: boolean
  message?: string
  error?: string
  error_code?: MailboxSaveErrorCode | null
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
export async function saveEmailSettings(config: EmailSettingsConfig): Promise<EmailSettingsSaveResponse> {
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
