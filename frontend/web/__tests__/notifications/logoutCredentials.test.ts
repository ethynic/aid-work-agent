import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
vi.mock('@/composables/useCreditCheck', () => ({ useCreditCheck: () => ({ checkCreditBeforeAction: vi.fn(async () => true) }) }))
beforeEach(() => {
  vi.resetModules(); localStorage.clear()
  Object.defineProperty(window, 'agentDesktop', { configurable: true, value: undefined })
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
  Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
  window.history.replaceState({}, '', '/t/tenant-one/chat')
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('late real logout preserves a completed replacement login', () => {
  it.each(['auth', 'saas', 'tenant-auth'] as const)('keeps Bob credentials and reactive auth after late %s logout', async entry => {
    const { initializeWebNotifications } = await import('@/notifications/client')
    initializeWebNotifications()
    const { useTenantAuth } = await import('@/composables/useTenantAuth')
    const auth = useTenantAuth()
    const tenant = { tenant_id: 'tenant-one', company_name: 'Fixture' } as any
    await auth.setLogin('fixture-alice-token', { user_id: 'alice', username: 'Alice', phone: '', role: 'user' }, tenant)
    localStorage.setItem('user_info', JSON.stringify({ user_id: 'alice' }))
    let finish!: (value: Response) => void
    const request = new Promise<Response>(resolve => { finish = resolve })
    const fetch = vi.fn(() => request)
    vi.stubGlobal('fetch', fetch)
    const operation = entry === 'auth' ? (await import('@/api/auth')).logout() :
      entry === 'saas' ? (await import('@/api/saasTenant')).adminLogout() : auth.logout()
    await flushPromises()
    expect(fetch).toHaveBeenCalledOnce()
    // Actual setLogin replaces all credentials while the old HTTP response is pending.
    await auth.setLogin('fixture-bob-token', { user_id: 'bob', username: 'Bob', phone: '', role: 'user' }, { ...tenant, company_name: 'Bob fixture' })
    localStorage.setItem('user_info', JSON.stringify({ user_id: 'bob' }))
    finish(new Response('{}', { status: 200 })); await operation
    expect(localStorage.getItem('saas_token_tenant-one')).toBe('fixture-bob-token')
    expect(JSON.parse(localStorage.getItem('saas_admin_tenant-one')!).user_id).toBe('bob')
    expect(JSON.parse(localStorage.getItem('saas_tenant_tenant-one')!).company_name).toBe('Bob fixture')
    expect(JSON.parse(localStorage.getItem('user_info')!).user_id).toBe('bob')
    expect(auth.isLoggedIn.value).toBe(true)
    expect(auth.admin.value?.user_id).toBe('bob')
  })
})
