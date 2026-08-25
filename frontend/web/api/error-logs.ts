/**
 * 平台错误日志管理 API 调用模块
 */

import { getAuthHeader } from '@/api/auth'

const API_BASE = import.meta.env.VITE_API_BASE || ''

export interface ErrorLogItem {
  id: number
  timestamp: string
  module: string | null
  error_type: string | null
  message: string
  traceback: string | null
  status: 'unprocessed' | 'processed' | 'ignored'
  processed_by: string | null
  processed_at: string | null
}

export interface ErrorLogListResponse {
  success: boolean
  data: ErrorLogItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
  message?: string
}

export interface CleanupResponse {
  success: boolean
  deleted_db_records: number
  deleted_log_files: number
  message?: string
}

/**
 * 获取错误日志列表
 * @param status 过滤状态：unprocessed / processed / ignored
 * @param page 页码
 * @param pageSize 每页数量
 */
export async function getErrorLogs(
  status?: string,
  page: number = 1,
  pageSize: number = 20
): Promise<ErrorLogListResponse> {
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeader()
  }

  let url = `${API_BASE}/api/admin/error-logs?page=${page}&page_size=${pageSize}`
  if (status) {
    url += `&status=${encodeURIComponent(status)}`
  }

  const response = await fetch(url, { headers })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 更新错误日志状态
 * @param logId 日志ID
 * @param status 新状态：processed / ignored
 */
export async function updateErrorLogStatus(
  logId: number,
  status: 'processed' | 'ignored'
): Promise<{ success: boolean; message: string }> {
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeader()
  }

  const response = await fetch(`${API_BASE}/api/admin/error-logs/${logId}/status`, {
    method: 'PUT',
    headers,
    body: JSON.stringify({ status })
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }

  return response.json()
}

/**
 * 清理旧日志（30天前）
 */
export async function cleanupOldErrorLogs(): Promise<CleanupResponse> {
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeader()
  }

  const response = await fetch(`${API_BASE}/api/admin/error-logs/cleanup_old_error_logs`, {
    method: 'DELETE',
    headers
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }

  return response.json()
}
