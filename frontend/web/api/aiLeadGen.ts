/**
 * AI获客智能体 — 业务数据查询 API（只读）
 */

import { getTenantScopedKey } from './tenantStorage'
import { credentialGet } from '@/platform/credentialStore'

const API_BASE = import.meta.env.VITE_API_BASE || ''

function getAuthHeaders(): Record<string, string> {
  const tenantMatch = window.location.pathname.match(/^\/t\/([^/]+)/)
  const token = import.meta.env.VITE_DESKTOP_TARGET === 'true'
    ? credentialGet(getTenantScopedKey('saas_token'))
    : localStorage.getItem('token')
  const tenantId = import.meta.env.VITE_DESKTOP_TARGET === 'true'
    ? (tenantMatch?.[1] || '')
    : (localStorage.getItem('tenantId') || '')
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (tenantId) headers['X-Tenant-Id'] = tenantId
  headers['Content-Type'] = 'application/json'
  return headers
}

// --- 类型定义 ---

export interface Campaign {
  id: number
  campaign_name: string
  brief?: string
  target_region?: string
  send_mode?: string
  status?: string
  daily_limit?: number
  scheduler_task_id?: string
  reply_mailbox?: string
  send_mailbox_level?: string
  product_refs?: string
  created_at?: string
  updated_at?: string
}

export interface CampaignOption {
  id: number
  campaign_name: string
  status?: string
}

export interface Lead {
  id: number
  campaign_id: number
  campaign_name?: string
  company_name?: string
  country?: string
  city?: string
  website?: string
  industry?: string
  employees?: string
  founded?: string
  source?: string
  source_url?: string
  score?: number
  status?: string
  reject_reason?: string
  notes?: string
  created_at?: string
  updated_at?: string
}

export interface Contact {
  id: number
  campaign_id: number
  campaign_name?: string
  lead_id?: number
  company_name?: string
  name?: string
  title?: string
  email?: string
  email_source?: string
  email_type?: string
  email_function?: string
  email_relevance?: string
  confidence?: string
  language?: string
  phone?: string
  linkedin?: string
  notes?: string
  created_at?: string
  updated_at?: string
}

export interface EmailRecord {
  id: number
  campaign_id: number
  campaign_name?: string
  contact_id?: number
  contact_name?: string
  contact_email?: string
  subject_a?: string
  subject_b?: string
  ab_group?: string
  body?: string
  body_preview?: string
  status?: string
  message_id?: string
  sent_at?: string
  error?: string
  retry_count?: number
  created_at?: string
  updated_at?: string
}

export interface ReplyRecord {
  id: number
  campaign_id: number
  campaign_name?: string
  contact_id?: number
  contact_name?: string
  contact_email?: string
  email_id?: number
  from_addr?: string
  subject?: string
  body?: string
  body_preview?: string
  received_at?: string
  intent?: string
  intent_summary?: string
  notified?: boolean
  message_id?: string
  created_at?: string
}

export interface ListResponse<T> {
  total: number
  page: number
  page_size: number
  items: T[]
}

export interface ListParams {
  campaign_id?: string
  status?: string
  send_mode?: string
  confidence?: string
  email_source?: string
  ab_group?: string
  intent?: string
  notified?: boolean
  keyword?: string
  page?: number
  page_size?: number
}

// --- 枚举映射表（5 页共用）---

export const campaignStatusMap: Record<string, string> = {
  draft: '草稿', searching: '搜索中', drafting: '撰写中', awaiting_review: '待审核',
  sending: '发送中', paused: '已暂停', completed: '已完成', failed: '失败',
}

export const sendModeMap: Record<string, string> = {
  review: '人工审核', auto: '全自动',
}

export const leadStatusMap: Record<string, string> = {
  new: '新线索', qualified: '已合格', rejected: '已排除', contacted: '已联系',
}

export const emailStatusMap: Record<string, string> = {
  draft: '草稿', approved: '已批准', sending: '发送中', sent: '已发送',
  failed: '发送失败', bounced: '退信',
}

export const emailSourceMap: Record<string, string> = {
  official_site: '官网', directory: '名录', verified_pattern: '验证模板',
  guessed: '推测', manual: '手动录入',
}

export const emailTypeMap: Record<string, string> = {
  personal: '个人', department: '部门', generic: '通用', unknown: '未知',
}

export const confidenceMap: Record<string, string> = {
  high: '高', medium: '中', low: '低',
}

export const relevanceMap: Record<string, string> = {
  relevant: '相关', generic: '泛用', unknown: '未知',
}

export const intentMap: Record<string, string> = {
  interested: '有意向', info_request: '索要资料', refused: '拒绝',
  irrelevant: '无关', unclear: '不明确',
}

type BadgeIntent = 'success' | 'warning' | 'danger' | 'info' | 'gray'

const badgeClassMap: Record<BadgeIntent, string> = {
  success: 'bg-success-100 text-success-700',
  warning: 'bg-warning-100 text-warning-700',
  danger: 'bg-danger-100 text-danger-700',
  info: 'bg-info-100 text-info-700',
  gray: 'bg-gray-100 text-muted',
}

function badgeOf(map: Record<string, string>, colors: Record<string, BadgeIntent>, value?: string | null) {
  if (!value) return { label: '-', class: badgeClassMap.gray }
  return {
    label: map[value] || value,
    class: badgeClassMap[colors[value] || 'gray'],
  }
}

const campaignStatusColors: Record<string, BadgeIntent> = {
  completed: 'success', failed: 'danger', awaiting_review: 'warning', sending: 'warning',
  paused: 'warning', draft: 'info', searching: 'info', drafting: 'info',
}

const sendModeColors: Record<string, BadgeIntent> = {
  review: 'info', auto: 'success',
}

const leadStatusColors: Record<string, BadgeIntent> = {
  qualified: 'success', contacted: 'info', new: 'info', rejected: 'danger',
}

const emailStatusColors: Record<string, BadgeIntent> = {
  sent: 'success', failed: 'danger', bounced: 'danger', approved: 'success',
  sending: 'warning', draft: 'info',
}

const confidenceColors: Record<string, BadgeIntent> = {
  high: 'success', medium: 'warning', low: 'gray',
}

const emailSourceColors: Record<string, BadgeIntent> = {
  official_site: 'success', verified_pattern: 'success', directory: 'info',
  manual: 'info', guessed: 'warning',
}

const intentColors: Record<string, BadgeIntent> = {
  interested: 'success', info_request: 'warning', refused: 'danger',
  irrelevant: 'gray', unclear: 'warning',
}

export function campaignStatusBadge(value?: string | null) {
  return badgeOf(campaignStatusMap, campaignStatusColors, value)
}
export function sendModeBadge(value?: string | null) {
  return badgeOf(sendModeMap, sendModeColors, value)
}
export function leadStatusBadge(value?: string | null) {
  return badgeOf(leadStatusMap, leadStatusColors, value)
}
export function emailStatusBadge(value?: string | null) {
  return badgeOf(emailStatusMap, emailStatusColors, value)
}
export function confidenceBadge(value?: string | null) {
  return badgeOf(confidenceMap, confidenceColors, value)
}
export function emailSourceBadge(value?: string | null) {
  return badgeOf(emailSourceMap, emailSourceColors, value)
}
export function intentBadge(value?: string | null) {
  return badgeOf(intentMap, intentColors, value)
}

// --- API 函数 ---

async function request<T>(path: string, params: Record<string, any> = {}): Promise<T> {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') query.set(k, String(v))
  })
  const qs = query.toString()
  const res = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ''}`, { headers: getAuthHeaders() })
  const json = await res.json()
  if (!json.success) throw new Error(json.error || '查询失败')
  return json.data
}

export const aiLeadGenAPI = {
  listCampaigns: (params: ListParams = {}) =>
    request<ListResponse<Campaign>>('/api/ai-lead-gen/campaigns', params),

  getCampaignOptions: () =>
    request<{ items: CampaignOption[] }>('/api/ai-lead-gen/campaigns/options'),

  listLeads: (params: ListParams = {}) =>
    request<ListResponse<Lead>>('/api/ai-lead-gen/leads', params),

  listContacts: (params: ListParams = {}) =>
    request<ListResponse<Contact>>('/api/ai-lead-gen/contacts', params),

  listEmails: (params: ListParams = {}) =>
    request<ListResponse<EmailRecord>>('/api/ai-lead-gen/emails', params),

  getEmailDetail: (emailId: number) =>
    request<EmailRecord>(`/api/ai-lead-gen/emails/${emailId}`),

  listReplies: (params: ListParams = {}) =>
    request<ListResponse<ReplyRecord>>('/api/ai-lead-gen/replies', params),

  getReplyDetail: (replyId: number) =>
    request<ReplyRecord>(`/api/ai-lead-gen/replies/${replyId}`),
}
