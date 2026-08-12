/**
 * 回复风格管理 API
 */
import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/reply-styles`

export interface ReplyStyle {
  style_id: string
  tenant_id: string
  name: string
  description: string | null
  content: string
  version: number
  is_active: boolean
  is_system: boolean
  created_at: string | null
  updated_at: string | null
}

export interface ReplyStyleVersion {
  version: number
  is_active: boolean
  content: string
  name: string
  created_at: string | null
}

export async function listStyles(): Promise<{ success: boolean; styles: Omit<ReplyStyle, 'content' | 'tenant_id'>[] }> {
  const res = await fetch(API_BASE, {
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) throw new Error('获取风格列表失败')
  return res.json()
}

export async function getStyle(styleId: string): Promise<{ success: boolean; style: ReplyStyle }> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(styleId)}`, {
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) throw new Error('获取风格详情失败')
  return res.json()
}

export async function createStyle(data: {
  style_id: string
  name: string
  description?: string
  content: string
}): Promise<{ success: boolean; style: ReplyStyle }> {
  const res = await fetch(API_BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '创建风格失败')
  }
  return res.json()
}

export async function updateStyle(
  styleId: string,
  data: { name?: string; description?: string; content?: string },
): Promise<{ success: boolean; style: ReplyStyle }> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(styleId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '更新风格失败')
  }
  return res.json()
}

export async function deleteStyle(styleId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(styleId)}`, {
    method: 'DELETE',
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '删除风格失败')
  }
  return res.json()
}

export async function listVersions(styleId: string): Promise<{ success: boolean; versions: ReplyStyleVersion[] }> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(styleId)}/versions`, {
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) throw new Error('获取版本历史失败')
  return res.json()
}

export async function activateVersion(styleId: string, version: number): Promise<{ success: boolean }> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(styleId)}/versions/${version}/activate`, {
    method: 'POST',
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '激活版本失败')
  }
  return res.json()
}


// ==================== 系统内置风格管理（平台管理员 /portal） ====================

const SYSTEM_BASE = `${API_BASE}/system`

export async function listSystemStyles(): Promise<{ success: boolean; styles: Omit<ReplyStyle, 'content' | 'tenant_id'>[] }> {
  const res = await fetch(`${SYSTEM_BASE}/list`, {
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) throw new Error('获取系统风格列表失败')
  return res.json()
}

export async function getSystemStyle(styleId: string): Promise<{ success: boolean; style: ReplyStyle }> {
  const res = await fetch(`${SYSTEM_BASE}/${encodeURIComponent(styleId)}`, {
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) throw new Error('获取系统风格详情失败')
  return res.json()
}

export async function createSystemStyle(data: {
  style_id: string
  name: string
  description?: string
  content: string
}): Promise<{ success: boolean; style: ReplyStyle }> {
  const res = await fetch(`${SYSTEM_BASE}/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '创建系统风格失败')
  }
  return res.json()
}

export async function updateSystemStyle(
  styleId: string,
  data: { name?: string; description?: string; content?: string },
): Promise<{ success: boolean; style: ReplyStyle }> {
  const res = await fetch(`${SYSTEM_BASE}/${encodeURIComponent(styleId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '更新系统风格失败')
  }
  return res.json()
}

export async function deleteSystemStyle(styleId: string): Promise<{ success: boolean }> {
  const res = await fetch(`${SYSTEM_BASE}/${encodeURIComponent(styleId)}`, {
    method: 'DELETE',
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '删除系统风格失败')
  }
  return res.json()
}

export async function listSystemVersions(styleId: string): Promise<{ success: boolean; versions: ReplyStyleVersion[] }> {
  const res = await fetch(`${SYSTEM_BASE}/${encodeURIComponent(styleId)}/versions`, {
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) throw new Error('获取系统风格版本历史失败')
  return res.json()
}

export async function activateSystemVersion(styleId: string, version: number): Promise<{ success: boolean }> {
  const res = await fetch(`${SYSTEM_BASE}/${encodeURIComponent(styleId)}/versions/${version}/activate`, {
    method: 'POST',
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '激活系统风格版本失败')
  }
  return res.json()
}
