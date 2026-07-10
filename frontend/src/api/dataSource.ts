/**
 * 数据源导入 API
 */
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/data-analysis`

// ===== Types =====

export interface Connector {
  id: string
  name: string
  db_type: string
  host: string
  port: number
  database_name: string
  username: string
  is_active: boolean
  imported_tables: string[]
  last_sync_at: string | null
  created_at: string
}

export interface ColumnInfo {
  name: string
  semantic_name: string
  data_type: string
  description: string
  enum_values?: string[]
  sample_values?: string[]
}

export interface SchemaInfo {
  table_name: string
  description: string
  source_info: string
  columns: ColumnInfo[]
}

export interface RelationItem {
  from_table: string
  from_column: string
  to_table: string
  to_column: string
  relation_type: string
  description?: string
}

export interface SchemaDocument {
  id: number
  title: string
  source_type: string
  metadata: Record<string, any> | null
  created_at: string
  summary?: string
  /** 源数据是否仍可用：available=可用 / missing=源已失效（孤儿元数据）/ unknown=未知 */
  source_status?: 'available' | 'missing' | 'unknown'
}

// ===== Connector APIs =====

export async function listConnectors(): Promise<Connector[]> {
  const response = await fetch(`${API_BASE}/connectors`, {
    headers: { ...getAuthHeader() }
  })
  if (!response.ok) {
    throw new Error(`获取连接器列表失败: ${response.status}`)
  }
  const result = await response.json()
  return result.connectors || result.data || result
}

export async function createConnector(data: {
  name: string
  db_type: string
  host: string
  port: number
  database_name: string
  username: string
  password: string
}): Promise<Connector> {
  const response = await fetch(`${API_BASE}/connectors`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(data)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || `创建连接器失败: ${response.status}`)
  }
  return response.json()
}

export async function updateConnector(
  id: string,
  data: {
    name?: string
    host?: string
    port?: number
    database_name?: string
    username?: string
    password?: string
  }
): Promise<Connector> {
  const response = await fetch(`${API_BASE}/connectors/${id}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(data)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || `更新连接器失败: ${response.status}`)
  }
  return response.json()
}

export async function deleteConnector(id: string): Promise<void> {
  const response = await fetch(`${API_BASE}/connectors/${id}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() }
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || `删除连接器失败: ${response.status}`)
  }
}

export async function testConnection(data: {
  db_type: string
  host: string
  port: number
  database_name: string
  username: string
  password: string
}): Promise<{ success: boolean; version_info?: string; error?: string }> {
  const response = await fetch(`${API_BASE}/connectors/test`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(data)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || `测试连接失败: ${response.status}`)
  }
  return response.json()
}

export async function testSavedConnector(id: string): Promise<{ success: boolean; version_info?: string; error?: string }> {
  const response = await fetch(`${API_BASE}/connectors/${id}/test`, {
    method: 'POST',
    headers: { ...getAuthHeader() }
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || `测试连接失败: ${response.status}`)
  }
  return response.json()
}

export async function listRemoteTables(connectorId: string): Promise<
  { table_name: string; row_count: number; columns: { name: string; type: string }[] }[]
> {
  const response = await fetch(`${API_BASE}/connectors/${connectorId}/tables`, {
    headers: { ...getAuthHeader() }
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || `获取远程表列表失败: ${response.status}`)
  }
  const result = await response.json()
  return result.tables || result.data || result
}

export async function importTables(
  connectorId: string,
  tables: string[]
): Promise<{ schemas: SchemaInfo[] }> {
  const response = await fetch(`${API_BASE}/connectors/${connectorId}/import`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({ tables })
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || `导入表失败: ${response.status}`)
  }
  return response.json()
}

// ===== Upload =====

export async function uploadExcel(file: File): Promise<{ schemas: SchemaInfo[] }> {
  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    headers: { ...getAuthHeader() },
    body: formData
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || '上传文件失败')
  }
  return response.json()
}

export interface UploadStreamCallbacks {
  onConnected?: (filename: string) => void
  onProgress?: (stage: string, message: string) => void
  onSheetProgress?: (current: number, total: number, sheetName: string) => void
  onSheetDone?: (current: number, total: number, sheetName: string, tableName: string) => void
  onComplete: (schemas: SchemaInfo[]) => void
  onError: (message: string) => void
}

/**
 * 流式上传 Excel/CSV，通过 SSE 接收解析与推断进度。
 * 返回 cancel 句柄，调用方可在中途取消。
 */
export function uploadExcelStream(
  file: File,
  callbacks: UploadStreamCallbacks
): { cancel: () => void } {
  const formData = new FormData()
  formData.append('file', file)

  const abortController = new AbortController()

  ;(async () => {
    let response: Response
    try {
      response = await fetch(`${API_BASE}/upload`, {
        method: 'POST',
        headers: { ...getAuthHeader() },
        body: formData,
        signal: abortController.signal,
      })
    } catch (e: any) {
      if (e?.name === 'AbortError') return
      callbacks.onError(e?.message || '上传失败')
      return
    }

    if (!response.ok) {
      let msg = `上传失败 (${response.status})`
      try {
        const err = await response.json()
        if (err?.error) msg = err.error
      } catch {}
      callbacks.onError(msg)
      return
    }

    if (!response.body) {
      callbacks.onError('浏览器不支持流式读取')
      return
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const frames = buffer.split('\n\n')
        buffer = frames.pop() || ''

        for (const frame of frames) {
          const line = frame.split('\n').find(l => l.startsWith('data: '))
          if (!line) continue
          const payload = line.slice('data: '.length).trim()
          if (!payload) continue

          let event: any
          try {
            event = JSON.parse(payload)
          } catch {
            continue
          }

          switch (event.type) {
            case 'connected':
              callbacks.onConnected?.(event.filename)
              break
            case 'progress':
              callbacks.onProgress?.(event.stage, event.message)
              break
            case 'sheet_progress':
              callbacks.onSheetProgress?.(event.current, event.total, event.sheet_name)
              break
            case 'sheet_done':
              callbacks.onSheetDone?.(event.current, event.total, event.sheet_name, event.table_name)
              break
            case 'complete':
              callbacks.onComplete(event.schemas || [])
              return
            case 'error':
              callbacks.onError(event.message || '文件解析失败')
              return
          }
        }
      }
      // 流提前关闭且未收到 complete
      callbacks.onError('连接中断，未收到完整结果')
    } catch (e: any) {
      if (e?.name === 'AbortError') return
      callbacks.onError(e?.message || '读取流失败')
    }
  })()

  return {
    cancel: () => {
      try { abortController.abort() } catch {}
    },
  }
}

// ===== Schema Management =====

export async function listSchemas(): Promise<SchemaDocument[]> {
  const response = await fetch(`${API_BASE}/schemas`, {
    headers: { ...getAuthHeader() }
  })
  if (!response.ok) {
    throw new Error(`获取数据表列表失败: ${response.status}`)
  }
  const result = await response.json()
  return result.schemas || result.data || result
}

export async function saveSchema(
  schema: SchemaInfo & { connector_id?: string }
): Promise<{ success: boolean; document_id: number }> {
  const response = await fetch(`${API_BASE}/schemas`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(schema)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || '保存数据表失败')
  }
  return response.json()
}

export async function updateSchema(
  docId: number,
  schema: SchemaInfo
): Promise<{ success: boolean }> {
  const response = await fetch(`${API_BASE}/schemas/${docId}`, {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(schema)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || '更新数据表失败')
  }
  return response.json()
}

export async function deleteSchema(docId: number): Promise<void> {
  const response = await fetch(`${API_BASE}/schemas/${docId}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() }
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || '删除数据表失败')
  }
}

// ===== Relations =====

export async function inferRelations(schemaDocIds: number[]): Promise<RelationItem[]> {
  const response = await fetch(`${API_BASE}/relations/infer`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({ schema_doc_ids: schemaDocIds })
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || '推断关联关系失败')
  }
  const result = await response.json()
  return result.relations || result.data || result
}

export async function batchSaveRelations(relations: RelationItem[]): Promise<void> {
  const response = await fetch(`${API_BASE}/relations/batch`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify({ relations })
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || '保存关联关系失败')
  }
}

export async function listRelations(): Promise<RelationItem[]> {
  const response = await fetch(`${API_BASE}/relations`, {
    headers: { ...getAuthHeader() }
  })
  if (!response.ok) {
    throw new Error(`获取关联关系失败: ${response.status}`)
  }
  const result = await response.json()
  return result.relations || result.data || result
}

export async function addRelation(relation: RelationItem): Promise<void> {
  const response = await fetch(`${API_BASE}/relations`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(relation)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || '添加关联关系失败')
  }
}

export async function deleteRelation(relation: {
  from_table: string
  from_column: string
  to_table: string
  to_column: string
}): Promise<void> {
  const response = await fetch(`${API_BASE}/relations`, {
    method: 'DELETE',
    headers: {
      'Content-Type': 'application/json',
      ...getAuthHeader()
    },
    body: JSON.stringify(relation)
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error(err.error || '删除关联关系失败')
  }
}
