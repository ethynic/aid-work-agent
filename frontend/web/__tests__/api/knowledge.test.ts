/**
 * 知识库 API 测试（moveDocuments）
 *
 * 验证点：
 * - POST /api/knowledge/documents/move，JSON body {doc_ids, source_type, sub_category}
 * - 认证头（含 X-Tenant-Id）
 * - 成功解析 moved/skipped；success:false 抛中文错误
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { moveDocuments, getDocumentDetail } from '@/api/knowledge'

const TENANT_ID = 'tenant_kb_test'
const TENANT_PATH = `/t/${TENANT_ID}/knowledge`

// 捕获最近一次请求的 method/url/headers/body
interface CapturedRequest {
  method: string
  url: string
  authorization: string | null
  tenantId: string | null
  contentType: string | null
  body: any
}

function captureRequests(): { captured: CapturedRequest[] } {
  const captured: CapturedRequest[] = []
  const record = (request: Request) => {
    captured.push({
      method: request.method,
      url: new URL(request.url).pathname,
      authorization: request.headers.get('Authorization'),
      tenantId: request.headers.get('X-Tenant-Id'),
      contentType: request.headers.get('Content-Type'),
      body: null,
    })
  }

  server.use(
    http.post('/api/knowledge/documents/move', async ({ request }) => {
      record(request)
      captured[captured.length - 1].body = await request.json()
      return HttpResponse.json({ success: true, moved: 2, skipped: 0 })
    })
  )
  return { captured }
}

describe('knowledge API - moveDocuments', () => {
  const originalPath = window.location.pathname

  beforeEach(() => {
    localStorage.clear()
    // 模拟租户前台路由：getAuthHeader() 依赖 pathname 解析 tenant_id 与 token key
    window.history.pushState({}, '', TENANT_PATH)
    localStorage.setItem(`saas_token_${TENANT_ID}`, 'test_kb_token')
  })

  afterEach(() => {
    window.history.pushState({}, '', originalPath)
  })

  it('POST /documents/move，带 Authorization、X-Tenant-Id、JSON body', async () => {
    const { captured } = captureRequests()
    const result = await moveDocuments([1, 2], 'policy', null)

    expect(captured).toHaveLength(1)
    expect(captured[0].method).toBe('POST')
    expect(captured[0].url).toBe('/api/knowledge/documents/move')
    expect(captured[0].authorization).toBe('Bearer test_kb_token')
    expect(captured[0].tenantId).toBe(TENANT_ID)
    expect(captured[0].contentType).toContain('application/json')
    expect(captured[0].body).toEqual({
      doc_ids: [1, 2],
      source_type: 'policy',
      sub_category: null,
    })

    expect(result.success).toBe(true)
    expect(result.moved).toBe(2)
    expect(result.skipped).toBe(0)
  })

  it('sub_category 传给子分类时透传，null 时后端语义为顶级', async () => {
    const { captured } = captureRequests()
    await moveDocuments([3], 'product', 'manual')

    expect(captured[0].body.sub_category).toBe('manual')
    expect(captured[0].body.source_type).toBe('product')
  })

  it('success:false 响应体按中文错误抛出', async () => {
    server.use(
      http.post('/api/knowledge/documents/move', () => {
        return HttpResponse.json({ success: false, error: '目标顶级分类不存在' })
      })
    )
    await expect(moveDocuments([1], 'bad_top', null)).rejects.toThrow('目标顶级分类不存在')
  })
})

describe('knowledge API - getDocumentDetail', () => {
  const originalPath = window.location.pathname

  beforeEach(() => {
    localStorage.clear()
    window.history.pushState({}, '', TENANT_PATH)
    localStorage.setItem(`saas_token_${TENANT_ID}`, 'test_kb_token')
  })

  afterEach(() => {
    window.history.pushState({}, '', originalPath)
  })

  const DETAIL_BODY = {
    success: true,
    document: {
      id: 7, title: '商品 A', source_type: 'hongtao', sub_category: null,
      file_type: '.md', file_path: null, file_size: 0, total_chunks: 1,
      created_at: '2026-09-01T12:00:00', summary: null, origin: 'hongtao_api',
      status: 'active', expires_at: null,
      metadata: { external_id: 'p1', raw_payload: { name: '瓷砖' } },
    },
  }

  it('GET /documents/{id}，带 Authorization 与 X-Tenant-Id，解析 metadata', async () => {
    let captured: { method: string; url: string; authorization: string | null; tenantId: string | null } | null = null
    server.use(
      http.get('/api/knowledge/documents/7', ({ request }) => {
        captured = {
          method: request.method,
          url: new URL(request.url).pathname,
          authorization: request.headers.get('Authorization'),
          tenantId: request.headers.get('X-Tenant-Id'),
        }
        return HttpResponse.json(DETAIL_BODY)
      })
    )

    const result = await getDocumentDetail(7)

    expect(captured!.method).toBe('GET')
    expect(captured!.url).toBe('/api/knowledge/documents/7')
    expect(captured!.authorization).toBe('Bearer test_kb_token')
    expect(captured!.tenantId).toBe(TENANT_ID)
    expect(result.success).toBe(true)
    expect(result.document.metadata).toEqual({ external_id: 'p1', raw_payload: { name: '瓷砖' } })
  })

  it('404 响应按中文错误抛出（跨租户/软删除统一「文档不存在」）', async () => {
    server.use(
      http.get('/api/knowledge/documents/404', () => {
        return HttpResponse.json({ detail: '文档不存在' }, { status: 404 })
      })
    )
    await expect(getDocumentDetail(404)).rejects.toThrow('获取文档详情失败')
  })
})
