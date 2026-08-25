/**
 * 租户数据迁移 API Client
 */

import { getTenantScopedKey } from './tenantStorage'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas`

function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  // 平台管理后台 portal_token 优先；租户前台按 tenant_id 取 saas_token
  const token = localStorage.getItem('portal_token') || localStorage.getItem(getTenantScopedKey('saas_token'))
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

export interface MigrationRequest {
  source_db: string
  source_tenant: string
  tables: string[]
  mode: string
  source_storage: string
}

export interface MigrationTableSummary {
  table?: string
  source_count: number
  target_count_before?: number
  target_before?: number
  inserted: number
  skipped?: number
  deleted?: number
  files_copied?: number
}

export interface MigrationResult {
  success: boolean
  summary: Record<string, any>
  errors: string[]
  error?: string
}

export async function previewMigration(tenantId: string, data: MigrationRequest): Promise<MigrationResult> {
  const res = await fetch(`${API_BASE}/tenants/${encodeURIComponent(tenantId)}/migration/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  const result = await res.json()
  if (!res.ok) {
    throw new Error(result.detail?.error || result.error || '预览失败')
  }
  return result
}

export async function executeMigration(tenantId: string, data: MigrationRequest): Promise<MigrationResult> {
  const res = await fetch(`${API_BASE}/tenants/${encodeURIComponent(tenantId)}/migration/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(data),
  })
  const result = await res.json()
  if (!res.ok) {
    throw new Error(result.detail?.error || result.error || '执行迁移失败')
  }
  return result
}
