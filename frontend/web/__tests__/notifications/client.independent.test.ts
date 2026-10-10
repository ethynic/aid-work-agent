import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrowserBinding } from '@/notifications/client'

const api = vi.hoisted(() => ({ capabilities: vi.fn(), targets: vi.fn(), saveTarget: vi.fn(), enablePreference: vi.fn(), revokeTarget: vi.fn() }))
vi.mock('@/notifications/api', () => ({ notificationApi: api, NotificationApiError: class extends Error { constructor(public status: number) { super('fixture API error') } } }))

const bindingKey = 'task-notifications:binding:v1'
const pendingKey = 'task-notifications:pending:v1'
const installKey = 'task-notifications:installation:v1'
const transitionKey = 'task-notifications:identity-transition:v1'
const initial: BrowserBinding = { target_id: 'target-alice', installation_ref: 'installation-alice', binding_version: 1,
  state: 'active', enabled_since: null, user_id: 'alice', tenant_id: 'tenant-one', key_id: 'key-one' }
let server: BrowserBinding
let activeSubscription: any
let unsubscribe: ReturnType<typeof vi.fn>
let subscribe: ReturnType<typeof vi.fn>
let workerMessages: any[]
let registeredListeners: [string, EventListenerOrEventListenerObject][]

function credentials(user: string) {
  localStorage.setItem('saas_admin_tenant-one', JSON.stringify({ user_id: user }))
  localStorage.setItem('saas_token_tenant-one', 'fixture-token-' + user)
}
function saveLocal(value: BrowserBinding) { localStorage.setItem(bindingKey, JSON.stringify(value)) }
function serverView() { return { ...server } }
async function runtime() {
  const client = await import('@/notifications/client')
  client.initializeWebNotifications()
  return { ...client, ...await import('@/platform/logoutHooks'), ...await import('@/platform/identityHooks') }
}
beforeEach(() => {
  vi.resetModules(); vi.clearAllMocks(); localStorage.clear()
  Object.defineProperty(window, 'agentDesktop', { configurable: true, value: undefined })
  window.history.replaceState({}, '', '/t/tenant-one/notifications')
  credentials('alice'); server = { ...initial }; saveLocal(initial)
  localStorage.setItem(installKey, initial.installation_ref)
  workerMessages = []; registeredListeners = []
  const originalAdd = window.addEventListener.bind(window)
  vi.spyOn(window, 'addEventListener').mockImplementation((type: string, listener: any, options?: any) => {
    registeredListeners.push([type, listener]); originalAdd(type, listener, options)
  })
  unsubscribe = vi.fn(async () => { activeSubscription = null; return true })
  const subscription = { unsubscribe, toJSON: () => ({ endpoint: 'https://provider.invalid/fixture', keys: { p256dh: 'fixture-public', auth: 'fixture-auth' } }) }
  activeSubscription = subscription
  subscribe = vi.fn(async () => { activeSubscription = subscription; return subscription })
  const reg = { active: { postMessage: (message: any, ports: any[]) => { workerMessages.push(message); ports[0].peer.onmessage({ data: { ok: true } }) } },
    pushManager: { getSubscription: vi.fn(async () => activeSubscription), subscribe } }
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { register: vi.fn(async () => reg), ready: Promise.resolve(reg) } })
  Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn(async () => 'granted') })
  vi.stubGlobal('MessageChannel', class { port1: any = { close() {} }; port2 = { peer: this.port1 } })
  api.capabilities.mockResolvedValue({ enabled: true, web_push: { enabled: true, public_key: 'AA', key_id: 'key-one' } })
  api.targets.mockImplementation(async () => ({ targets: [serverView()] }))
  api.saveTarget.mockImplementation(async (request: any) => {
    if (request.installation_ref === server.installation_ref) {
      if (request.expected_binding_version !== server.binding_version) throw new Error('server CAS rejected stale generation')
      server = { ...server, binding_version: server.binding_version + 1, state: 'active' }
    } else {
      const user = JSON.parse(localStorage.getItem('saas_admin_tenant-one')!).user_id
      server = { ...initial, user_id: user, target_id: 'target-' + user, installation_ref: request.installation_ref }
    }
    return serverView()
  })
  api.revokeTarget.mockImplementation(async (target: BrowserBinding) => {
    if (target.binding_version !== server.binding_version) throw new Error('server CAS rejected stale revocation')
    server = { ...server, binding_version: server.binding_version + 1, state: 'revoked' }
    return { success: true, target: serverView() }
  })
  api.enablePreference.mockResolvedValue({})
})
afterEach(() => {
  for (const [type, listener] of registeredListeners) window.removeEventListener(type, listener)
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
})

describe('independent browser notification generation boundaries', () => {
  it('reenables after successful logout using the authoritative server generation', async () => {
    const app = await runtime()
    const logout = await app.prepareLogout()
    expect(logout.headers['X-Notification-Binding-Version']).toBe('2')
    await expect(app.enableNotifications(app.readBinding())).rejects.toThrow('账号正在切换')
    expect(api.saveTarget).not.toHaveBeenCalled()
    server = { ...server, binding_version: 3, state: 'revoked' } // Original logout transaction fences the acknowledged tombstone too.
    await logout.complete(true)
    expect(localStorage.getItem(pendingKey)).toBeNull()
    expect(app.readBinding()?.state).toBe('revoked')
    const reopened = await app.enableNotifications(app.readBinding())
    expect(api.saveTarget).toHaveBeenLastCalledWith(expect.objectContaining({ expected_binding_version: 3 }), expect.any(Object))
    expect(reopened.binding_version).toBe(4)
    expect(reopened.state).toBe('active')
  })

  it('revokes before account credentials are replaced and subscribes a new installation', async () => {
    const app = await runtime()
    const preparation = await app.prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-one' })
    expect(api.revokeTarget).toHaveBeenCalledWith(expect.objectContaining({ target_id: initial.target_id }), expect.objectContaining({ Authorization: 'Bearer fixture-token-alice' }))
    expect(app.readBinding()?.state).toBe('revoked')
    expect(workerMessages[workerMessages.length - 1].binding).toBeNull()
    expect(unsubscribe).toHaveBeenCalledOnce()
    await expect(app.enableNotifications(app.readBinding())).rejects.toThrow('账号正在切换')
    credentials('bob')
    await preparation.complete()
    const reopened = await app.enableNotifications(app.readBinding())
    expect(reopened.user_id).toBe('bob')
    expect(reopened.installation_ref).not.toBe(initial.installation_ref)
    expect(subscribe).toHaveBeenCalledOnce()
    expect(api.saveTarget).toHaveBeenCalledWith(expect.not.objectContaining({ expected_binding_version: 1 }), expect.objectContaining({ Authorization: 'Bearer fixture-token-bob' }))
  })

  it('keeps push suppressed and blocks a new account when old revocation failed offline', async () => {
    const app = await runtime()
    api.revokeTarget.mockRejectedValueOnce(new Error('offline'))
    const preparation = await app.prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-one' })
    credentials('bob')
    await preparation.complete()
    await expect(app.enableNotifications(app.readBinding())).rejects.toThrow('旧账号')
    expect(app.readBinding()?.state).toBe('revoked')
    expect(workerMessages[workerMessages.length - 1].binding).toBeNull()
    expect(api.saveTarget).not.toHaveBeenCalled()
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
  })

  it('recovers a refresh that committed on the server but lost its response', async () => {
    const app = await runtime()
    const save = api.saveTarget.getMockImplementation()!
    api.saveTarget.mockImplementationOnce(async (...arguments_: any[]) => {
      await save(...arguments_)
      throw new Error('response lost after commit')
    })
    await expect(app.enableNotifications(app.readBinding())).rejects.toThrow('response lost')
    expect(server.binding_version).toBe(2)
    expect(app.readBinding()?.binding_version).toBe(1)
    const reopened = await app.enableNotifications(app.readBinding())
    expect(api.saveTarget).toHaveBeenLastCalledWith(expect.objectContaining({ expected_binding_version: 2 }), expect.any(Object))
    expect(reopened.binding_version).toBe(3)
  })

  it('does not overwrite a newer tab binding when registration finishes late without Web Locks', async () => {
    const app = await runtime()
    api.saveTarget.mockImplementationOnce(async () => {
      saveLocal({ ...initial, binding_version: 9 })
      return { ...initial, binding_version: 2 }
    })
    await expect(app.enableNotifications(initial)).rejects.toThrow('通知绑定已变化')
    expect(app.readBinding()?.binding_version).toBe(9)
    expect(api.enablePreference).not.toHaveBeenCalled()
  })

  it('does not unsubscribe a newer tab after delayed disable failure', async () => {
    const app = await runtime()
    api.revokeTarget.mockImplementationOnce(async () => {
      saveLocal({ ...initial, binding_version: 9 })
      throw new Error('late failure')
    })
    await expect(app.disableNotifications(initial)).rejects.toThrow('late failure')
    expect(app.readBinding()?.binding_version).toBe(9)
    expect(app.readBinding()?.state).toBe('active')
    expect(unsubscribe).not.toHaveBeenCalled()
    expect(localStorage.getItem(pendingKey)).toBeNull()
  })

  it('does not apply a delayed successful logout completion to a newer browser generation', async () => {
    const app = await runtime()
    const logout = await app.prepareLogout()
    saveLocal({ ...initial, binding_version: 9 })
    await logout.complete(true)
    expect(app.readBinding()?.binding_version).toBe(9)
    expect(app.readBinding()?.state).toBe('active')
  })

  it('recovers an abandoned expired transition only after cleaning its pending old binding', async () => {
    vi.useFakeTimers()
    server = { ...server, binding_version: 2, state: 'revoked' }
    saveLocal({ ...initial, state: 'revoked', revocation_confirmed: false })
    localStorage.setItem(pendingKey, JSON.stringify(initial))
    localStorage.setItem(transitionKey, JSON.stringify({ nonce: 'abandoned-tab', expires_at: Date.now() - 1 }))
    const app = await runtime()
    const reopened = await app.enableNotifications(app.readBinding())
    expect(reopened.binding_version).toBe(3)
    expect(localStorage.getItem(transitionKey)).toBeNull()
    expect(localStorage.getItem(pendingKey)).toBeNull()
    expect(api.saveTarget).toHaveBeenLastCalledWith(expect.objectContaining({ expected_binding_version: 2 }), expect.any(Object))
  })

  it('renews the transition while old-account revocation remains in flight beyond its original TTL', async () => {
    vi.useFakeTimers()
    let finishRevocation: ((value: unknown) => void) | undefined
    api.revokeTarget.mockImplementationOnce(() => new Promise(resolve => { finishRevocation = resolve }))
    const app = await runtime()
    const preparationPromise = app.prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-one' })
    await vi.waitFor(() => expect(api.revokeTarget).toHaveBeenCalledOnce())
    const firstExpiry = JSON.parse(localStorage.getItem(transitionKey)!).expires_at
    await vi.advanceTimersByTimeAsync(121_000)
    expect(Date.now()).toBeGreaterThan(firstExpiry)
    expect(JSON.parse(localStorage.getItem(transitionKey)!).expires_at).toBeGreaterThan(Date.now())
    await expect(app.enableNotifications(app.readBinding())).rejects.toThrow('账号正在切换')
    expect(api.saveTarget).not.toHaveBeenCalled()
    server = { ...server, binding_version: 2, state: 'revoked' }
    finishRevocation!({ success: true, target: serverView() })
    const preparation = await preparationPromise
    credentials('bob'); await preparation.complete()
    expect(localStorage.getItem(transitionKey)).toBeNull()
    expect((await app.enableNotifications(app.readBinding())).user_id).toBe('bob')
  })

  it('preserves another tab transition marker when an earlier logout completes', async () => {
    const app = await runtime()
    const logout = await app.prepareLogout()
    const nextMarker = JSON.stringify({ nonce: 'another-tab', expires_at: Date.now() + 120_000 })
    localStorage.setItem(transitionKey, nextMarker)
    await logout.complete(true)
    expect(localStorage.getItem(transitionKey)).toBe(nextMarker)
    await expect(app.enableNotifications(app.readBinding())).rejects.toThrow('账号正在切换')
  })

  it('does not treat HTTP logout success as acknowledgement of failed target revocation', async () => {
    const app = await runtime()
    api.revokeTarget.mockRejectedValueOnce(new Error('target revocation offline'))
    const logout = await app.prepareLogout()
    await logout.complete(true)
    expect(app.readBinding()?.revocation_confirmed).toBe(false)
    expect(app.readBinding()?.state).toBe('revoked')
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
    credentials('bob')
    await expect(app.enableNotifications(app.readBinding())).rejects.toThrow('旧账号')
    expect(api.saveTarget).not.toHaveBeenCalled()
  })

  it('suppresses a newer same-account binding when logout actually removed its credentials', async () => {
    const app = await runtime()
    const logout = await app.prepareLogout()
    saveLocal({ ...initial, binding_version: 9 })
    localStorage.removeItem('saas_token_tenant-one')
    localStorage.removeItem('saas_admin_tenant-one')
    await logout.complete(true)
    expect(app.readBinding()?.binding_version).toBe(9)
    expect(app.readBinding()?.state).toBe('revoked')
    expect(app.readBinding()?.revocation_confirmed).toBe(false)
    expect(JSON.parse(localStorage.getItem(pendingKey)!).binding_version).toBe(9)
    expect(workerMessages[workerMessages.length - 1].binding).toBeNull()
  })

  it('does not install browser cleanup hooks or register a worker in Desktop', async () => {
    Object.defineProperty(window, 'agentDesktop', { configurable: true, value: {} })
    const app = await runtime()
    expect(navigator.serviceWorker.register).not.toHaveBeenCalled()
    expect((await app.prepareLogout()).headers).toEqual({})
    await app.prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-one' })
    expect(api.revokeTarget).not.toHaveBeenCalled()
  })
})
