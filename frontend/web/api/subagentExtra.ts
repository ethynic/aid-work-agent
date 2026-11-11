/**
 * 租户扩展提示词（extra）回退 API
 *
 * 对应后端 src/api/subagent_extra.py，前缀 /api/v1/subagents/{name}/extra。
 * 鉴权走 getSaasAuthHeader（自动带租户态 Authorization + X-Tenant-Id）。
 */
import { getSaasAuthHeader } from './saasTenant'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/v1/subagents`

export interface PreviousExtraResult {
  success: boolean
  /** null 表示无可回退版本；空串表示上一版为空（回退后使用默认配置）。回退入口显隐以 version 为准 */
  content: string | null
  version: number | null
  message?: string
}

export interface RollbackExtraResult {
  success: boolean
  version: number
  content: string
}

/** 获取上一版扩展提示词内容（回退前预览）；version 为 null 表示无可回退，content 空串表示上一版为空 */
export async function getPreviousExtra(subagentName: string): Promise<PreviousExtraResult> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(subagentName)}/extra/previous`, {
    headers: getSaasAuthHeader(),
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(data?.detail || data?.error || `请求失败: ${response.status}`)
  }
  return data
}

/** 回退扩展提示词到上一版（上一版内容存为新版本，最新即生效） */
export async function rollbackExtra(subagentName: string): Promise<RollbackExtraResult> {
  const response = await fetch(`${API_BASE}/${encodeURIComponent(subagentName)}/extra/rollback`, {
    method: 'POST',
    headers: getSaasAuthHeader(),
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(data?.detail || data?.error || `请求失败: ${response.status}`)
  }
  return data
}
