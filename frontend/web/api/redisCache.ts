/**
 * Redis 缓存管理 API
 *
 * 平台管理员可视化查看/删除 Redis 缓存键值。
 * 设计文档：docs/system/design-redis-cache-admin.md
 */

import { getAuthHeader } from '@/api/auth'

const API_BASE = import.meta.env.VITE_API_BASE || ''

// ============== 类型定义 ==============

export interface RedisOverview {
  success: boolean
  total_keys: number
  registered_keys: number
  bare_keys: number
  redis_connected: boolean
  fallback_active: boolean
  key_prefix?: string
  error?: string
  debug?: string
}

export interface RedisKeyItem {
  key: string
  type: string  // string / hash / list / set / zset / none
  ttl: number  // -1=永不过期，-2=已过期
  size: number | null
  size_label: string
  ttl_info: {
    value: number
    label: string
    level: 'normal' | 'warning' | 'danger'
  }
  is_bare: boolean
}

export interface RedisKeyListResponse {
  success: boolean
  items: RedisKeyItem[]
  next_cursor: number
  has_more: boolean
  filter: {
    prefix: string | null
    search: string | null
    is_bare_view: boolean
  }
  error?: string
  debug?: string
}

export interface RedisKeyDetail {
  success: boolean
  key: string
  type?: string
  ttl?: number
  ttl_info?: {
    value: number
    label: string
    level: 'normal' | 'warning' | 'danger'
  }
  size?: number | null
  size_label?: string
  value?: any  // string 类型的值
  members?: any  // hash/list/set/zset 的成员
  member_count?: number
  truncated?: boolean
  is_bare?: boolean
  message?: string
  error?: string
  debug?: string
}

export interface RedisDeleteResponse {
  success: boolean
  deleted_key?: string
  error?: string
  debug?: string
}

// ============== API 函数 ==============

/**
 * 获取 Redis 概览统计
 */
export async function getRedisOverview(): Promise<RedisOverview> {
  const response = await fetch(`${API_BASE}/api/admin/redis/overview`, {
    headers: { ...getAuthHeader() },
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }
  return response.json()
}

/**
 * 获取键列表
 */
export async function listRedisKeys(params: {
  prefix?: string | null
  search?: string | null
  cursor?: number
  limit?: number
}): Promise<RedisKeyListResponse> {
  const searchParams = new URLSearchParams()
  if (params.prefix) searchParams.set('prefix', params.prefix)
  if (params.search) searchParams.set('search', params.search)
  if (params.cursor !== undefined) searchParams.set('cursor', String(params.cursor))
  if (params.limit !== undefined) searchParams.set('limit', String(params.limit))

  const response = await fetch(`${API_BASE}/api/admin/redis/keys?${searchParams.toString()}`, {
    headers: { ...getAuthHeader() },
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }
  return response.json()
}

/**
 * 获取单键详情
 */
export async function getRedisKeyDetail(key: string): Promise<RedisKeyDetail> {
  const response = await fetch(`${API_BASE}/api/admin/redis/keys/${encodeURIComponent(key)}`, {
    headers: { ...getAuthHeader() },
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }
  return response.json()
}

/**
 * 删除单键
 */
export async function deleteRedisKey(key: string): Promise<RedisDeleteResponse> {
  const response = await fetch(`${API_BASE}/api/admin/redis/keys/${encodeURIComponent(key)}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }
  return response.json()
}

export interface ClearAllCacheResponse {
  success: boolean
  deleted?: number
  keys_found?: number
  fallback_cleared?: number
  message?: string
  error?: string
}

/**
 * 清空所有 Redis 缓存（按 REDIS_KEY_PREFIX 隔离）
 *
 * 调用既有 /api/clear_cache 端点，平台管理员鉴权。
 */
export async function clearAllCache(): Promise<ClearAllCacheResponse> {
  const response = await fetch(`${API_BASE}/api/clear_cache`, {
    method: 'GET',
    credentials: 'include',
    headers: { ...getAuthHeader() },
  })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }
  return response.json()
}
