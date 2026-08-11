/**
 * 本地工具设备管理 API 测试
 *
 * 验证点：
 * - 4 个端点的 URL / method / 认证头（含 X-Tenant-Id）
 * - 后端 FastAPI HTTPException 错误结构（{detail: {error}}）解析为中文错误抛出
 *
 * 为什么重要：租户前台所有 API 必须带 X-Tenant-Id 才能被 TenantContextMiddleware
 * 正确解析租户上下文，缺失会导致 400「缺少租户上下文」或跨租户串数据。
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import {
  listDevices,
  createPairingTicket,
  selectDevice,
  revokeDevice,
} from '@/api/localTools'

const TENANT_ID = 'tenant_lt_test'
const TENANT_PATH = `/t/${TENANT_ID}/local-tools`

// 捕获最近一次请求的 method/url/headers
interface CapturedRequest {
  method: string
  url: string
  authorization: string | null
  tenantId: string | null
}

function captureRequests(): { captured: CapturedRequest[] } {
  const captured: CapturedRequest[] = []
  const record = (request: Request) => {
    captured.push({
      method: request.method,
      url: new URL(request.url).pathname,
      authorization: request.headers.get('Authorization'),
      tenantId: request.headers.get('X-Tenant-Id'),
    })
  }

  server.use(
    http.get('/api/local-tools/devices', ({ request }) => {
      record(request)
      return HttpResponse.json({
        success: true,
        devices: [
          {
            device_id: 'dev-1',
            name: '我的电脑',
            platform: 'windows',
            runtime_version: '1.0.0',
            capabilities: { providers: ['boss-recruiting'] },
            selected: true,
            status: 'active',
            online: true,
            last_seen_at: '2026-08-11T10:00:00',
            created_at: '2026-08-10T09:00:00',
          },
        ],
      })
    }),
    http.post('/api/local-tools/pairing-tickets', ({ request }) => {
      record(request)
      return HttpResponse.json({
        success: true,
        code: 'ABCD2345',
        expires_at: '2026-08-11T10:05:00',
      })
    }),
    http.post('/api/local-tools/devices/:deviceId/select', ({ request }) => {
      record(request)
      return HttpResponse.json({ success: true, device_id: 'dev-1' })
    }),
    http.delete('/api/local-tools/devices/:deviceId', ({ request }) => {
      record(request)
      return HttpResponse.json({ success: true, device_id: 'dev-1' })
    }),
  )
  return { captured }
}

describe('localTools API', () => {
  const originalPath = window.location.pathname

  beforeEach(() => {
    localStorage.clear()
    // 模拟租户前台路由：getAuthHeader() 依赖 pathname 解析 tenant_id 与 token key
    window.history.pushState({}, '', TENANT_PATH)
    localStorage.setItem(`saas_token_${TENANT_ID}`, 'test_lt_token')
  })

  afterEach(() => {
    window.history.pushState({}, '', originalPath)
  })

  it('listDevices：GET /api/local-tools/devices，带 Authorization 与 X-Tenant-Id', async () => {
    const { captured } = captureRequests()
    const devices = await listDevices()

    expect(captured).toHaveLength(1)
    expect(captured[0].method).toBe('GET')
    expect(captured[0].url).toBe('/api/local-tools/devices')
    expect(captured[0].authorization).toBe('Bearer test_lt_token')
    expect(captured[0].tenantId).toBe(TENANT_ID)

    expect(devices).toHaveLength(1)
    expect(devices[0].device_id).toBe('dev-1')
    expect(devices[0].online).toBe(true)
    expect(devices[0].selected).toBe(true)
  })

  it('createPairingTicket：POST /pairing-tickets，返回 code 与 expires_at', async () => {
    const { captured } = captureRequests()
    const ticket = await createPairingTicket()

    expect(captured[0].method).toBe('POST')
    expect(captured[0].url).toBe('/api/local-tools/pairing-tickets')
    expect(captured[0].authorization).toBe('Bearer test_lt_token')
    expect(captured[0].tenantId).toBe(TENANT_ID)

    expect(ticket.code).toBe('ABCD2345')
    expect(ticket.expires_at).toBe('2026-08-11T10:05:00')
  })

  it('selectDevice：POST /devices/{id}/select，device_id 进 URL 路径', async () => {
    const { captured } = captureRequests()
    await selectDevice('dev-1')

    expect(captured[0].method).toBe('POST')
    expect(captured[0].url).toBe('/api/local-tools/devices/dev-1/select')
    expect(captured[0].tenantId).toBe(TENANT_ID)
  })

  it('revokeDevice：DELETE /devices/{id}', async () => {
    const { captured } = captureRequests()
    await revokeDevice('dev-1')

    expect(captured[0].method).toBe('DELETE')
    expect(captured[0].url).toBe('/api/local-tools/devices/dev-1')
    expect(captured[0].tenantId).toBe(TENANT_ID)
  })

  it('device_id 含特殊字符时做 URL 编码', async () => {
    const { captured } = captureRequests()
    await selectDevice('dev/with slash')

    expect(captured[0].url).toBe('/api/local-tools/devices/dev%2Fwith%20slash/select')
  })

  it('HTTP 错误：解析 FastAPI detail.error 为中文错误抛出', async () => {
    server.use(
      http.get('/api/local-tools/devices', () => {
        return HttpResponse.json(
          { detail: { error: '缺少租户上下文（tenant_id）', debug: 'xxx' } },
          { status: 400 }
        )
      })
    )
    await expect(listDevices()).rejects.toThrow('缺少租户上下文（tenant_id）')
  })

  it('HTTP 错误：detail 为字符串时也能解析', async () => {
    server.use(
      http.post('/api/local-tools/pairing-tickets', () => {
        return HttpResponse.json({ detail: '未登录或登录已过期' }, { status: 401 })
      })
    )
    await expect(createPairingTicket()).rejects.toThrow('未登录或登录已过期')
  })

  it('success:false 响应体按错误抛出', async () => {
    server.use(
      http.post('/api/local-tools/devices/:deviceId/select', () => {
        return HttpResponse.json({ success: false, error: '选定设备失败，请稍后重试' })
      })
    )
    await expect(selectDevice('dev-1')).rejects.toThrow('选定设备失败，请稍后重试')
  })

  it('非 JSON 错误响应使用兜底中文文案', async () => {
    server.use(
      http.delete('/api/local-tools/devices/:deviceId', () => {
        return new HttpResponse('Internal Server Error', { status: 500 })
      })
    )
    await expect(revokeDevice('dev-1')).rejects.toThrow('解绑设备失败，请稍后重试')
  })
})
