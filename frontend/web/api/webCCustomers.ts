/**
 * 官网接待客户（web_c 渠道租户侧只读查看页）API
 *
 * 后端模块 src/saas/api/web_c_customers.py（设计 docs/system/web-c-channel-design.md §5.1）。
 * 附件下载复用外部接待页既有端点（session_id 归属校验同口径）。
 */

import { getSaasAuthHeader } from './externalCustomers'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/web-c-customers`

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { ...getSaasAuthHeader(), ...(init?.headers || {}) }
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error((data as { detail?: string }).detail || (data as { error?: string }).error || `请求失败（${response.status}）`)
  }
  return data as T
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

export interface WebCChannel {
  config_id: string
  name: string
  created_at?: string
}

export function listWebCChannels(): Promise<{ success: boolean; channels: WebCChannel[] }> {
  return request(`${API_BASE}/channels`)
}

export interface WebCCustomer {
  user_id: string
  username: string
  nickname: string | null
  display_name?: string
  channel_user_id?: string
  channel_config_id?: string
  channel_name?: string
  avatar_url: string | null
  first_session_at?: string
  last_session_at?: string
}

export function listWebCCustomers(params: { username?: string; config_id?: string; page?: number; page_size?: number }): Promise<{ success: boolean; users: WebCCustomer[]; total: number }> {
  return request(`${API_BASE}/users${query(params)}`)
}

export interface WebCSession {
  session_id: string
  channel_user_id?: string
  subagent_id?: string
  created_at?: string
  updated_at?: string
  last_message_at?: string
}

export function listWebCCustomerSessions(params: {
  user_id: string; start_date?: string; end_date?: string; subagent_id?: string; config_id?: string; page?: number; page_size?: number
}): Promise<{ success: boolean; sessions: WebCSession[]; total: number }> {
  return request(`${API_BASE}/users/${encodeURIComponent(params.user_id)}/sessions${query({
    start_date: params.start_date, end_date: params.end_date, subagent_id: params.subagent_id, config_id: params.config_id,
    page: params.page, page_size: params.page_size
  })}`)
}

export function listWebCSessionMessages(params: {
  session_id: string; content_search?: string; page?: number; page_size?: number
}): Promise<{ success: boolean; messages: Record<string, unknown>[]; total: number }> {
  return request(`${API_BASE}/sessions/${encodeURIComponent(params.session_id)}/messages${query({
    content_search: params.content_search, page: params.page, page_size: params.page_size
  })}`)
}

export interface WebCStats {
  total_sessions: number
  total_messages: number
  active_users: number
  by_subagent: { subagent_id: string; subagent_name?: string; sessions: number }[]
}

export function getWebCStats(params: { start_date?: string; end_date?: string }): Promise<WebCStats> {
  return request(`${API_BASE}/stats${query(params)}`)
}

export interface LeadStats {
  total_leads: number
  by_contact_method: { contact_method: string; count: number; ratio: number }[]
  by_kf_account: { channel_chat_id: string | null; kf_account_name: string | null; count: number; ratio: number }[]
}

export function getWebCLeadStats(params: { start_date?: string; end_date?: string }): Promise<LeadStats> {
  return request(`${API_BASE}/lead-stats${query(params)}`)
}

export interface WebCLead {
  lead_id: string
  contact_name: string | null
  contact_method: string
  phone: string | null
  other_contact: Record<string, unknown> | null
  demand_summary: string | null
  stage: string
  intention?: string
  kf_account_name: string | null
  created_at: string
}

export function listWebCLeads(params: {
  start_date?: string; end_date?: string; stage?: string; page?: number; page_size?: number
}): Promise<{ success: boolean; leads: WebCLead[]; total: number }> {
  return request(`${API_BASE}/leads${query(params)}`)
}

export function getWebCLeadDetail(leadId: string): Promise<{ success: boolean; lead: WebCLead }> {
  return request(`${API_BASE}/leads/${encodeURIComponent(leadId)}`)
}

export function updateWebCLeadStage(leadId: string, stage: string): Promise<{ success: boolean; lead: WebCLead }> {
  return request(`${API_BASE}/leads/${encodeURIComponent(leadId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stage })
  })
}
