import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrowserBinding } from '@/notifications/client'

vi.mock('@/composables/useCreditCheck', () => ({ useCreditCheck: () => ({ checkCreditBeforeAction: vi.fn(async () => ({ allowed: true })) }) }))
const bindingKey = 'task-notifications:binding:v1'
const pendingKey = 'task-notifications:pending:v1'
const initial: BrowserBinding = { target_id: 'target-alice', installation_ref: 'installation-alice', binding_version: 1,
  state: 'active', enabled_since: null, user_id: 'alice', tenant_id: 'tenant-one', key_id: 'key-one' }
let listeners: [string, EventListenerOrEventListenerObject][]
function deferred() {
  let resolve!: (value?: any) => void
  const promise = new Promise<any>(success => { resolve = success })
  return { promise, resolve }
}
function credentials(user: string) {
  localStorage.setItem('saas_admin_tenant-one', JSON.stringify({ user_id: user, username: user, role: 'user' }))
  localStorage.setItem('saas_token_tenant-one', 'fixture-token-' + user)
  localStorage.setItem('saas_tenant_tenant-one', JSON.stringify({ tenant_id: 'tenant-one' }))
}
beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks(); vi.useFakeTimers(); localStorage.clear(); listeners = []
  Object.defineProperty(window, 'agentDesktop', { configurable: true, value: undefined })
  window.history.replaceState({}, '', '/t/tenant-one/chat')
  credentials('alice'); localStorage.setItem(bindingKey, JSON.stringify(initial))
  localStorage.setItem('user_info', JSON.stringify({ user_id: 'alice' }))
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn(async () => 'granted') })
  vi.stubGlobal('MessageChannel', class { port1: any = { close() {} }; port2 = { peer: this.port1 } })
  const originalAdd = window.addEventListener.bind(window)
  vi.spyOn(window, 'addEventListener').mockImplementation((type: string, listener: any, options?: any) => {
    listeners.push([type, listener]); originalAdd(type, listener, options)
  })
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
    if (url.includes('/notifications/targets') && options.method === 'DELETE') throw new Error('fixture provider offline')
    if (url.includes('/notifications/targets')) return new Response(JSON.stringify({ targets: [initial] }), { status: 200 })
    if (url.endsWith('/auth/admin_logout') || url.endsWith('/auth/logout')) return new Response('{}', { status: 200 })
    throw new Error('Unexpected external request')
  }))
})
afterEach(() => {
  for (const [type, listener] of listeners) window.removeEventListener(type, listener)
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

async function install(stall: 'ready' | 'getSubscription' | 'unsubscribe') {
  const pending = deferred()
  const workerMessages: any[] = []
  const unsubscribe = vi.fn(async () => stall === 'unsubscribe' ? pending.promise : true)
  const subscription = { unsubscribe }
  const reg = { active: { postMessage: (message: any, ports: any[]) => { workerMessages.push(message); ports[0].peer.onmessage({ data: { ok: true } }) } },
    pushManager: { getSubscription: vi.fn(async () => stall === 'getSubscription' ? pending.promise : subscription) } }
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: {
    register: vi.fn(async () => reg), ready: stall === 'ready' ? pending.promise : Promise.resolve(reg),
  } })
  const client = await import('@/notifications/client')
  client.initializeWebNotifications()
  return { client, pending, reg, subscription, unsubscribe, workerMessages }
}

describe('independent installed notification hook with stalled browser cleanup', () => {
  it('queued Web Locks cancellation suppresses the old subject and its delayed callback cannot touch the new owner', async () => {
    const runtime = await install('getSubscription')
    runtime.reg.pushManager.getSubscription.mockResolvedValue(null)
    let signal!: AbortSignal
    let queuedCallback!: () => Promise<unknown>
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: vi.fn((_name: string, options: any, callback: () => Promise<unknown>) => {
      signal = options.signal; queuedCallback = callback
      return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('queued request aborted'))))
    }) } })
    const { adminLogout } = await import('@/api/saasTenant')
    let settled = false
    const logout = adminLogout().then(() => { settled = true })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(settled).toBe(true); await logout
    expect(signal.aborted).toBe(true)
    expect(runtime.client.readBinding()?.state).toBe('revoked')
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
    expect(runtime.workerMessages[runtime.workerMessages.length - 1]?.binding).toBeNull()
    const next = { ...initial, user_id: 'bob', target_id: 'target-bob', binding_version: 9 }
    credentials('bob'); localStorage.setItem(bindingKey, JSON.stringify(next))
    const lateOffset = runtime.workerMessages.length
    await expect(queuedCallback()).rejects.toThrow()
    expect(runtime.client.readBinding()).toEqual(next)
    expect(runtime.workerMessages.slice(lateOffset).some(message => message.binding === null)).toBe(false)
  })

  it.each(['shared', 'saas'] as const)('a delayed %s HTTP logout does not delete replacement account credentials', async entry => {
    const runtime = await install('getSubscription')
    runtime.reg.pushManager.getSubscription.mockResolvedValue(null)
    const pending = deferred()
    const fetchBefore = vi.mocked(fetch).getMockImplementation()!
    vi.mocked(fetch).mockImplementation((url: any, options?: any) => String(url).endsWith('/auth/logout') || String(url).endsWith('/auth/admin_logout')
      ? pending.promise : fetchBefore(url, options))
    const auth = entry === 'shared' ? await import('@/api/auth') : await import('@/api/saasTenant')
    const logout = entry === 'shared' ? (auth as typeof import('@/api/auth')).logout() : (auth as typeof import('@/api/saasTenant')).adminLogout()
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith(entry === 'shared' ? '/auth/logout' : '/auth/admin_logout'))).toBe(true))
    const { useTenantAuth } = await import('@/composables/useTenantAuth')
    const newLogin = useTenantAuth()
    await newLogin.setLogin('fixture-token-bob', { user_id: 'bob', username: 'Bob', phone: '', role: 'user' }, { tenant_id: 'tenant-one' } as any)
    localStorage.setItem('user_info', JSON.stringify({ user_id: 'bob' }))
    pending.resolve(new Response('{}', { status: 200 })); await logout
    expect(localStorage.getItem('saas_token_tenant-one')).toBe('fixture-token-bob')
    expect(JSON.parse(localStorage.getItem('saas_admin_tenant-one')!).user_id).toBe('bob')
    expect(localStorage.getItem('saas_tenant_tenant-one')).toBeTruthy()
    expect(JSON.parse(localStorage.getItem('user_info')!).user_id).toBe('bob')
    expect(newLogin.admin.value?.user_id).toBe('bob')
    expect(newLogin.saasToken.value).toBe('fixture-token-bob')
  })

  it.each(['shared', 'saas'] as const)('a delayed %s notification prepare does not delete replacement account credentials', async entry => {
    const runtime = await install('ready')
    const auth = entry === 'shared' ? await import('@/api/auth') : await import('@/api/saasTenant')
    const logout = entry === 'shared' ? (auth as typeof import('@/api/auth')).logout() : (auth as typeof import('@/api/saasTenant')).adminLogout()
    await vi.advanceTimersByTimeAsync(1)
    credentials('bob'); localStorage.setItem('user_info', JSON.stringify({ user_id: 'bob' }))
    await vi.advanceTimersByTimeAsync(60_000); await logout
    expect(localStorage.getItem('saas_token_tenant-one')).toBe('fixture-token-bob')
    expect(JSON.parse(localStorage.getItem('saas_admin_tenant-one')!).user_id).toBe('bob')
    expect(JSON.parse(localStorage.getItem('user_info')!).user_id).toBe('bob')
    expect(runtime.client.readBinding()?.state).toBe('revoked')
  })

  it('retains unresolved native unsubscribe metadata across another identity cleanup and prevents reuse', async () => {
    const runtime = await install('unsubscribe')
    const { prepareLogout } = await import('@/platform/logoutHooks')
    const first = prepareLogout()
    await vi.advanceTimersByTimeAsync(60_000)
    await (await first).complete(false)
    expect(JSON.parse(localStorage.getItem(pendingKey)!).subscription_cleanup_pending).toBe(true)
    // A repeated cleanup fails before native unsubscribe can be called again.
    runtime.reg.pushManager.getSubscription.mockImplementationOnce(() => new Promise(() => {}))
    const { prepareIdentityChange } = await import('@/platform/identityHooks')
    const second = prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-one' })
    await vi.advanceTimersByTimeAsync(60_000)
    await (await second).complete()
    expect(JSON.parse(localStorage.getItem(pendingKey)!).subscription_cleanup_pending).toBe(true)
    expect(runtime.client.readBinding()?.state).toBe('revoked')
    await expect(runtime.client.enableNotifications(runtime.client.readBinding())).rejects.toThrow('仍在清理旧通知')
    expect(vi.mocked(fetch).mock.calls.some(([_url, options]) => options?.method === 'POST' && String(_url).includes('/notifications/targets'))).toBe(false)
  })

  it('the shared auth logout also clears its credentials when Service Worker readiness stalls', async () => {
    const runtime = await install('ready')
    const auth = await import('@/api/auth')
    let settled = false
    const operation = auth.logout().then(() => { settled = true })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(settled).toBe(true)
    await operation
    expect(localStorage.getItem('saas_token_tenant-one')).toBeNull()
    expect(localStorage.getItem('user_info')).toBeNull()
    expect(runtime.client.readBinding()?.state).toBe('revoked')
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/auth/logout'))).toBe(true)
  })

  it.each(['ready', 'getSubscription', 'unsubscribe'] as const)('SaaS logout finishes and removes credentials despite stalled %s', async stall => {
    const runtime = await install(stall)
    const auth = await import('@/api/saasTenant')
    let settled = false
    const operation = auth.adminLogout().then(() => { settled = true })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(settled).toBe(true)
    await operation
    expect(localStorage.getItem('saas_token_tenant-one')).toBeNull()
    expect(localStorage.getItem('saas_admin_tenant-one')).toBeNull()
    expect(localStorage.getItem('saas_tenant_tenant-one')).toBeNull()
    expect(runtime.client.readBinding()?.state).toBe('revoked')
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith('/auth/admin_logout'))).toBe(true)
    // A delayed old browser API response must not suppress a newly established owner.
    const next = { ...initial, target_id: 'target-bob', user_id: 'bob', binding_version: 9 }
    credentials('bob'); localStorage.setItem(bindingKey, JSON.stringify(next))
    const lateOffset = runtime.workerMessages.length
    runtime.pending.resolve(stall === 'ready' ? runtime.reg : stall === 'getSubscription' ? runtime.subscription : true)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(runtime.client.readBinding()).toEqual(next)
    expect(runtime.workerMessages.slice(lateOffset).some(message => message.binding === null)).toBe(false)
    expect(localStorage.getItem('saas_token_tenant-one')).toBe('fixture-token-bob')
  })

  it.each(['ready', 'getSubscription', 'unsubscribe'] as const)('direct login can replace credentials despite stalled old %s cleanup', async stall => {
    const runtime = await install(stall)
    const { useTenantAuth } = await import('@/composables/useTenantAuth')
    let settled = false
    const operation = useTenantAuth().setLogin('fixture-token-bob', {
      user_id: 'bob', username: 'Bob', phone: '', role: 'user',
    }, { tenant_id: 'tenant-one' } as any).then(() => { settled = true })
    await vi.advanceTimersByTimeAsync(60_000)
    expect(settled).toBe(true)
    await operation
    expect(localStorage.getItem('saas_token_tenant-one')).toBe('fixture-token-bob')
    expect(JSON.parse(localStorage.getItem('saas_admin_tenant-one')!).user_id).toBe('bob')
    expect(runtime.client.readBinding()?.user_id).toBe('alice')
    expect(runtime.client.readBinding()?.state).toBe('revoked')
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
    runtime.pending.resolve(stall === 'ready' ? runtime.reg : stall === 'getSubscription' ? runtime.subscription : true)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(localStorage.getItem('saas_token_tenant-one')).toBe('fixture-token-bob')
    expect(runtime.client.readBinding()?.state).toBe('revoked')
  })
})
