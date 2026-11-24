/**
 * 平台管理报表 API 调用模块
 */

import { getAuthHeader } from '@/api/auth'

const API_BASE = import.meta.env.VITE_API_BASE || ''

export interface WecomScheduledSendPrice {
  unit_price: string
  default_price: string
  overridden: boolean
}

export async function wecomScheduledSendPrice(unitPrice?: string | null): Promise<WecomScheduledSendPrice> {
  const response = await fetch(`${API_BASE}/api/saas/billing/wecom-scheduled-send-price`, {
    method: unitPrice === undefined ? 'GET' : 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: unitPrice === undefined ? undefined : JSON.stringify({ unit_price: unitPrice }),
  })
  const result = await response.json().catch(() => ({}))
  if (!response.ok || result.success === false) {
    throw new Error(typeof result.detail === 'string' ? result.detail : result.error || `单价请求失败（${response.status}）`)
  }
  return result.data
}

/**
 * 获取平台Token消耗报表
 * @param month 月份，格式 YYYY-MM
 * @returns 平台Token消耗报表数据
 */
export async function getPlatformTokenUsage(month: string): Promise<any> {
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeader()
  }
  const response = await fetch(`${API_BASE}/api/admin/token-usage?month=${encodeURIComponent(month)}`, {
    headers
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }

  return response.json()
}
