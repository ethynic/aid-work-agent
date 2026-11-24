/**
 * 连接邮箱 API 测试
 *
 * 验证点：
 * - GET /api/email-settings：带 Authorization 与 X-Tenant-Id，返回 data/bound 结构
 * - POST /api/email-settings：方法与请求体（9 项配置），失败响应里的 error_code 能读到
 * - DELETE /api/email-settings：方法与鉴权头
 *
 * 为什么重要：租户前台所有 API 必须带 X-Tenant-Id 才能被 TenantContextMiddleware
 * 正确解析租户上下文，缺失会导致 400「缺少租户上下文」或跨租户串数据。
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { getEmailSettings, saveEmailSettings, deleteEmailSettings } from '@/api/connectMailbox'
import type { EmailSettingsConfig } from '@/api/connectMailbox'

const TENANT_ID = 'tenant_mb_test'
const TENANT_PATH = `/t/${TENANT_ID}/connections/email`

function makeConfig(): EmailSettingsConfig {
  return {
    email_address: '987654321@qq.com',
    smtp_server: 'smtp.qq.com',
    smtp_port: 465,
    smtp_user: '987654321@qq.com',
    smtp_password: 'authcode',
    smtp_encryption: 'ssl',
    imap_server: 'imap.qq.com',
    imap_port: 993,
    imap_encryption: 'ssl',
  }
}

describe('connectMailbox API', () => {
  const originalPath = window.location.pathname

  beforeEach(() => {
    localStorage.clear()
    // 模拟租户前台路由：getAuthHeader() 依赖 pathname 解析 tenant_id 与 token key
    window.history.pushState({}, '', TENANT_PATH)
    localStorage.setItem(`saas_token_${TENANT_ID}`, 'test_mb_token')
  })

  afterEach(() => {
    window.history.pushState({}, '', originalPath)
  })

  it('getEmailSettings：GET /api/email-settings，带 Authorization 与 X-Tenant-Id', async () => {
    let captured: Request | undefined
    server.use(http.get('/api/email-settings', ({ request }) => {
      captured = request
      return HttpResponse.json({
        success: true,
        data: { ...makeConfig(), smtp_password: '****', updated_at: '2026-11-24T10:00:00' },
        bound: true,
      })
    }))
    const res = await getEmailSettings()

    expect(captured!.method).toBe('GET')
    expect(captured!.headers.get('Authorization')).toBe('Bearer test_mb_token')
    expect(captured!.headers.get('X-Tenant-Id')).toBe(TENANT_ID)
    expect(res.success).toBe(true)
    expect(res.bound).toBe(true)
    expect(res.data?.smtp_password).toBe('****')
    expect(res.data?.updated_at).toBe('2026-11-24T10:00:00')
  })

  it('getEmailSettings：未配置时 data 为 null、bound 为 false', async () => {
    server.use(http.get('/api/email-settings', () =>
      HttpResponse.json({ success: true, data: null, bound: false })))
    const res = await getEmailSettings()
    expect(res.data).toBeNull()
    expect(res.bound).toBe(false)
  })

  it('saveEmailSettings：POST /api/email-settings，请求体为 9 项配置', async () => {
    let captured: Request | undefined
    let body: unknown
    server.use(http.post('/api/email-settings', async ({ request }) => {
      captured = request
      body = await request.json()
      return HttpResponse.json({ success: true, message: '邮箱绑定成功，测试邮件已发送' })
    }))
    const config = makeConfig()
    const res = await saveEmailSettings(config)

    expect(captured!.method).toBe('POST')
    expect(captured!.headers.get('Authorization')).toBe('Bearer test_mb_token')
    expect(captured!.headers.get('X-Tenant-Id')).toBe(TENANT_ID)
    expect(body).toEqual(config)
    expect(res.success).toBe(true)
    expect(res.message).toBe('邮箱绑定成功，测试邮件已发送')
  })

  it('saveEmailSettings：失败响应里的 error_code 能读到', async () => {
    server.use(http.post('/api/email-settings', () =>
      HttpResponse.json({ success: false, error: '授权码不正确，或未开启 IMAP/SMTP 服务', error_code: 'auth' })))
    const res = await saveEmailSettings(makeConfig())
    expect(res.success).toBe(false)
    expect(res.error_code).toBe('auth')
    expect(res.error).toContain('授权码')
  })

  it('deleteEmailSettings：DELETE /api/email-settings，带鉴权头', async () => {
    let captured: Request | undefined
    server.use(http.delete('/api/email-settings', ({ request }) => {
      captured = request
      return HttpResponse.json({ success: true, message: '已解除绑定' })
    }))
    const res = await deleteEmailSettings()

    expect(captured!.method).toBe('DELETE')
    expect(captured!.headers.get('Authorization')).toBe('Bearer test_mb_token')
    expect(captured!.headers.get('X-Tenant-Id')).toBe(TENANT_ID)
    expect(res.success).toBe(true)
  })
})
