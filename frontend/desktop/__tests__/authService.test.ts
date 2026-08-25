import { describe, expect, it, vi } from 'vitest'
import { createDesktopAuthService, DESKTOP_AUTH_KEY } from '@desktop/services/auth'
import type { ApiTransport } from '@shared/platform/contracts'

describe('Desktop auth service', () => {
  it('persists only the shared session contract through CredentialStore', async () => {
    const store = { hydrate: vi.fn(async () => ({})), set: vi.fn(async () => undefined), delete: vi.fn(async () => undefined) }
    const transport: ApiTransport = { request: vi.fn(async () => ({ success: true, token: 'token', tenant_id: 'tenant', user: { user_id: 'user', username: 'Ada' } })) as ApiTransport['request'] }
    const service = createDesktopAuthService(store, { resolve: (path) => `https://api.example${path}` }, transport)
    const session = await service.login({ tenant_code: 'ACME', identifier: 'ada', password: 'secret', captcha_code: 'ABCD', captcha_id: 'captcha' })
    expect(session).toMatchObject({ tenantCode: 'ACME', tenantId: 'tenant' })
    expect(store.set).toHaveBeenCalledWith(DESKTOP_AUTH_KEY, JSON.stringify(session))
  })

  it('restores and clears encrypted desktop auth state', async () => {
    const session = { token: 'token', tenantId: 'tenant', tenantCode: 'ACME', user: { user_id: 'user', username: 'Ada' } }
    const store = { hydrate: vi.fn(async () => ({ [DESKTOP_AUTH_KEY]: JSON.stringify(session) })), set: vi.fn(), delete: vi.fn(async () => undefined) }
    const service = createDesktopAuthService(store, { resolve: (path) => path }, { request: vi.fn() as ApiTransport['request'] })
    await expect(service.restore()).resolves.toEqual(session)
    await service.logout()
    expect(store.delete).toHaveBeenCalledWith(DESKTOP_AUTH_KEY)
  })

  it('preserves server field errors for the accessible login form', async () => {
    const store = { hydrate: vi.fn(async () => ({})), set: vi.fn(), delete: vi.fn() }
    const transport: ApiTransport = { request: vi.fn(async () => ({ success: false, errors: [{ field: 'captcha_code', message: '验证码错误' }] })) as ApiTransport['request'] }
    const service = createDesktopAuthService(store, { resolve: (path) => path }, transport)
    await expect(service.login({ tenant_code: 'ACME', identifier: 'ada', password: 'x', captcha_code: 'x', captcha_id: 'x' }))
      .rejects.toMatchObject({ message: '验证码错误', fieldErrors: [{ field: 'captcha_code', message: '验证码错误' }] })
    expect(store.set).not.toHaveBeenCalled()
  })

  it('rejects malformed restored and server sessions before they reach renderer state', async () => {
    const store = { hydrate: vi.fn(async () => ({ [DESKTOP_AUTH_KEY]: JSON.stringify({ token: {}, tenantId: 'tenant', tenantCode: 'ACME', user: { user_id: 'user', username: 'Ada' } }) })), set: vi.fn(), delete: vi.fn() }
    const transport: ApiTransport = { request: vi.fn(async () => ({ success: true, token: 'token', tenant_id: 'tenant', user: { user_id: 'user' } })) as ApiTransport['request'] }
    const service = createDesktopAuthService(store, { resolve: (path) => path }, transport)
    await expect(service.restore()).resolves.toBeNull()
    await expect(service.login({ tenant_code: 'ACME', identifier: 'ada', password: 'x', captcha_code: 'x', captcha_id: 'x' }))
      .rejects.toThrow('登录响应格式无效')
    expect(store.set).not.toHaveBeenCalled()
  })
})
