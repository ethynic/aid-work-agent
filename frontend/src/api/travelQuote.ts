/**
 * 旅游报价定价数据管理 API
 */

import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/v1/travel-quote`

// ============================================================
// 通用 CRUD 函数
// ============================================================

async function crudList(resource: string, params?: Record<string, string>): Promise<any[]> {
  const query = params ? '?' + new URLSearchParams(params).toString() : ''
  const res = await fetch(`${API_BASE}/${resource}${query}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error(`获取${resource}列表失败`)
  const json = await res.json()
  return json.data || []
}

async function crudCreate(resource: string, data: Record<string, any>): Promise<any> {
  const res = await fetch(`${API_BASE}/${resource}`, {
    method: 'POST',
    headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error(`创建${resource}失败`)
  const json = await res.json()
  return json.data
}

async function crudUpdate(resource: string, id: number, data: Record<string, any>): Promise<any> {
  const res = await fetch(`${API_BASE}/${resource}/${id}`, {
    method: 'PUT',
    headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error(`更新${resource}失败`)
  const json = await res.json()
  return json.data
}

async function crudDelete(resource: string, id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/${resource}/${id}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error(`删除${resource}失败`)
}

// ============================================================
// 区域管理
// ============================================================

export const regions = {
  list: (params?: { name?: string }) => crudList('regions', params),
  create: (data: any) => crudCreate('regions', data),
  update: (id: number, data: any) => crudUpdate('regions', id, data),
  delete: (id: number) => crudDelete('regions', id)
}

// ============================================================
// 车辆价格
// ============================================================

export const vehicles = {
  list: (params?: { region_name?: string }) => crudList('vehicles', params),
  create: (data: any) => crudCreate('vehicles', data),
  update: (id: number, data: any) => crudUpdate('vehicles', id, data),
  delete: (id: number) => crudDelete('vehicles', id)
}

// ============================================================
// 景点管理
// ============================================================

export const attractions = {
  list: (params?: { region_name?: string }) => crudList('attractions', params),
  create: (data: any) => crudCreate('attractions', data),
  update: (id: number, data: any) => crudUpdate('attractions', id, data),
  delete: (id: number) => crudDelete('attractions', id)
}

// ============================================================
// 门票管理（嵌套在景点下）
// ============================================================

export const tickets = {
  list: (attractionId: number) => crudList(`attractions/${attractionId}/tickets`),
  create: (attractionId: number, data: any) => crudCreate(`attractions/${attractionId}/tickets`, data),
  update: (id: number, data: any) => crudUpdate('tickets', id, data),
  delete: (id: number) => crudDelete('tickets', id)
}

// ============================================================
// 酒店管理
// ============================================================

export const hotels = {
  list: (params?: { region_name?: string }) => crudList('hotels', params),
  create: (data: any) => crudCreate('hotels', data),
  update: (id: number, data: any) => crudUpdate('hotels', id, data),
  delete: (id: number) => crudDelete('hotels', id)
}

// ============================================================
// 房型管理（嵌套在酒店下）
// ============================================================

export const rooms = {
  list: (hotelId: number) => crudList(`hotels/${hotelId}/rooms`),
  create: (hotelId: number, data: any) => crudCreate(`hotels/${hotelId}/rooms`, data),
  update: (id: number, data: any) => crudUpdate('rooms', id, data),
  delete: (id: number) => crudDelete('rooms', id)
}

// ============================================================
// 餐标价格
// ============================================================

export const meals = {
  list: (params?: { region_name?: string; meal_tier?: string }) => crudList('meals', params),
  create: (data: any) => crudCreate('meals', data),
  update: (id: number, data: any) => crudUpdate('meals', id, data),
  delete: (id: number) => crudDelete('meals', id)
}

// ============================================================
// 导游费用
// ============================================================

export const guides = {
  list: (params?: { region_name?: string; guide_type?: string }) => crudList('guides', params),
  create: (data: any) => crudCreate('guides', data),
  update: (id: number, data: any) => crudUpdate('guides', id, data),
  delete: (id: number) => crudDelete('guides', id)
}

// ============================================================
// 其他费用
// ============================================================

export const fees = {
  list: (params?: { fee_category?: string }) => crudList('fees', params),
  create: (data: any) => crudCreate('fees', data),
  update: (id: number, data: any) => crudUpdate('fees', id, data),
  delete: (id: number) => crudDelete('fees', id)
}

// ============================================================
// 淡旺季配置
// ============================================================

export const seasons = {
  list: () => crudList('seasons'),
  create: (data: any) => crudCreate('seasons', data),
  update: (id: number, data: any) => crudUpdate('seasons', id, data),
  delete: (id: number) => crudDelete('seasons', id)
}

// ============================================================
// Excel 批量导入
// ============================================================

export interface ImportResult {
  total_imported: number
  total_skipped: number
  results: Array<{
    sheet: string
    table: string
    imported: number
    skipped: number
    errors: string[]
  }>
}

export async function importExcel(file: File): Promise<{ success: boolean; data: ImportResult }> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}/import/excel`, {
    method: 'POST',
    headers: { ...getAuthHeader() },
    body: formData
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: '导入失败' }))
    throw new Error(err.detail || '导入失败')
  }
  return res.json()
}

export async function downloadTemplate(): Promise<void> {
  const res = await fetch(`${API_BASE}/import/template`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('下载模板失败')
  const blob = await res.blob()
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'travel_quote_template.xlsx'
  a.click()
  window.URL.revokeObjectURL(url)
}
