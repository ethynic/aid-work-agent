/**
 * 本地工具设备管理 API
 *
 * 对应后端 src/local_tools/api.py 的 Web 用户 API：
 *   POST   /api/local-tools/pairing-tickets        生成一次性配对码（5 分钟有效）
 *   GET    /api/local-tools/devices                当前用户设备列表
 *   POST   /api/local-tools/devices/{id}/select    选定当前设备（单选）
 *   DELETE /api/local-tools/devices/{id}           撤销设备
 *
 * 错误处理：后端错误响应为 HTTP 状态码 + FastAPI detail（{detail: {error, debug}}），
 * 统一解析为中文 Error 抛出，页面层用 alert 展示。
 */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/local-tools`

/**
 * 设备视图（对齐后端 GET /devices 返回字段）
 * 注意：后端主键字段名为 device_id（非 id），列表视图不含 manifest_digest
 */
export interface LocalToolDevice {
  device_id: string
  name: string | null
  platform: string | null
  runtime_version: string | null
  capabilities?: Record<string, any> | null
  selected: boolean
  status: string | null
  online: boolean
  last_seen_at: string | null
  created_at: string | null
}

export interface PairingTicket {
  code: string
  expires_at: string
}

/**
 * 解析后端错误响应并抛出中文错误
 * 兼容两种结构：FastAPI HTTPException 的 {detail: {error}} 与业务层的 {success: false, error}
 */
async function throwApiError(res: Response, fallback: string): Promise<never> {
  let message = fallback
  try {
    const body = await res.json()
    const detail = body?.detail
    if (detail && typeof detail === 'object' && detail.error) {
      message = detail.error
    } else if (typeof detail === 'string') {
      message = detail
    } else if (body?.error) {
      message = body.error
    }
  } catch {
    // 响应体非 JSON 时使用兜底文案
  }
  throw new Error(message)
}

/** 校验成功响应（res.ok 且 body.success !== false），失败则抛中文错误 */
async function ensureSuccess(res: Response, fallback: string): Promise<any> {
  if (!res.ok) {
    await throwApiError(res, fallback)
  }
  const body = await res.json()
  if (body && body.success === false) {
    throw new Error(body.error || fallback)
  }
  return body
}

/**
 * 查询当前用户的本地工具设备列表
 */
export async function listDevices(): Promise<LocalToolDevice[]> {
  console.log('前端日志：查询本地工具设备列表')
  const res = await fetch(`${API_BASE}/devices`, {
    headers: { ...getAuthHeader() }
  })
  const body = await ensureSuccess(res, '查询设备列表失败，请稍后重试')
  return body.devices || []
}

/**
 * 生成一次性配对码（明文仅此一次返回，5 分钟有效）
 */
export async function createPairingTicket(): Promise<PairingTicket> {
  console.log('前端日志：生成本地工具配对码')
  const res = await fetch(`${API_BASE}/pairing-tickets`, {
    method: 'POST',
    headers: { ...getAuthHeader() }
  })
  const body = await ensureSuccess(res, '创建配对码失败，请稍后重试')
  return { code: body.code, expires_at: body.expires_at }
}

/**
 * 选定当前设备（单选，新选定会取消其他设备的选定状态）
 */
export async function selectDevice(deviceId: string): Promise<void> {
  console.log('前端日志：选定本地工具设备', { deviceId })
  const res = await fetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}/select`, {
    method: 'POST',
    headers: { ...getAuthHeader() }
  })
  await ensureSuccess(res, '选定设备失败，请稍后重试')
}

/**
 * 撤销（解绑）设备，设备 token 立即失效
 */
export async function revokeDevice(deviceId: string): Promise<void> {
  console.log('前端日志：解绑本地工具设备', { deviceId })
  const res = await fetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() }
  })
  await ensureSuccess(res, '解绑设备失败，请稍后重试')
}
