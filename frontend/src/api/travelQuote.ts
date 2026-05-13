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

export interface HotelKBImportResult {
  total_hotels: number
  imported: number
  skipped: number
  errors: string[]
  details: Array<{
    sheet: string
    total: number
    imported: number
    skipped: number
  }>
}

export interface AttractionKBImportResult {
  total_attractions: number
  imported: number
  skipped: number
  errors: string[]
  details: Array<{
    sheet: string
    total: number
    imported: number
    skipped: number
  }>
}

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

export interface VehicleImportResult {
  imported: number
  skipped: number
  errors: string[]
  details: Array<{
    total: number
    imported: number
    skipped: number
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

export async function importVehicleExcel(file: File): Promise<{ success: boolean; data: VehicleImportResult }> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}/import/vehicle-excel`, {
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

export async function importHotelExcelKB(file: File): Promise<{ success: boolean; data: HotelKBImportResult }> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}/import/hotel-excel-kb`, {
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

export async function importAttractionExcelKB(file: File): Promise<{ success: boolean; data: AttractionKBImportResult }> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${API_BASE}/import/attraction-excel-kb`, {
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

// ============================================================
// 知识库模式 API
// ============================================================

/** 向量搜索酒店 */
export async function searchHotelsKB(params: { q: string; top_k?: number }): Promise<any[]> {
  const sp: Record<string, string> = { q: params.q }
  if (params.top_k != null) sp.top_k = String(params.top_k)
  const query = '?' + new URLSearchParams(sp).toString()
  const res = await fetch(`${API_BASE}/search/hotels${query}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('搜索酒店失败')
  const json = await res.json()
  return json.data || []
}

/** 列出所有酒店知识库文档 */
export async function listHotelsKB(params?: { limit?: number; offset?: number }): Promise<{ total: number; items: any[] }> {
  const sp: Record<string, string> = {}
  if (params?.limit != null) sp.limit = String(params.limit)
  if (params?.offset != null) sp.offset = String(params.offset)
  const query = Object.keys(sp).length ? '?' + new URLSearchParams(sp).toString() : ''
  const res = await fetch(`${API_BASE}/kb/hotels${query}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取酒店列表失败')
  const json = await res.json()
  return json.data || { total: 0, items: [] }
}

/** 列出所有景点知识库文档 */
export async function listAttractionsKB(params?: { limit?: number; offset?: number }): Promise<{ total: number; items: any[] }> {
  const sp: Record<string, string> = {}
  if (params?.limit != null) sp.limit = String(params.limit)
  if (params?.offset != null) sp.offset = String(params.offset)
  const query = Object.keys(sp).length ? '?' + new URLSearchParams(sp).toString() : ''
  const res = await fetch(`${API_BASE}/kb/attractions${query}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取景点列表失败')
  const json = await res.json()
  return json.data || { total: 0, items: [] }
}

/** 向量搜索景点 */
export async function searchAttractionsKB(params: { q: string; top_k?: number }): Promise<any[]> {
  const sp: Record<string, string> = { q: params.q }
  if (params.top_k != null) sp.top_k = String(params.top_k)
  const query = '?' + new URLSearchParams(sp).toString()
  const res = await fetch(`${API_BASE}/search/attractions${query}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('搜索景点失败')
  const json = await res.json()
  return json.data || []
}

/** 获取酒店知识库详情 */
export async function getHotelKB(docId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/kb/hotels/${docId}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取酒店详情失败')
  const json = await res.json()
  return json.data
}

/** 获取景点知识库详情 */
export async function getAttractionKB(docId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/kb/attractions/${docId}`, {
    headers: { ...getAuthHeader() }
  })
  if (!res.ok) throw new Error('获取景点详情失败')
  const json = await res.json()
  return json.data
}

/** 导入酒店到知识库 */
export async function importHotelKB(data: {
  tenant_id: string
  hotel_name: string
  region: string
  info_text: string
  price_table_text: string
  metadata?: Record<string, any>
}): Promise<any> {
  const res = await fetch(`${API_BASE}/import/hotels-kb`, {
    method: 'POST',
    headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('导入酒店知识库失败')
  return res.json()
}

/** 导入景点到知识库 */
export async function importAttractionKB(data: {
  tenant_id: string
  attraction_name: string
  region: string
  category: string
  info_text: string
  ticket_table_text: string
  metadata?: Record<string, any>
}): Promise<any> {
  const res = await fetch(`${API_BASE}/import/attractions-kb`, {
    method: 'POST',
    headers: { ...getAuthHeader(), 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  if (!res.ok) throw new Error('导入景点知识库失败')
  return res.json()
}
