/**
 * 使用教程文档中心 API（Portal）
 *
 * 读取 docs/guides/ 下的教程/手册/指南文档。
 * 后端：src/api/guides.py（仅平台管理员）
 */

import { getAuthHeader } from '@/api/auth'

const API_BASE = import.meta.env.VITE_API_BASE || ''

// ============== 类型定义 ==============

export interface GuideDocMeta {
  path: string
  title: string
  updated_at: string
  size_bytes: number
}

export interface GuideCategory {
  dir: string
  name: string
  docs: GuideDocMeta[]
}

export interface GuideCatalog {
  categories: GuideCategory[]
}

export interface GuideDoc {
  path: string
  title: string
  content: string
  updated_at: string
}

// ============== API ==============

async function guidesFetch<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { ...getAuthHeader() } })
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }
  return response.json()
}

/**
 * 获取教程文档目录（按分类分组）
 */
export async function getGuidesCatalog(): Promise<GuideCatalog> {
  return guidesFetch(`${API_BASE}/api/guides/catalog`)
}

/**
 * 获取单篇教程文档内容
 */
export async function getGuideDoc(path: string): Promise<GuideDoc> {
  return guidesFetch(`${API_BASE}/api/guides/doc?path=${encodeURIComponent(path)}`)
}
