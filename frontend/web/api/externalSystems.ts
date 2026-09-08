/**
 * 外部系统入口 API（SSO 打开第三方系统）
 * 设计方案：docs/system/external-system-entry-design.md
 */

import { getSaasAuthHeader } from './externalCustomers'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/external-systems`

export interface ExternalSystem {
  system_id: string
  name: string
  mode: 'direct_url' | 'ticket_redirect' | 'token_param' | 'form_submit'
  sso_ready: boolean
  entry_url: string | null
}

interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
  fallback_url?: string
}

export async function listExternalSystems(): Promise<ExternalSystem[]> {
  const response = await fetch(API_BASE, {
    headers: { ...getSaasAuthHeader() },
  })
  const res: ApiResponse<{ items: ExternalSystem[] }> = await response.json()
  if (!res.success) {
    throw new Error(res.error || '获取外部系统列表失败')
  }
  return res.data?.items || []
}

async function getSsoUrl(systemId: string): Promise<ApiResponse<{ url: string }>> {
  const response = await fetch(`${API_BASE}/${systemId}/sso-url`, {
    method: 'POST',
    headers: { ...getSaasAuthHeader(), 'Content-Type': 'application/json' },
  })
  return response.json()
}

/**
 * 打开外部系统（新开窗口）。
 * SSO 模式先同步开占位窗口防浏览器拦截，换票成功后重定向；
 * 换票失败重定向到 fallback_url 供用户手动登录。
 */
export async function openExternalSystem(item: ExternalSystem): Promise<{ ok: boolean; message?: string }> {
  if (item.mode === 'direct_url' && item.entry_url) {
    window.open(item.entry_url, '_blank')
    return { ok: true }
  }

  const win = window.open('', '_blank')
  if (!win) {
    // 弹窗被浏览器拦截：换票成功也没有窗口可展示，提示用户允许弹窗后重试
    return { ok: false, message: '浏览器拦截了新窗口，请允许本站弹窗后重试' }
  }
  try {
    const res = await getSsoUrl(item.system_id)
    const target = (res.success && res.data?.url) || res.fallback_url
    if (target) {
      win.location.href = target
      return res.success
        ? { ok: true }
        : { ok: false, message: res.error || 'SSO 登录失败，请在新打开的页面手动登录' }
    }
    win.close()
    return { ok: false, message: res.error || '打开外部系统失败' }
  } catch (e) {
    win.close()
    return { ok: false, message: (e as Error).message || '网络异常，打开失败' }
  }
}
