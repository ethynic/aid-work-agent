/**
 * 租户数字员工实例 API
 *
 * 对应后端 /api/saas/tenant/agent-instances（require_admin 鉴权，
 * getSaasAuthHeader 自动带 X-Tenant-Id）。
 */
import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/tenant/agent-instances`

export interface GreetingPrompt {
  label: string
  message: string
}

export interface TenantAgentInstance {
  agent_id: string
  base_agent_id: string
  base_source: string | null
  display_name: string | null
  description: string | null
  extra_prompt: string | null
  greeting_summary: string | null
  greeting_prompts: GreetingPrompt[] | null
  is_copy: boolean
  status: string
  created_at?: string
  updated_at?: string
}

export interface UpdateInstancePayload {
  display_name?: string | null
  description?: string | null
  extra_prompt?: string | null
  greeting_summary?: string | null
  greeting_prompts?: GreetingPrompt[] | null
}

async function request(path: string, init: RequestInit = {}): Promise<any> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader(), ...(init.headers || {}) },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(body?.detail || body?.error || `请求失败（${res.status}）`)
  }
  return body
}

export async function listTenantAgentInstances(): Promise<TenantAgentInstance[]> {
  const res = await request('')
  return res.data || []
}

/** 按 agent_id 查单条实例行（基础员工未定制过时无行，返回 null）；失败静默返回 null */
export async function getTenantAgentInstanceOrDefault(
  _tenantId: string,
  agentId: string,
): Promise<TenantAgentInstance | null> {
  try {
    const list = await listTenantAgentInstances()
    return list.find(i => i.agent_id === agentId) || null
  } catch {
    return null
  }
}

export async function createTenantAgentInstance(
  baseAgentId: string,
  displayName: string,
): Promise<TenantAgentInstance> {
  const res = await request('', {
    method: 'POST',
    body: JSON.stringify({ base_agent_id: baseAgentId, display_name: displayName }),
  })
  return res.data
}

export async function updateTenantAgentInstance(
  agentId: string,
  payload: UpdateInstancePayload,
): Promise<TenantAgentInstance> {
  const res = await request(`/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
  return res.data
}

export async function deleteTenantAgentInstance(agentId: string): Promise<void> {
  await request(`/${encodeURIComponent(agentId)}`, { method: 'DELETE' })
}
