/**
 * SaaS 数字员工授权 API Client
 * 所有 /api/saas/permissions/* 调用的统一封装
 */

import { getTenantScopedKey } from './tenantStorage'
import { credentialGet } from '@/platform/credentialStore'
import { triggerNativeDownload } from '@/utils/download'

// 复用 saasTenant 的 getSaasAuthHeader
function getTokenKey(): string {
  return getTenantScopedKey('saas_token')
}

function getCurrentTenantId(): string | null {
  const path = window.location.pathname
  const match = path.match(/^\/t\/([^/]+)/)
  return match ? match[1] : null
}

function getSaasAuthHeader(): Record<string, string> {
  const headers: Record<string, string> = {}

  // 根据路由获取对应的 token
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  // 从 URL 路径获取 tenant_id，添加到 Header
  const tenantId = getCurrentTenantId()
  if (tenantId) {
    headers['X-Tenant-Id'] = tenantId
  }

  return headers
}

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/permissions`

export interface AgentItem {
  agent_id: string
  name: string
  description: string
  type: 'builtin' | 'custom'
  business_pages?: any[]
  // 实例相关字段（租户模式下返回实例时包含）
  instance_id?: string  // 实例ID，租户模式下必填
  display_name?: string  // 显示名称
  instance_name?: string // 实例名称（如"外贸获客智能体 - 实例1"）
  subagent_type?: string // 子智能体类型
  status?: string       // 实例状态：idle/busy
  avatar?: string       // 头像
  [key: string]: any  // 允许其他字段
}

// ==================== 平台管理员 - 租户授权 ====================

export async function getTenantAgentPermissions(tenantId: string): Promise<{
  success: boolean
  data?: { agent_ids: string[]; count: number; agent_quotas?: Record<string, number> }
  message?: string
}> {
  const res = await fetch(`${API_BASE}/tenant/${encodeURIComponent(tenantId)}/agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取租户数字员工授权失败')
  return res.json()
}

export async function setTenantAgentPermissions(tenantId: string, agentIds: string[], agentQuotas: Record<string, number> = {}): Promise<{
  success: boolean
  message?: string
}> {
  const headers = getSaasAuthHeader()
  headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE}/tenant/${encodeURIComponent(tenantId)}/agents`, {
    method: 'POST',
    headers: headers,
    body: JSON.stringify({ agent_ids: agentIds, agent_quotas: agentQuotas }),
  })
  if (!res.ok) throw new Error('设置租户数字员工授权失败')
  return res.json()
}

export async function getAllAvailableAgents(): Promise<{
  success: boolean
  data?: AgentItem[]
}> {
  const res = await fetch(`${API_BASE}/available-agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取所有可用数字员工失败')
  return res.json()
}

// ==================== 租户管理员 - 用户授权 ====================

export async function getUserAgentPermissions(userId: string): Promise<{
  success: boolean
  data?: { agent_ids: string[]; count: number }
  message?: string
}> {
  const res = await fetch(`${API_BASE}/user/${encodeURIComponent(userId)}/agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取用户数字员工授权失败')
  return res.json()
}

export async function setUserAgentPermissions(userId: string, agentIds: string[]): Promise<{
  success: boolean
  message?: string
}> {
  const headers = getSaasAuthHeader()
  headers['Content-Type'] = 'application/json'
  const res = await fetch(`${API_BASE}/user/${encodeURIComponent(userId)}/agents`, {
    method: 'POST',
    headers: headers,
    body: JSON.stringify({ agent_ids: agentIds }),
  })
  if (!res.ok) throw new Error('设置用户数字员工授权失败')
  return res.json()
}

export async function getTenantAvailableUserAgents(): Promise<{
  success: boolean
  data?: AgentItem[]
}> {
  const res = await fetch(`${API_BASE}/tenant/available-user-agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取租户可用数字员工失败')
  return res.json()
}

// ==================== 当前用户 - 获取可访问的数字员工 ====================

export async function getMyAllowedAgents(): Promise<{
  success: boolean
  data?: AgentItem[]
  count: number
}> {
  const res = await fetch(`${API_BASE}/my/allowed-agents`, {
    headers: getSaasAuthHeader(),
  })
  if (!res.ok) throw new Error('获取当前用户可访问数字员工失败')
  return res.json()
}

// ==================== 子智能体环境变量 ====================

const ENV_VAR_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/tenant/subagent-env-vars`

function getEnvVarAuthHeader(tenantId: string): Record<string, string> {
  const headers: Record<string, string> = {}
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  headers['X-Tenant-Id'] = tenantId
  return headers
}

export interface EnvVarItem {
  tenant_id: string
  subagent_name: string
  var_name: string
  var_value: string
  description: string | null
  created_at: string
  updated_at: string
}

export async function getSubagentEnvVars(tenantId: string, subagentName: string): Promise<{
  success: boolean
  data?: EnvVarItem[]
}> {
  const res = await fetch(`${ENV_VAR_BASE}/${encodeURIComponent(subagentName)}`, {
    headers: getEnvVarAuthHeader(tenantId),
  })
  if (!res.ok) throw new Error('获取环境变量失败')
  return res.json()
}

export async function setSubagentEnvVars(
  tenantId: string,
  subagentName: string,
  vars: Array<{ name: string; value: string; description?: string }>,
): Promise<{ success: boolean; message?: string }> {
  const headers = getEnvVarAuthHeader(tenantId)
  headers['Content-Type'] = 'application/json'
  const res = await fetch(`${ENV_VAR_BASE}/${encodeURIComponent(subagentName)}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify({ vars }),
  })
  if (!res.ok) throw new Error('设置环境变量失败')
  return res.json()
}

// ==================== 租户配置文件管理 ====================

const CONFIG_FILE_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/tenant/config-file`

export interface ConfigFileStatus {
  success: boolean
  configured: boolean
  filename?: string
  size?: number
  updated_at?: number
}

export async function getConfigFileStatus(tenantId: string, subagentName: string): Promise<ConfigFileStatus> {
  const headers: Record<string, string> = {}
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const res = await fetch(`${CONFIG_FILE_BASE}/${encodeURIComponent(subagentName)}/status`, { headers })
  if (!res.ok) throw new Error('获取配置文件状态失败')
  return res.json()
}

export async function uploadConfigFile(tenantId: string, subagentName: string, file: File): Promise<{ success: boolean; message?: string }> {
  const headers: Record<string, string> = {}
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${CONFIG_FILE_BASE}/${encodeURIComponent(subagentName)}`, {
    method: 'POST',
    headers,
    body: formData,
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || '上传配置文件失败')
  }
  return res.json()
}

export async function downloadConfigFile(tenantId: string, subagentName: string): Promise<void> {
  // 下载票据流程：POST 认证换票据 -> 原生下载直链（带进度条），规范见 backend_dev.md
  const headers: Record<string, string> = {}
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const res = await fetch(`${CONFIG_FILE_BASE}/${encodeURIComponent(subagentName)}/download_ticket`, {
    method: 'POST',
    headers
  })
  if (!res.ok) throw new Error('下载配置文件失败')
  const { ticket } = await res.json()
  if (!ticket) throw new Error('获取下载票据失败')
  triggerNativeDownload(
    `${CONFIG_FILE_BASE}/${encodeURIComponent(subagentName)}?ticket=${encodeURIComponent(ticket)}`,
    `${subagentName}-api.md`
  )
}

export async function deleteConfigFile(tenantId: string, subagentName: string): Promise<{ success: boolean; message?: string }> {
  const headers: Record<string, string> = {}
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const res = await fetch(`${CONFIG_FILE_BASE}/${encodeURIComponent(subagentName)}`, {
    method: 'DELETE',
    headers,
  })
  if (!res.ok) throw new Error('删除配置文件失败')
  return res.json()
}

// ==================== 租户级子智能体知识库关联 ====================

const KNOWLEDGE_BASE_URL = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/tenant/subagent-knowledge`

export interface KnowledgeSourceItem {
  source_type: string
  display_name: string
  owner_tenant_id?: string | null
}

// ==================== 租户间知识库共享授权（平台管理员） ====================

const SHARE_BASE_URL = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/tenant/knowledge-shares`

export interface KnowledgeShareItem {
  from_tenant_id: string
  from_company_name: string
}

export async function getTenantKnowledgeShares(tenantId: string): Promise<{ success: boolean; data: KnowledgeShareItem[] }> {
  const headers: Record<string, string> = {}
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const res = await fetch(`${SHARE_BASE_URL}`, { headers })
  if (!res.ok) throw new Error('获取知识库接入失败')
  return res.json()
}

export async function setTenantKnowledgeShares(
  tenantId: string, fromTenantIds: string[]
): Promise<{ success: boolean; message?: string; error?: string }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const res = await fetch(`${SHARE_BASE_URL}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify({ from_tenants: fromTenantIds.map(id => ({ from_tenant_id: id })) }),
  })
  if (!res.ok) throw new Error('保存知识库接入失败')
  return res.json()
}

export async function getSubagentKnowledgeSources(tenantId: string, subagentName: string): Promise<{ success: boolean; data: KnowledgeSourceItem[] }> {
  const headers: Record<string, string> = {}
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const res = await fetch(`${KNOWLEDGE_BASE_URL}/${encodeURIComponent(subagentName)}`, { headers })
  if (!res.ok) throw new Error('获取知识库关联失败')
  return res.json()
}

export async function setSubagentKnowledgeSources(
  tenantId: string, subagentName: string, sources: KnowledgeSourceItem[]
): Promise<{ success: boolean; message?: string; error?: string }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const res = await fetch(`${KNOWLEDGE_BASE_URL}/${encodeURIComponent(subagentName)}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify({ sources }),
  })
  if (!res.ok) throw new Error('设置知识库关联失败')
  return res.json()
}

export async function listTenantKnowledgeCategories(tenantId: string): Promise<{ items: { id: number; source_type: string; display_name: string | null; parent_id: number | null; document_count: number }[] }> {
  const headers: Record<string, string> = {}
  const tokenKey = getTokenKey()
  const token = credentialGet(tokenKey)
  if (token) headers['Authorization'] = `Bearer ${token}`
  headers['X-Tenant-Id'] = tenantId
  const KB_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/knowledge`
  const res = await fetch(`${KB_BASE}/categories`, { headers })
  if (!res.ok) throw new Error('获取知识库分类失败')
  return res.json()
}
