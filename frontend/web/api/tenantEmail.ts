import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/tenant/email-settings`

export interface TenantEmailConfig {
  email_address: string
  smtp_server: string
  smtp_port: number
  smtp_user: string
  smtp_password: string
  smtp_encryption: string
  imap_server: string
  imap_port: number
  imap_encryption: string
}

export interface TenantEmailSettingsResponse {
  success: boolean
  data: TenantEmailConfig | null
  bound: boolean
  error?: string
}

export async function getTenantEmailSettings(): Promise<TenantEmailSettingsResponse> {
  const res = await fetch(API_BASE, { headers: getSaasAuthHeader() })
  if (!res.ok) throw new Error('获取租户邮箱配置失败')
  return res.json()
}

export async function saveTenantEmailSettings(config: TenantEmailConfig): Promise<{ success: boolean; message?: string; error?: string }> {
  const res = await fetch(API_BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(config)
  })
  if (!res.ok) throw new Error('保存租户邮箱配置失败')
  return res.json()
}

export async function deleteTenantEmailSettings(): Promise<{ success: boolean; message?: string; error?: string }> {
  const res = await fetch(API_BASE, {
    method: 'DELETE',
    headers: getSaasAuthHeader()
  })
  if (!res.ok) throw new Error('删除租户邮箱配置失败')
  return res.json()
}
