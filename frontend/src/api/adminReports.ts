/**
 * 平台管理报表 API 调用模块
 */

import { getAuthHeader } from '@/api/auth'

const API_BASE = import.meta.env.VITE_API_BASE || ''

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