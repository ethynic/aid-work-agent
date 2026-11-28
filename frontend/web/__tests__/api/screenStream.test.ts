/**
 * 实时画面 API 测试
 *
 * 验证点：
 * - 4 个端点的 URL / method / 认证头（含 X-Tenant-Id）
 * - 后端错误结构 {detail: {error, code, debug}} 解析为带 code 的 ScreenStreamApiError，
 *   组合函数靠 code 区分重试（SCREEN_STREAM_BUSY）与结束（SCREEN_SESSION_ENDED / NOT_FOUND）
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import {
  createScreenSession,
  pollScreenSession,
  sendScreenSignal,
  endScreenSession,
  ScreenStreamApiError,
} from '@/api/screenStream'

const TENANT_ID = 'tenant_screen_test'
const TENANT_PATH = `/t/${TENANT_ID}/local-tools`

function captureRequests(): { captured: Array<{ method: string; url: string; authorization: string | null; tenantId: string | null; body: unknown }> } {
  const captured: Array<{ method: string; url: string; authorization: string | null; tenantId: string | null; body: unknown }> = []
  const record = async (request: Request) => {
    let body: unknown = null
    try { body = await request.clone().json() } catch { /* 无请求体 */ }
    captured.push({
      method: request.method,
      url: new URL(request.url).pathname + new URL(request.url).search,
      authorization: request.headers.get('Authorization'),
      tenantId: request.headers.get('X-Tenant-Id'),
      body,
    })
  }
  server.use(
    http.post('/api/local-tools/devices/:deviceId/screen-sessions', async ({ request }) => {
      await record(request)
      return HttpResponse.json({ success: true, session: { id: 'sess-1', state: 'pending', stun_urls: ['stun:stun.example.com:3478'] } })
    }),
    http.get('/api/local-tools/devices/:deviceId/screen-sessions/:sessionId', async ({ request }) => {
      await record(request)
      return HttpResponse.json({ success: true, session: { id: 'sess-1', state: 'prepared', end_reason: null }, signals: [] })
    }),
    http.post('/api/local-tools/devices/:deviceId/screen-sessions/:sessionId/signals', async ({ request }) => {
      await record(request)
      return HttpResponse.json({ success: true, seq: 3 })
    }),
    http.post('/api/local-tools/devices/:deviceId/screen-sessions/:sessionId/end', async ({ request }) => {
      await record(request)
      return HttpResponse.json({ success: true, session: { id: 'sess-1', state: 'ended', end_reason: 'viewer_closed' } })
    }),
  )
  return { captured }
}

describe('screenStream API', () => {
  const originalPath = window.location.pathname

  beforeEach(() => {
    localStorage.clear()
    window.history.pushState({}, '', TENANT_PATH)
    localStorage.setItem(`saas_token_${TENANT_ID}`, 'test_screen_token')
  })

  afterEach(() => {
    window.history.pushState({}, '', originalPath)
  })

  it('createScreenSession：POST screen-sessions，带认证头与租户头，返回会话与 STUN 列表', async () => {
    const { captured } = captureRequests()
    const created = await createScreenSession('dev-1')
    expect(created).toEqual({ sessionId: 'sess-1', state: 'pending', stunUrls: ['stun:stun.example.com:3478'] })
    expect(captured[0]).toMatchObject({ method: 'POST', url: '/api/local-tools/devices/dev-1/screen-sessions', authorization: 'Bearer test_screen_token', tenantId: TENANT_ID })
  })

  it('pollScreenSession：GET 带 after_seq 查询参数，返回会话与信令', async () => {
    const { captured } = captureRequests()
    const result = await pollScreenSession('dev-1', 'sess-1', 5)
    expect(result.session).toEqual({ id: 'sess-1', state: 'prepared', end_reason: null })
    expect(result.signals).toEqual([])
    expect(captured[0]).toMatchObject({ method: 'GET', url: '/api/local-tools/devices/dev-1/screen-sessions/sess-1?after_seq=5', tenantId: TENANT_ID })
  })

  it('sendScreenSignal：POST signals，body 为 {type, payload}', async () => {
    const { captured } = captureRequests()
    const seq = await sendScreenSignal('dev-1', 'sess-1', 'answer', { sdp: 'answer-sdp' })
    expect(seq).toBe(3)
    expect(captured[0]).toMatchObject({ method: 'POST', url: '/api/local-tools/devices/dev-1/screen-sessions/sess-1/signals', tenantId: TENANT_ID })
    expect(captured[0].body).toEqual({ type: 'answer', payload: { sdp: 'answer-sdp' } })
  })

  it('endScreenSession：POST end 幂等结束', async () => {
    const { captured } = captureRequests()
    await expect(endScreenSession('dev-1', 'sess-1')).resolves.toBeUndefined()
    expect(captured[0]).toMatchObject({ method: 'POST', url: '/api/local-tools/devices/dev-1/screen-sessions/sess-1/end', tenantId: TENANT_ID })
  })

  it('后端 {detail:{error, code}} 错误解析为带 code 的 ScreenStreamApiError', async () => {
    server.use(
      http.post('/api/local-tools/devices/:deviceId/screen-sessions', () => {
        return HttpResponse.json({ detail: { error: 'Runtime 电脑不在线', code: 'DEVICE_OFFLINE', debug: '' } }, { status: 409 })
      }),
    )
    const error = await createScreenSession('dev-1').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ScreenStreamApiError)
    expect((error as ScreenStreamApiError).code).toBe('DEVICE_OFFLINE')
    expect((error as ScreenStreamApiError).message).toBe('Runtime 电脑不在线')
  })

  it('SCREEN_STREAM_BUSY（503）同样保留 code，供组合函数按可重试处理', async () => {
    server.use(
      http.get('/api/local-tools/devices/:deviceId/screen-sessions/:sessionId', () => {
        return HttpResponse.json({ detail: { error: '实时画面暂时繁忙，请稍后重试', code: 'SCREEN_STREAM_BUSY', debug: '' } }, { status: 503 })
      }),
    )
    const error = await pollScreenSession('dev-1', 'sess-1', 0).catch((e: unknown) => e)
    expect((error as ScreenStreamApiError).code).toBe('SCREEN_STREAM_BUSY')
  })

  it('非 JSON 错误响应使用兜底文案，code 为 SCREEN_STREAM_ERROR', async () => {
    server.use(
      http.post('/api/local-tools/devices/:deviceId/screen-sessions', () => {
        return new HttpResponse('gateway error', { status: 502 })
      }),
    )
    const error = await createScreenSession('dev-1').catch((e: unknown) => e)
    expect((error as ScreenStreamApiError).code).toBe('SCREEN_STREAM_ERROR')
    expect((error as ScreenStreamApiError).message).toBe('创建画面会话失败，请稍后重试')
  })

  it('stun_urls 缺失或非字符串条目时按空列表处理', async () => {
    server.use(
      http.post('/api/local-tools/devices/:deviceId/screen-sessions', () => {
        return HttpResponse.json({ success: true, session: { id: 'sess-1', state: 'pending', stun_urls: ['stun:a:1', 42, null] } })
      }),
    )
    const created = await createScreenSession('dev-1')
    expect(created.stunUrls).toEqual(['stun:a:1'])
  })
})
