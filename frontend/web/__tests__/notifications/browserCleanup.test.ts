import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrowserBinding } from '@/notifications/client'

const bindingKey = 'task-notifications:binding:v1'
const pendingKey = 'task-notifications:pending:v1'
const initial: BrowserBinding = { target_id: 'target-alice', installation_ref: 'installation-alice', binding_version: 1,
  state: 'active', enabled_since: null, user_id: 'alice', tenant_id: 'tenant-one', key_id: 'key-one' }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(accept => { resolve = accept })
  return { promise, resolve }
}
let listeners: Array<[string, EventListenerOrEventListenerObject]>
function login() {
  localStorage.setItem('saas_admin_tenant-one', JSON.stringify({ user_id: 'alice' }))
  localStorage.setItem('saas_token_tenant-one', 'fixture-token')
}
beforeEach(() => {
  vi.resetModules(); vi.useFakeTimers(); localStorage.clear(); listeners = []
  window.history.replaceState({}, '', '/t/tenant-one/notifications')
  login(); localStorage.setItem(bindingKey, JSON.stringify(initial))
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  Object.defineProperty(window, 'agentDesktop', { configurable: true, value: undefined })
  Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('Notification', { permission: 'granted' })
  vi.stubGlobal('MessageChannel', class { port1: any = { close() {} }; port2 = { peer: this.port1 } })
  const add = window.addEventListener.bind(window)
  vi.spyOn(window, 'addEventListener').mockImplementation((type: string, listener: any, options?: any) => {
    listeners.push([type, listener]); add(type, listener, options)
  })
})
afterEach(() => {
  for (const [type, listener] of listeners) window.removeEventListener(type, listener)
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})
function installWorker(pushManager: any) {
  const registration = { active: { postMessage: (_value: any, ports: any[]) => ports[0].peer.onmessage({ data: { ok: true } }) }, pushManager }
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { register: async () => registration, ready: Promise.resolve(registration) } })
}

describe('bounded browser cleanup and shared pending state', () => {
  it('aborts a queued Web Lock and prevents its late callback from touching another binding', async () => {
    installWorker({ getSubscription: async () => null })
    let callback!: () => Promise<unknown>
    let signal!: AbortSignal
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
      request: vi.fn((_name: string, options: any, fn: () => Promise<unknown>) => {
        callback = fn; signal = options.signal
        return new Promise((_accept, reject) => signal.addEventListener('abort', () => reject(new Error('queue aborted'))))
      }),
    } })
    const client = await import('@/notifications/client')
    client.initializeWebNotifications()
    const { prepareLogout } = await import('@/platform/logoutHooks')
    const operation = prepareLogout()
    await vi.advanceTimersByTimeAsync(5001)
    const prepared = await operation
    expect(signal.aborted).toBe(true)
    expect(client.readBinding()?.state).toBe('revoked')
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
    const newer = { ...initial, user_id: 'bob', target_id: 'target-bob', binding_version: 9 }
    localStorage.setItem(bindingKey, JSON.stringify(newer))
    await expect(callback()).rejects.toThrow('等待超时')
    await prepared.complete(true)
    expect(client.readBinding()).toEqual(newer)
    expect(localStorage.getItem('task-notifications:identity-transition:v1')).toBeNull()
  })

  it('blocks another module from reusing a subscription until native unsubscribe has actually removed it', async () => {
    const cleanup = deferred<boolean>()
    const unsubscribe = vi.fn(() => cleanup.promise.then(result => { subscription = null; return result }))
    let subscription: any = { unsubscribe }
    const subscribe = vi.fn(async () => (subscription = { toJSON: () => ({ endpoint: 'https://push.example.test/new', keys: { p256dh: 'public', auth: 'fake' } }), unsubscribe: async () => true }))
    installWorker({ getSubscription: async () => subscription, subscribe })
    let serverTarget: BrowserBinding = { ...initial, state: 'revoked', binding_version: 2 }
    const save = vi.fn()
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith('/notifications/capabilities')) return new Response(JSON.stringify({ enabled: true, web_push: { enabled: true, public_key: 'AA', key_id: 'key-one' } }))
      if (url.endsWith('/notifications/targets') && options.method === 'POST') {
        save(); serverTarget = { ...initial, binding_version: 3 }; return new Response(JSON.stringify(serverTarget))
      }
      if (url.endsWith('/notifications/targets')) return new Response(JSON.stringify({ targets: [serverTarget] }))
      if (url.endsWith('/notifications/preferences')) return new Response('{}')
      if (url.endsWith('/auth/admin_logout')) return new Response('{}')
      throw new Error('Unexpected external request')
    }))
    const first = await import('@/notifications/client')
    first.initializeWebNotifications()
    const { adminLogout } = await import('@/api/saasTenant')
    const logout = adminLogout()
    await vi.advanceTimersByTimeAsync(5001)
    await logout
    expect(localStorage.getItem('saas_token_tenant-one')).toBeNull()
    expect(JSON.parse(localStorage.getItem(pendingKey)!).subscription_cleanup_pending).toBe(true)
    login()
    const { prepareIdentityChange } = await import('@/platform/identityHooks')
    const retry = await prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-one' })
    await retry.complete()
    expect(JSON.parse(localStorage.getItem(pendingKey)!).subscription_cleanup_pending).toBe(true)
    expect(unsubscribe).toHaveBeenCalledOnce()
    // Fresh module models a tab with no access to the old Promise or memory flags.
    vi.resetModules(); login()
    const second = await import('@/notifications/client')
    await expect(second.enableNotifications(second.readBinding())).rejects.toThrow('仍在清理旧通知')
    expect(save).not.toHaveBeenCalled(); expect(subscribe).not.toHaveBeenCalled()
    cleanup.resolve(true); await vi.advanceTimersByTimeAsync(1)
    const enabled = await second.enableNotifications(second.readBinding())
    expect(enabled.binding_version).toBe(3)
    expect(subscribe).toHaveBeenCalledOnce(); expect(save).toHaveBeenCalledOnce()
    expect(second.readBinding()?.state).toBe('active')
    expect(localStorage.getItem(pendingKey)).toBeNull()
  })
})
