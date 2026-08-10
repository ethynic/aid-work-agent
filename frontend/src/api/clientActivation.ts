/**
 * 协会客户端激活码管理 API Client（平台管理员用）。
 * 后端：src/saas/api/client_activation_mgmt.py，路由前缀 /api/saas/client-activations。
 * 认证复用 saasTenant.getSaasAuthHeader（/portal 下取 portal_token；激活码端点用
 * body/query 的 tenant_id，不依赖 X-Tenant-Id header）。
 */
import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/saas/client-activations`

export interface ActivationCode {
  id: number
  code: string
  tenant_id: string
  client_name: string | null
  status: 'unused' | 'used' | 'disabled' | string
  max_uses: number
  used_count: number
  activated_at: string | null
  expires_at: string | null
  created_at: string | null
}

export interface CreatedActivationCode {
  id: number
  code: string // 明文，仅创建时返回（list 也返回明文）
  tenant_id: string
  client_name: string | null
  status: string
  max_uses: number
  expires_at: string | null
  created_at: string | null
}

async function parseError(res: Response, fallback: string): Promise<never> {
  let detail = fallback
  try {
    const data = await res.json()
    detail = data.detail || data.error || data.message || fallback
  } catch {
    /* 保持 fallback */
  }
  throw new Error(detail)
}

/** 按租户列出激活码（后端返回裸数组）。 */
export async function listActivationCodes(tenantId: string): Promise<ActivationCode[]> {
  const res = await fetch(`${API_BASE}/list?tenant_id=${encodeURIComponent(tenantId)}`, {
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) await parseError(res, '获取激活码列表失败')
  return res.json()
}

/** 生成激活码（返回含明文 code，需当场展示给管理员）。 */
export async function createActivationCode(params: {
  tenant_id: string
  client_name?: string
  expires_at?: string // ISO 8601
  max_uses?: number
}): Promise<CreatedActivationCode> {
  const res = await fetch(API_BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getSaasAuthHeader() },
    body: JSON.stringify(params),
  })
  if (!res.ok) await parseError(res, '生成激活码失败')
  return res.json()
}

/** 禁用激活码（未激活的不可再激活）。 */
export async function disableActivationCode(codeId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/${codeId}`, {
    method: 'DELETE',
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) await parseError(res, '禁用激活码失败')
}

/** 吊销该激活码关联的 active 绑定（踢客户端下线）。 */
export async function revokeActivationBinding(codeId: number): Promise<{ revoked_count: number }> {
  const res = await fetch(`${API_BASE}/${codeId}/revoke`, {
    method: 'POST',
    headers: { ...getSaasAuthHeader() },
  })
  if (!res.ok) await parseError(res, '吊销绑定失败')
  const data = await res.json()
  return { revoked_count: Number(data?.revoked_count ?? 0) }
}
