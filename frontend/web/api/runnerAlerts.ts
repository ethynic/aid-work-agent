import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/platform`

export interface RunnerAlert {
  alert_id: string
  runner_id: string
  attempt: number
  tenant_id: string | null
  profile_id: string | null
  started_at: string
  alerted_at: string
  ended_at: string | null
  elapsed_seconds: number
  status?: string
}

export interface RunnerAlertDetail {
  alert: RunnerAlert
  runner: {
    request_text: string | null
    latest_model_response: string | null
    latest_model_context: unknown | null
    model_saved_at: string | null
    model_unavailable_reason?: string | null
    context_redacted?: boolean
  }
}

export interface RunnerMonitorStatus {
  last_successful_scan_at: string | null
  stalled: boolean
}

export interface RunnerAlertThreshold {
  tenant_id: string
  profile_id: string
  minutes: number
}

export interface RunnerAlertTarget {
  target_id: string
  installation_ref: string
  binding_version: number
  state: string
  enabled_since: string | null
  channel: string
  provider: string
  rule_id: string | null
  configuration_display: string | null
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...getAuthHeader(),
    ...(init.headers as Record<string, string> || {}),
  }
  // Platform alerts have global scope even when an admin previously managed a tenant.
  delete headers['X-Tenant-Id']
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error || `HTTP_${response.status}`)
  return data as T
}

export const runnerAlertsAPI = {
  list: (params: { state: 'all' | 'current' | 'history'; page: number; page_size: number; runner_id?: string }) => {
    const query = new URLSearchParams({ state: params.state, page: String(params.page), page_size: String(params.page_size) })
    if (params.runner_id) query.set('runner_id', params.runner_id)
    return request<{ success: boolean; items: RunnerAlert[]; total: number; page: number; page_size: number; monitor: RunnerMonitorStatus }>(`/runner-alerts?${query}`)
  },
  detail: (alertId: string) => request<{ success: boolean } & RunnerAlertDetail>(`/runner-alerts/${encodeURIComponent(alertId)}`),
  thresholds: () => request<{ success: boolean; default_minutes: number; overrides: RunnerAlertThreshold[] }>('/runner-alert-thresholds'),
  saveThreshold: (body: RunnerAlertThreshold) => request<{ success: boolean }>('/runner-alert-thresholds', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  deleteThreshold: (tenantId: string, profileId: string) => request<{ success: boolean }>(
    `/runner-alert-thresholds?${new URLSearchParams({ tenant_id: tenantId, profile_id: profileId })}`, { method: 'DELETE' },
  ),
  target: () => request<{ target: RunnerAlertTarget | null }>('/runner-alert-target'),
  saveTarget: (webhookUrl: string, expectedBindingVersion?: number) => request<{ target: RunnerAlertTarget }>('/runner-alert-target', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ webhook_url: webhookUrl, ...(expectedBindingVersion == null ? {} : { expected_binding_version: expectedBindingVersion }) }),
  }),
  deleteTarget: (bindingVersion: number) => request<{ success: boolean; target: RunnerAlertTarget }>('/runner-alert-target', {
    method: 'DELETE', headers: { 'X-Notification-Binding-Version': String(bindingVersion) },
  }),
}
