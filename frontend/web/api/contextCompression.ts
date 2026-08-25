/**
 * 上下文压缩管理 API（Phase 7 §7.3）
 *
 * 路由前缀：/api/observability/context-summaries
 * 认证头：复用 getSaasAuthHeader（含 X-Tenant-Id），与既有 SaaS API 一致
 */

import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/observability/context-summaries`

export interface ContextSummaryListItem {
  summary_id: string
  session_id: string
  source_type: string
  tenant_id?: string | null
  status: string
  summary_version: number
  compressed_message_count: number
  original_token_count: number
  compressed_token_count: number
  compression_ratio: number
  fallback_used: boolean
  llm_provider?: string | null
  llm_model?: string | null
  created_at: string
  superseded_at?: string | null
}

export interface ContextSummaryDetail extends ContextSummaryListItem {
  summary_text: string
  compressed_message_ids?: number[]
}

export interface CompressedMessage {
  id: number
  role: string
  content: string
  tool_calls?: any
  metadata?: any
  compacted: boolean
  created_at: string
}

export interface CompressionStats {
  counts: Record<string, number>
  duration: { count: number; avg: number; min: number; max: number; p50: number; p95: number }
  ratio: { count: number; avg: number; min: number; max: number; p50: number; p95: number }
  fallback_duration: { count: number; avg: number; min: number; max: number; p50: number; p95: number }
}

export interface CompressionResultVO {
  summary_id: string
  compressed_message_count: number
  original_token_count: number
  compressed_token_count: number
  compression_ratio: number
  fallback_used: boolean
  trigger_reason: string
  llm_provider: string | null
  llm_model: string | null
}

export async function listSummaries(params?: {
  session_id?: string
  source_type?: string
  limit?: number
}): Promise<{ success: boolean; items: ContextSummaryListItem[]; total: number; error?: string }> {
  const qs = new URLSearchParams()
  if (params?.session_id) qs.set('session_id', params.session_id)
  if (params?.source_type) qs.set('source_type', params.source_type)
  if (params?.limit) qs.set('limit', String(params.limit))
  const url = `${API_BASE}${qs.toString() ? '?' + qs.toString() : ''}`
  const res = await fetch(url, { headers: getSaasAuthHeader() })
  return res.json()
}

export async function getCompressionStats(): Promise<{
  success: boolean
  stats?: CompressionStats
  error?: string
}> {
  const res = await fetch(`${API_BASE}/stats`, { headers: getSaasAuthHeader() })
  return res.json()
}

export async function getSummaryDetail(
  summaryId: string,
): Promise<{
  success: boolean
  summary?: ContextSummaryDetail
  messages?: CompressedMessage[]
  error?: string
}> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(summaryId)}`, {
    headers: getSaasAuthHeader(),
  })
  return res.json()
}

export async function rollbackSummary(
  summaryId: string,
): Promise<{ success: boolean; restored_message_count?: number; error?: string }> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(summaryId)}/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
  })
  return res.json()
}

export async function manualCompact(
  sessionId: string,
  sourceType: string = 'chat',
): Promise<{ success: boolean; result?: CompressionResultVO | null; message?: string; error?: string }> {
  const qs = new URLSearchParams({ source_type: sourceType })
  const res = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/compact?${qs.toString()}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    },
  )
  return res.json()
}
