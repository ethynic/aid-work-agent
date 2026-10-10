import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
const api = vi.hoisted(() => ({ capabilities: vi.fn(), targets: vi.fn(), saveTarget: vi.fn(), enablePreference: vi.fn(), revokeTarget: vi.fn() }))
vi.mock('@/notifications/api', () => ({ notificationApi: api, NotificationApiError: class extends Error { status = 404 } }))
import { currentSubject, readBinding, enableNotifications, disableNotifications, initializeWebNotifications, type BrowserBinding } from '@/notifications/client'
import { prepareIdentityChange } from '@/platform/identityHooks'
import { prepareLogout } from '@/platform/logoutHooks'
const key = 'task-notifications:binding:v1'
const pendingKey = 'task-notifications:pending:v1'
const binding: BrowserBinding = { target_id: 'target-1', installation_ref: 'installation-1', binding_version: 1, state: 'active', enabled_since: null, user_id: 'alice', tenant_id: 'tenant-1', key_id: 'key-1' }
const unsubscribe = vi.fn(async () => true)
const subscribe = vi.fn(async () => subscription)
const subscription = { unsubscribe, toJSON: () => ({ endpoint: 'https://push.example.test/secret', keys: { p256dh: 'public', auth: 'secret' } }) }
const reg = { active: { postMessage: (_msg: unknown, ports: any[]) => ports[0].peer.onmessage({ data: { ok: true } }) }, pushManager: { getSubscription: vi.fn(async () => subscription), subscribe } }
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear()
  window.history.replaceState({}, '', '/t/tenant-1/notifications')
  localStorage.setItem('saas_admin_tenant-1', JSON.stringify({ user_id: 'alice' }))
  localStorage.setItem('saas_token_tenant-1', 'test-only-credential')
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { register: vi.fn(async () => reg), ready: Promise.resolve(reg) } })
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('Notification', { permission: 'granted', requestPermission: vi.fn(async () => 'granted') })
  vi.stubGlobal('MessageChannel', class { port1: any = { close() {} }; port2 = { peer: this.port1 } })
  api.capabilities.mockResolvedValue({ enabled: true, web_push: { enabled: true, public_key: 'AA', key_id: 'key-1' } })
  api.targets.mockResolvedValue({ targets: [] })
  api.saveTarget.mockResolvedValue(binding)
  api.enablePreference.mockResolvedValue({})
  api.revokeTarget.mockResolvedValue({ target: { ...binding, binding_version: 2, state: 'revoked' } })
})
afterEach(() => vi.unstubAllGlobals())
describe('browser notification ownership', () => {
  it('scopes identity by tenant and registers no credentials in the binding', async () => {
    expect(currentSubject()).toEqual({ user_id: 'alice', tenant_id: 'tenant-1' })
    await enableNotifications(null)
    expect(api.saveTarget).toHaveBeenCalledWith(expect.objectContaining({ channel: 'web_push', provider: 'web_push', configuration: expect.objectContaining({ key_id: 'key-1' }) }), expect.objectContaining({ 'X-Tenant-Id': 'tenant-1' }))
    expect(api.enablePreference).toHaveBeenCalledOnce()
    expect(localStorage.getItem(key)).not.toContain('test-only-credential')
    expect(localStorage.getItem(key)).not.toContain('push.example')
  })
  it('does not adopt a newer cross-tab binding', async () => {
    localStorage.setItem(key, JSON.stringify({ ...binding, binding_version: 2 }))
    await expect(enableNotifications(binding)).rejects.toThrow('另一个标签页')
    expect(api.saveTarget).not.toHaveBeenCalled()
  })
  it('does not take over an endpoint belonging to an unauthenticated old account', async () => {
    const old = { ...binding, user_id: 'bob' }
    localStorage.setItem(key, JSON.stringify(old))
    await expect(enableNotifications(old)).rejects.toThrow('先登录该账号')
    expect(api.saveTarget).not.toHaveBeenCalled()
    expect(api.revokeTarget).not.toHaveBeenCalled()
  })
  it('uses the expected binding version on re-registration', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    await enableNotifications(binding)
    expect(api.saveTarget).toHaveBeenCalledWith(expect.objectContaining({ expected_binding_version: 1 }), expect.any(Object))
  })
  it('retains only pending metadata and disables local push if offline revoke fails', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    api.revokeTarget.mockRejectedValueOnce(new Error('offline'))
    await expect(disableNotifications(binding)).rejects.toThrow('offline')
    expect(readBinding()?.state).toBe('revoked')
    expect(unsubscribe).toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem(pendingKey)!)).toEqual(binding)
    expect(localStorage.getItem(pendingKey)).not.toContain('credential')
  })
  it('adds logout CAS headers, clears local subscription, and keeps offline pending cleanup', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    initializeWebNotifications()
    const hook = await prepareLogout()
    expect(hook.headers).toEqual({ 'X-Notification-Target-Id': 'target-1', 'X-Notification-Binding-Version': '2' })
    expect(unsubscribe).toHaveBeenCalled()
    expect(readBinding()?.state).toBe('revoked')
    await hook.complete(false)
    // Explicit target revocation is confirmed even if the later logout request fails.
    expect(localStorage.getItem(pendingKey)).toBeNull()
  })
  it('recovers a lost target registration response using the server version for this installation', async () => {
    localStorage.setItem('task-notifications:installation:v1', 'installation-1')
    api.targets.mockResolvedValue({ targets: [{ ...binding, binding_version: 4 }] })
    await enableNotifications(null)
    expect(api.saveTarget).toHaveBeenCalledWith(expect.objectContaining({ expected_binding_version: 4 }), expect.any(Object))
  })
  it('aborts an in-flight enable when another account replaces this tenant credential', async () => {
    api.capabilities.mockImplementationOnce(async () => {
      localStorage.setItem('saas_token_tenant-1', 'new-account-token')
      localStorage.setItem('saas_admin_tenant-1', JSON.stringify({ user_id: 'bob' }))
      return { enabled: true, web_push: { enabled: true, public_key: 'AA', key_id: 'key-1' } }
    })
    await expect(enableNotifications(null)).rejects.toThrow('账号已变化')
    expect(api.saveTarget).not.toHaveBeenCalled()
  })
  it('allows a confirmed revoked foreign binding to switch after the old token is deleted', async () => {
    const old = { ...binding, user_id: 'bob', state: 'revoked', revocation_confirmed: true }
    localStorage.setItem(key, JSON.stringify(old))
    localStorage.setItem('task-notifications:installation:v1', 'old-installation')
    await enableNotifications(old)
    expect(api.revokeTarget).not.toHaveBeenCalled()
    expect(api.saveTarget).toHaveBeenCalledWith(expect.objectContaining({ installation_ref: expect.any(String) }), expect.any(Object))
    expect(api.saveTarget.mock.calls[0][0].installation_ref).not.toBe('old-installation')
  })
  it('blocks a revoked foreign binding whose offline cleanup is unconfirmed', async () => {
    const old = { ...binding, user_id: 'bob', state: 'revoked', revocation_confirmed: false }
    localStorage.setItem(key, JSON.stringify(old))
    localStorage.setItem(pendingKey, JSON.stringify(old))
    await expect(enableNotifications(old)).rejects.toThrow('撤销尚未完成')
    expect(api.saveTarget).not.toHaveBeenCalled()
  })
  it('recovers the server version after a successful logout before re-enabling the same account', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    initializeWebNotifications()
    const hook = await prepareLogout()
    await hook.complete(true)
    api.targets.mockResolvedValue({ targets: [{ ...binding, state: 'revoked', binding_version: 2 }] })
    await enableNotifications(readBinding())
    expect(api.saveTarget).toHaveBeenCalledWith(expect.objectContaining({ expected_binding_version: 2 }), expect.any(Object))
  })
  it('recovers a newer server registration version only on explicit enable', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    api.targets.mockResolvedValue({ targets: [{ ...binding, binding_version: 2 }] })
    expect(readBinding()?.binding_version).toBe(1)
    await enableNotifications(binding)
    expect(api.saveTarget).toHaveBeenCalledWith(expect.objectContaining({ expected_binding_version: 2 }), expect.any(Object))
  })
  it('keeps a newer tab binding/subscription when an old disable request fails late', async () => {
    const newer = { ...binding, binding_version: 3 }
    localStorage.setItem(key, JSON.stringify(binding))
    api.revokeTarget.mockImplementationOnce(async () => {
      localStorage.setItem(key, JSON.stringify(newer))
      throw new Error('late network failure')
    })
    await expect(disableNotifications(binding)).rejects.toThrow('late network failure')
    expect(readBinding()).toEqual(newer)
    expect(unsubscribe).not.toHaveBeenCalled()
    expect(localStorage.getItem(pendingKey)).toBeNull()
  })
  it('clears the old worker before direct login overwrites credentials, even from the root route', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    window.history.replaceState({}, '', '/')
    initializeWebNotifications()
    api.revokeTarget.mockResolvedValueOnce({ target: { ...binding, state: 'revoked', binding_version: 2 } })
    const identity = await prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-1' })
    await identity.complete()
    expect(unsubscribe).toHaveBeenCalled()
    expect(api.revokeTarget).toHaveBeenCalledWith(expect.objectContaining({ target_id: 'target-1' }), expect.objectContaining({ Authorization: 'Bearer test-only-credential' }))
    expect(readBinding()).toMatchObject({ state: 'revoked', revocation_confirmed: true })
    expect(localStorage.getItem(pendingKey)).toBeNull()
  })
  it('keeps only pending metadata after direct login offline cleanup', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    initializeWebNotifications()
    api.revokeTarget.mockRejectedValueOnce(new Error('offline'))
    const identity = await prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-1' })
    await identity.complete()
    expect(readBinding()).toMatchObject({ state: 'revoked', revocation_confirmed: false })
    expect(unsubscribe).toHaveBeenCalled()
    expect(localStorage.getItem(pendingKey)).not.toContain('test-only-credential')
    expect(JSON.parse(localStorage.getItem(pendingKey)!)).toEqual(binding)
  })
  it('allows an expired identity barrier to recover without reviving old binding state', async () => {
    localStorage.setItem('task-notifications:identity-transition:v1', JSON.stringify({ nonce: 'crashed-tab', expires_at: Date.now() - 1 }))
    localStorage.setItem(key, JSON.stringify({ ...binding, state: 'revoked', revocation_confirmed: true }))
    api.targets.mockResolvedValue({ targets: [{ ...binding, binding_version: 2, state: 'revoked' }] })
    await enableNotifications(readBinding())
    expect(localStorage.getItem('task-notifications:identity-transition:v1')).toBeNull()
    expect(api.saveTarget).toHaveBeenCalledWith(expect.objectContaining({ expected_binding_version: 2 }), expect.any(Object))
  })
  it('keeps an expired barrier recovery blocked when old-account revocation is pending', async () => {
    const old = { ...binding, user_id: 'bob', state: 'revoked' }
    localStorage.setItem('task-notifications:identity-transition:v1', JSON.stringify({ nonce: 'crashed-tab', expires_at: 1 }))
    localStorage.setItem(key, JSON.stringify(old)); localStorage.setItem(pendingKey, JSON.stringify(old))
    await expect(enableNotifications(old)).rejects.toThrow('旧账号')
    expect(readBinding()).toEqual(old)
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
    expect(api.saveTarget).not.toHaveBeenCalled()
  })
  it('renews the identity barrier while cleanup is still in flight beyond its original TTL', async () => {
    vi.useFakeTimers()
    localStorage.setItem(key, JSON.stringify(binding))
    initializeWebNotifications()
    let release!: () => void
    api.revokeTarget.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ target: { ...binding, state: 'revoked', binding_version: 2 } }) }))
    const preparation = prepareIdentityChange({ user_id: 'bob', tenant_id: 'tenant-1' })
    try {
      await vi.advanceTimersByTimeAsync(180_000)
      const transition = JSON.parse(localStorage.getItem('task-notifications:identity-transition:v1')!)
      expect(transition.expires_at).toBeGreaterThan(Date.now())
      await expect(enableNotifications(readBinding())).rejects.toThrow('账号正在切换')
      expect(api.saveTarget).not.toHaveBeenCalled()
    } finally {
      release()
      const identity = await preparation
      await identity.complete()
      vi.useRealTimers()
    }
  })
  it('retains pending revocation when logout returns success but explicit cleanup failed', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    initializeWebNotifications()
    api.revokeTarget.mockRejectedValueOnce(new Error('offline'))
    const hook = await prepareLogout()
    await hook.complete(true)
    expect(readBinding()).toMatchObject({ state: 'revoked', revocation_confirmed: false })
    expect(localStorage.getItem(pendingKey)).toBeTruthy()
  })
  it('suppresses a newer same-owner binding if logout finishes after its credentials were removed', async () => {
    localStorage.setItem(key, JSON.stringify(binding))
    initializeWebNotifications()
    const hook = await prepareLogout()
    const newer = { ...binding, binding_version: 9 }
    localStorage.setItem(key, JSON.stringify(newer))
    localStorage.removeItem('saas_token_tenant-1'); localStorage.removeItem('saas_admin_tenant-1')
    await hook.complete(true)
    expect(readBinding()).toMatchObject({ binding_version: 9, state: 'revoked', revocation_confirmed: false })
    expect(JSON.parse(localStorage.getItem(pendingKey)!)).toEqual(newer)
  })
  it('does not repeatedly request permission after a denial', async () => {
    vi.stubGlobal('Notification', { permission: 'denied', requestPermission: vi.fn() })
    await expect(enableNotifications(null)).rejects.toThrow('浏览器已拒绝')
    expect(Notification.requestPermission).not.toHaveBeenCalled()
    expect(api.capabilities).not.toHaveBeenCalled()
  })
})
