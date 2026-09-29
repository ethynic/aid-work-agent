/**
 * clientActivation API 测试
 *
 * 覆盖场景：
 * - createStaticBinding：POST /api/saas/client-bindings/static，成功返回含明文 access_token
 * - createStaticBinding：403（非 platform_admin）时抛出后端 detail（友好文案透出）
 * - createStaticBinding：异常响应体不可解析时使用 fallback 文案
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createStaticBinding } from '@/api/clientActivation'

describe('clientActivation API', () => {
  let originalFetch: typeof globalThis.fetch

  beforeEach(() => {
    originalFetch = globalThis.fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('createStaticBinding 应 POST /api/saas/client-bindings/static 并返回明文 token', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        binding_id: 'bnd_test',
        access_token: 'tok_plain_once',
        tenant_id: 'tenant_a',
        client_name: 'wecom-cli@PC',
        token_type: 'static',
        created_at: '2026-09-29T10:00:00',
      }),
    } as any)
    globalThis.fetch = fetchMock

    const created = await createStaticBinding({ tenant_id: 'tenant_a', client_name: 'wecom-cli@PC' })

    expect(created.access_token).toBe('tok_plain_once')
    expect(created.binding_id).toBe('bnd_test')
    expect(created.token_type).toBe('static')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/api/saas/client-bindings/static')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ tenant_id: 'tenant_a', client_name: 'wecom-cli@PC' })
  })

  it('createStaticBinding 403 时应抛出后端 detail（仅平台管理员可签发静态绑定）', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ detail: '仅平台管理员可签发静态绑定' }),
    } as any)

    await expect(
      createStaticBinding({ tenant_id: 'tenant_a', client_name: 'wecom-cli@PC' }),
    ).rejects.toThrow('仅平台管理员可签发静态绑定')
  })

  it('createStaticBinding 响应体不可解析时应使用 fallback 文案', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error('not json')
      },
    } as any)

    await expect(
      createStaticBinding({ tenant_id: 'tenant_a', client_name: 'wecom-cli@PC' }),
    ).rejects.toThrow('签发直连 Token 失败')
  })
})
