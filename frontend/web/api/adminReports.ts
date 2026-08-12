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

/**
 * 获取管理后台仪表盘统计数据
 * 返回三个核心指标：正常租户数量、本月Token用量、今日对话数量
 */
export interface DashboardStats {
  success: boolean
  tenant_count: number
  monthly_token_usage: number
  today_conversation_count: number
  month: string
  message?: string
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeader()
  }
  const response = await fetch(`${API_BASE}/api/admin/dashboard_stats`, {
    headers
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }

  return response.json()
}