/**
 * 聊天实例并发控制 API
 */

import { getAuthHeader } from '@/api/auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/chat/instances`

// ================== 类型定义 ==================

export interface ChatInstance {
  instance_id: string
  tenant_id: string
  subagent_type: string
  display_name: string
  instance_name?: string
  avatar?: string
  description?: string
  personality_traits?: string[]
  status: 'idle' | 'busy' | 'stopped'
  current_session_id?: string
  current_user_id?: string
  current_user?: {
    name: string
    held_seconds: number
  }
  queue_length?: number
  total_chats?: number
  total_messages?: number
  can_take_over?: boolean
  status_text?: string
  created_at: string
  updated_at: string
}

export interface LockInstanceResponse {
  success: boolean
  was_idle?: boolean
  is_queued?: boolean
  queue_position?: number
  queue_length?: number
  estimated_wait_seconds?: number
  instance?: ChatInstance
  error?: string
}

export interface QueueStatusResponse {
  in_queue: boolean
  status: 'waiting' | 'ready' | 'expired' | 'cancelled' | 'abandoned' | 'not_in_queue'
  position?: number
  queue_length?: number
  estimated_wait_seconds?: number
}

// ================== API 方法 ==================

/**
 * 获取租户可用的数字员工实例列表
 */
export async function listInstances(
  subagent_type?: string
): Promise<{ success: boolean; instances: ChatInstance[] }> {
  const params = new URLSearchParams()
  if (subagent_type) {
    params.set('subagent_type', subagent_type)
  }

  const res = await fetch(`${API_BASE}?${params}`, {
    headers: getAuthHeader(),
  })
  return res.json()
}

/**
 * 尝试锁定实例
 */
export async function lockInstance(
  instance_id: string,
  session_id: string
): Promise<LockInstanceResponse> {
  const res = await fetch(`${API_BASE}/${instance_id}/lock`, {
    method: 'POST',
    headers: {
      ...getAuthHeader(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ session_id }),
  })
  return res.json()
}

/**
 * 检查排队状态
 */
export async function checkQueueStatus(
  instance_id: string,
  session_id: string
): Promise<QueueStatusResponse> {
  const res = await fetch(
    `${API_BASE}/${instance_id}/queue-status?session_id=${session_id}`,
    { headers: getAuthHeader() }
  )
  const data = await res.json()
  return data.data
}

/**
 * 释放实例锁
 */
export async function releaseInstance(
  instance_id: string,
  session_id: string
): Promise<{ success: boolean; released: boolean }> {
  const res = await fetch(`${API_BASE}/${instance_id}/release`, {
    method: 'POST',
    headers: {
      ...getAuthHeader(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ session_id }),
  })
  return res.json()
}

/**
 * 取消排队
 */
export async function cancelQueue(
  instance_id: string,
  session_id: string
): Promise<{ success: boolean; cancelled: boolean }> {
  const res = await fetch(
    `${API_BASE}/${instance_id}/queue?session_id=${session_id}`,
    { method: 'DELETE', headers: getAuthHeader() }
  )
  return res.json()
}

/**
 * 接管实例（跨设备会话切换）
 */
export async function takeOverInstance(
  instance_id: string,
  new_session_id: string
): Promise<{
  success: boolean
  old_session_id?: string
  instance?: ChatInstance
  error?: string
}> {
  const res = await fetch(`${API_BASE}/${instance_id}/take-over`, {
    method: 'POST',
    headers: {
      ...getAuthHeader(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ new_session_id }),
  })
  return res.json()
}
