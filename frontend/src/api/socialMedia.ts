import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/social-media`

export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
  debug?: string
}

export interface SocialAccount {
  account_id: string
  platform: string
  display_name: string
  external_account_id?: string
  status: string
  capabilities_json?: { supported?: string[]; limits?: Record<string, unknown> }
  last_validated_at?: string
}

export interface ContentPlan {
  plan_id: string
  name: string
  period_start?: string
  period_end?: string
  goal?: string
  target_audience?: string
  status: string
}

export interface PublishJob {
  job_id: string
  account_id: string
  account_name?: string
  platform?: string
  variant_id: string
  publish_mode: string
  status: string
  scheduled_at?: string
  created_at?: string
}

async function request<T>(path: string, options: RequestInit = {}): Promise<ApiResponse<T>> {
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeader(),
    ...(options.headers || {}),
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
  return res.json()
}

export const socialMediaAPI = {
  listAccounts: () => request<{ items: SocialAccount[] }>('/accounts'),
  createAccount: (body: Record<string, unknown>) => request<{ account_id: string }>('/accounts', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  refreshCapabilities: (accountId: string) => request<Record<string, unknown>>(`/accounts/${accountId}/refresh-capabilities`, {
    method: 'POST',
  }),
  listPlans: () => request<{ items: ContentPlan[] }>('/plans'),
  createPlan: (body: Record<string, unknown>) => request<{ plan_id: string }>('/plans', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  createMaster: (body: Record<string, unknown>) => request<{ master_id: string; content_hash: string }>('/content-masters', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  createVariant: (masterId: string, body: Record<string, unknown>) => request<{ variant_id: string; validation: Record<string, unknown> }>(`/content-masters/${masterId}/variants`, {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  submitReview: (variantId: string) => request(`/variants/${variantId}/submit-review`, { method: 'POST' }),
  approveVariant: (variantId: string, comment = '') => request(`/variants/${variantId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ comment }),
  }),
  createPublishJob: (body: Record<string, unknown>) => request<{ job_id: string; status: string }>('/publish-jobs', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  listPublishJobs: () => request<{ items: PublishJob[] }>('/publish-jobs'),
  analyticsOverview: () => request<{ publish_jobs_by_status: Record<string, number>; accounts_by_platform: Record<string, number> }>('/analytics/overview'),
}
