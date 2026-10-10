import { getAuthHeader } from '@/api/auth'
import { credentialGet } from '@/platform/credentialStore'
import { installIdentityChangeHook } from '@/platform/identityHooks'
import { installLogoutHook, type LogoutPreparation } from '@/platform/logoutHooks'
import { getTenantScopedKey } from '@/api/tenantStorage'
import { notificationApi, NotificationApiError, type NotificationTarget } from './api'

const BINDING_KEY = 'task-notifications:binding:v1'
const PENDING_KEY = 'task-notifications:pending:v1'
const INSTALLATION_KEY = 'task-notifications:installation:v1'
const TRANSITION_KEY = 'task-notifications:identity-transition:v1'
const TRANSITION_LIFETIME_MS = 120_000
const BROWSER_OPERATION_TIMEOUT_MS = 5000
async function browserOperation<T>(operation: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([operation, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('浏览器通知清理尚未完成，请稍后重试。')), BROWSER_OPERATION_TIMEOUT_MS)
    })])
  } finally { clearTimeout(timer) }
}
function transitionActive(): boolean {
  const serialized = localStorage.getItem(TRANSITION_KEY)
  if (!serialized) return false
  try {
    const transition = JSON.parse(serialized)
    if (typeof transition.nonce === 'string' && Number.isFinite(transition.expires_at) && transition.expires_at > Date.now()) return true
  } catch {
    // Migrate legacy nonce-only markers to a bounded barrier without clearing live work.
    localStorage.setItem(TRANSITION_KEY, JSON.stringify({ nonce: serialized, expires_at: Date.now() + TRANSITION_LIFETIME_MS }))
    return true
  }
  if (localStorage.getItem(TRANSITION_KEY) === serialized) localStorage.removeItem(TRANSITION_KEY)
  return false
}
function identityTransition(): () => void {
  const nonce = crypto.randomUUID()
  const write = () => localStorage.setItem(TRANSITION_KEY, JSON.stringify({ nonce, expires_at: Date.now() + TRANSITION_LIFETIME_MS }))
  write()
  // An in-flight transition renews its own lease. Closing/crashing the tab stops renewal.
  const renewal = setInterval(() => {
    try {
      const current = JSON.parse(localStorage.getItem(TRANSITION_KEY) || 'null')
      if (current?.nonce === nonce) write()
      else clearInterval(renewal)
    } catch { clearInterval(renewal) }
  }, 30_000)
  return () => {
    clearInterval(renewal)
    try {
      const current = JSON.parse(localStorage.getItem(TRANSITION_KEY) || 'null')
      if (current?.nonce === nonce) localStorage.removeItem(TRANSITION_KEY)
    } catch { /* Do not remove another tab's replacement marker. */ }
  }
}
export interface BrowserBinding extends NotificationTarget { user_id: string; tenant_id: string | null; key_id: string; revocation_confirmed?: boolean; subscription_cleanup_pending?: boolean }
export function readBinding(): BrowserBinding | null {
  try { return JSON.parse(localStorage.getItem(BINDING_KEY) || 'null') } catch { return null }
}
export function currentSubject(): { user_id: string; tenant_id: string | null } | null {
  try {
    const user = JSON.parse(credentialGet(getTenantScopedKey('saas_admin')) || 'null')
    if (!user?.user_id) return null
    return { user_id: user.user_id, tenant_id: window.location.pathname.match(/^\/t\/([^/]+)/)?.[1] || null }
  } catch { return null }
}
export function ownsBinding(binding: BrowserBinding, subject = currentSubject()): boolean {
  return !!subject && binding.user_id === subject.user_id && binding.tenant_id === subject.tenant_id
}
export function supportsNotifications(): boolean {
  return window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && !window.agentDesktop
}
let registration: Promise<ServiceWorkerRegistration> | undefined
export function registerNotificationWorker(): Promise<ServiceWorkerRegistration> {
  if (!supportsNotifications()) return Promise.reject(new Error('当前环境不支持浏览器通知，请使用 HTTPS 和支持推送的浏览器。'))
  registration ??= browserOperation(navigator.serviceWorker.register('/task-notifications-sw.js', { scope: '/', updateViaCache: 'none' })).then(async () => {
    const ready = await browserOperation(navigator.serviceWorker.ready)
    await syncWorkerBinding(ready, readBinding())
    return ready
  }).catch(error => { registration = undefined; throw error })
  return registration
}
async function syncWorkerBinding(reg: ServiceWorkerRegistration, binding: BrowserBinding | null): Promise<void> {
  const worker = reg.active
  if (!worker) throw new Error('通知服务尚未就绪，请稍后重试。')
  await new Promise<void>((resolve, reject) => {
    const channel = new MessageChannel()
    const timer = setTimeout(() => { channel.port1.close(); reject(new Error('通知状态同步失败，请稍后重试。')) }, 5000)
    channel.port1.onmessage = event => {
      clearTimeout(timer); channel.port1.close()
      event.data?.ok ? resolve() : reject(new Error('通知状态保存失败，请稍后重试。'))
    }
    worker.postMessage({ type: 'notification-binding', binding: binding?.state === 'active' ? { installation_ref: binding.installation_ref, binding_version: binding.binding_version, tenant_id: binding.tenant_id } : null }, [channel.port2])
  })
}
function sameBinding(a: BrowserBinding | null, b: BrowserBinding | null): boolean {
  return a?.target_id === b?.target_id && a?.binding_version === b?.binding_version && a?.state === b?.state && a?.user_id === b?.user_id && a?.tenant_id === b?.tenant_id
}
async function withBindingLock<T>(fn: () => Promise<T>): Promise<T> {
  // Server CAS remains authoritative on browsers without Web Locks.
  if (!navigator.locks) return fn()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), BROWSER_OPERATION_TIMEOUT_MS)
  try {
    return await navigator.locks.request('task-notifications:binding', { signal: controller.signal }, async () => {
      clearTimeout(timer)
      if (controller.signal.aborted) throw new Error('通知操作等待超时，请稍后重试。')
      return fn()
    })
  } finally { clearTimeout(timer) }
}
function authFor(binding: BrowserBinding): Record<string, string> | null {
  const suffix = binding.tenant_id ? `_${binding.tenant_id.replace(/[^a-zA-Z0-9_-]/g, '_')}` : ''
  const tokenKey = binding.tenant_id ? `saas_token${suffix}` : 'portal_token'
  const adminKey = binding.tenant_id ? `saas_admin${suffix}` : 'portal_admin'
  try {
    const user = JSON.parse(credentialGet(adminKey) || 'null')
    const token = credentialGet(tokenKey)
    if (!token || user?.user_id !== binding.user_id) return null
    return { Authorization: `Bearer ${token}`, ...(binding.tenant_id ? { 'X-Tenant-Id': binding.tenant_id } : {}) }
  } catch { return null }
}
async function persistBinding(binding: BrowserBinding | null, expected?: BrowserBinding | null): Promise<void> {
  if (expected !== undefined && !sameBinding(expected, readBinding())) throw new Error('通知绑定已变化，请刷新后重试。')
  if (binding) localStorage.setItem(BINDING_KEY, JSON.stringify(binding))
  else localStorage.removeItem(BINDING_KEY)
  const reg = await registerNotificationWorker()
  await syncWorkerBinding(reg, readBinding())
  window.dispatchEvent(new Event('task-notifications-binding'))
}
export async function syncRevokedTarget(target: NotificationTarget): Promise<void> {
  const binding = readBinding()
  if (binding && ownsBinding(binding) && binding.state === 'revoked' && target.state === 'revoked' && binding.target_id === target.target_id) {
    await persistBinding({ ...binding, ...target, revocation_confirmed: true }, binding)
  }
}
async function unsubscribe(expected?: BrowserBinding): Promise<void> {
  const reg = await registerNotificationWorker()
  if (expected && !sameBinding(expected, readBinding())) return
  await syncWorkerBinding(reg, null)
  const subscription = await browserOperation(reg.pushManager.getSubscription())
  if (expected && !sameBinding(expected, readBinding())) return
  const outstanding = readPending()
  if (outstanding?.subscription_cleanup_pending) {
    if (subscription) throw new Error('浏览器仍在清理旧通知，请稍后重试。')
    const cleared = { ...outstanding }
    delete cleared.subscription_cleanup_pending
    localStorage.setItem(PENDING_KEY, JSON.stringify(cleared))
  }
  if (subscription) {
    // Native unsubscribe cannot be cancelled. Every tab must observe this fact
    // until it finishes or getSubscription proves the old subscription is gone.
    const pending = readPending()
    const marked = pending && { ...pending, subscription_cleanup_pending: true }
    if (marked) localStorage.setItem(PENDING_KEY, JSON.stringify(marked))
    await browserOperation(subscription.unsubscribe())
    if (marked && JSON.stringify(readPending()) === JSON.stringify(marked)) {
      localStorage.setItem(PENDING_KEY, JSON.stringify(pending))
    }
  }
}
function readPending(): BrowserBinding | null {
  try { return JSON.parse(localStorage.getItem(PENDING_KEY) || 'null') } catch { return null }
}
function savePending(binding: BrowserBinding): void {
  const existing = readPending()
  const sameInstallation = existing?.target_id === binding.target_id && existing.installation_ref === binding.installation_ref &&
    existing.user_id === binding.user_id && existing.tenant_id === binding.tenant_id
  localStorage.setItem(PENDING_KEY, JSON.stringify({ ...binding,
    ...(sameInstallation && existing?.subscription_cleanup_pending ? { subscription_cleanup_pending: true } : {}) }))
}
function removePending(expected: BrowserBinding): void {
  if (sameBinding(expected, readPending())) localStorage.removeItem(PENDING_KEY)
}
async function installationTarget(binding: BrowserBinding, auth: Record<string, string>): Promise<NotificationTarget | undefined> {
  const { targets } = await notificationApi.targets(auth)
  return targets.find(target => target.target_id === binding.target_id && target.installation_ref === binding.installation_ref)
}
export async function cleanupPending(): Promise<void> {
  const pending = readPending()
  if (!pending) return
  if (pending.subscription_cleanup_pending) {
    const reg = await registerNotificationWorker()
    if (await browserOperation(reg.pushManager.getSubscription())) throw new Error('浏览器仍在清理旧通知，请稍后重试。')
    if (!sameBinding(pending, readPending())) throw new Error('通知绑定已变化，请刷新后重试。')
    const cleared = { ...pending }
    delete cleared.subscription_cleanup_pending
    localStorage.setItem(PENDING_KEY, JSON.stringify(cleared))
  }
  const auth = authFor(pending)
  if (!auth) throw new Error('旧账号的通知撤销尚未完成，请先登录旧账号清理。')
  const snapshot = readBinding()
  // A newer active generation already established by this browser wins over old cleanup.
  if (snapshot?.target_id === pending.target_id && snapshot.state === 'active' && snapshot.binding_version > pending.binding_version) {
    removePending(pending)
    return
  }
  try {
    const latest = await installationTarget(pending, auth)
    if (!sameBinding(snapshot, readBinding())) throw new Error('通知绑定已变化，请刷新后重试。')
    const result = latest?.state === 'revoked' ? { target: latest } : await notificationApi.revokeTarget(latest || pending, auth)
    const current = readBinding()
    if (sameBinding(snapshot, current) && current?.target_id === pending.target_id && current.state === 'revoked') {
      await persistBinding({ ...current, ...result.target, revocation_confirmed: true }, current)
    }
  } catch (error) { if (!(error instanceof NotificationApiError && error.status === 404)) throw error }
  removePending(pending)
}
async function suppressBinding(binding: BrowserBinding, confirmed: boolean, target?: NotificationTarget): Promise<BrowserBinding | null> {
  if (!sameBinding(binding, readBinding())) return null
  const revoked: BrowserBinding = { ...binding, ...target, state: 'revoked', revocation_confirmed: confirmed }
  if (!readPending()) savePending(binding)
  localStorage.setItem(BINDING_KEY, JSON.stringify(revoked))
  const controller = navigator.serviceWorker?.controller
  if (controller) await syncWorkerBinding({ active: controller } as ServiceWorkerRegistration, null)
  await unsubscribe(revoked)
  return revoked
}
async function revokeForIdentityChange(binding: BrowserBinding): Promise<void> {
  if (binding.state === 'revoked' && binding.revocation_confirmed && !readPending()) return
  if (!sameBinding(binding, readBinding())) return
  savePending(binding)
  try {
    const revoked = await suppressBinding(binding, false)
    if (!revoked) return
    const auth = authFor(binding)
    if (!auth) return
    const latest = await installationTarget(binding, auth)
    if (!sameBinding(revoked, readBinding())) return
    const result = latest?.state === 'revoked' ? { target: latest } : await notificationApi.revokeTarget(latest || binding, auth)
    if (sameBinding(revoked, readBinding())) await persistBinding({ ...revoked, ...result.target, revocation_confirmed: true }, revoked)
    removePending(binding)
  } catch { /* Login proceeds with local push suppressed; authenticated cleanup remains pending. */ }
}

function applicationKey(value: string): Uint8Array<ArrayBuffer> {
  const raw = atob(value.replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, c => c.charCodeAt(0))
}
export async function enableNotifications(expected: BrowserBinding | null): Promise<BrowserBinding> {
  const owner = currentSubject()
  const auth = getAuthHeader()
  const assertContext = () => {
    const now = currentSubject()
    if (transitionActive()) throw new Error('账号正在切换，请稍后重试。')
    if (!owner || !now || owner.user_id !== now.user_id || owner.tenant_id !== now.tenant_id || auth.Authorization !== getAuthHeader().Authorization) throw new Error('账号已变化，请刷新后重试。')
  }
  // Request permission synchronously from the user's click, before network awaits (Safari).
  if (!supportsNotifications()) throw new Error('当前环境不支持浏览器通知，请使用 HTTPS 和支持推送的浏览器。')
  if (Notification.permission === 'denied') throw new Error('浏览器已拒绝通知权限，请在网站设置中允许通知后重试。')
  const permission = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission()
  if (permission !== 'granted') throw new Error('尚未允许通知，请在浏览器网站设置中开启。')
  return withBindingLock(async () => {
    assertContext()
    if (!sameBinding(expected, readBinding())) throw new Error('另一个标签页已修改通知绑定，请刷新后重试。')
    await cleanupPending()
    const subject = currentSubject()
    if (!subject) throw new Error('请登录后开启通知。')
    const old = readBinding()
    if (old && !ownsBinding(old, subject)) {
      if (!(old.state === 'revoked' && old.revocation_confirmed && !readPending())) {
        const oldAuth = authFor(old)
        if (!oldAuth) throw new Error('本浏览器已绑定其他账号，请先登录该账号关闭通知。')
        await revokeForIdentityChange(old)
        if (readPending()) throw new Error('旧账号的通知撤销尚未完成，请先登录旧账号清理。')
      }
      const cleared = readBinding()
      if (cleared && (cleared.target_id !== old.target_id || cleared.user_id !== old.user_id)) throw new Error('通知绑定已变化，请刷新后重试。')
      await unsubscribe(cleared || undefined)
      await persistBinding(null, cleared)
      // A new installation is required when changing the owner.
      localStorage.removeItem(INSTALLATION_KEY)
    }
    const activeSnapshot = readBinding()
    const capabilities = await notificationApi.capabilities(auth)
    assertContext()
    if (!capabilities.enabled || !capabilities.web_push.enabled || !capabilities.web_push.public_key) throw new Error('服务端暂未开放浏览器通知。')
    const reg = await registerNotificationWorker()
    let sub = await browserOperation(reg.pushManager.getSubscription())
    if (sub && old && old.key_id !== capabilities.web_push.key_id) { await browserOperation(sub.unsubscribe()); sub = null }
    if (!sub) sub = await browserOperation(reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: applicationKey(capabilities.web_push.public_key) }))
    let installation = old && ownsBinding(old, subject) ? old.installation_ref : localStorage.getItem(INSTALLATION_KEY)
    let recoveredVersion: number | undefined
    // Explicit enable can recover an ambiguous prior response for this subject/installation.
    // Reading status alone never adopts an active server generation.
    if (old && ownsBinding(old, subject)) {
      const snapshot = readBinding()
      const latest = await installationTarget(old, auth)
      assertContext()
      if (!sameBinding(snapshot, readBinding())) throw new Error('通知绑定已变化，请刷新后重试。')
      recoveredVersion = latest?.binding_version ?? old.binding_version
    } else if (installation) {
      const { targets } = await notificationApi.targets(auth)
      recoveredVersion = targets.find(target => target.installation_ref === installation)?.binding_version
      assertContext()
    }
    if (!installation) { installation = crypto.randomUUID(); localStorage.setItem(INSTALLATION_KEY, installation) }
    const json = sub.toJSON()
    assertContext()
    const target = await notificationApi.saveTarget({ channel: 'web_push', provider: 'web_push', installation_ref: installation,
      ...(recoveredVersion !== undefined ? { expected_binding_version: recoveredVersion } : {}),
      configuration: { endpoint: json.endpoint, keys: json.keys, expirationTime: json.expirationTime ?? null, key_id: capabilities.web_push.key_id } }, auth)
    try { assertContext() } catch (error) { await notificationApi.revokeTarget(target, auth); throw error }
    const binding: BrowserBinding = { ...target, ...subject, key_id: capabilities.web_push.key_id }
    // Persist the target before preference write so failures remain explicitly recoverable.
    await persistBinding(binding, activeSnapshot)
    assertContext()
    if (!sameBinding(binding, readBinding())) throw new Error('通知绑定已变化，请刷新后重试。')
    await notificationApi.enablePreference(auth)
    assertContext()
    if (!sameBinding(binding, readBinding())) throw new Error('通知绑定已变化，请刷新后重试。')
    return binding
  })
}
export async function disableNotifications(expected: BrowserBinding): Promise<void> {
  await withBindingLock(async () => {
    if (!sameBinding(expected, readBinding()) || !ownsBinding(expected)) throw new Error('通知绑定已变化，请刷新后重试。')
    const auth = getAuthHeader()
    try {
      const latest = await installationTarget(expected, auth)
      if (!sameBinding(expected, readBinding()) || !ownsBinding(expected)) throw new Error('通知绑定已变化，请刷新后重试。')
      const result = latest?.state === 'revoked' ? { target: latest } : await notificationApi.revokeTarget(latest || expected, auth)
      await suppressBinding(expected, true, result.target)
      removePending(expected)
    } catch (error) {
      // CAS protects a newer tab's binding/subscription from a delayed failure.
      if (sameBinding(expected, readBinding())) {
        savePending(expected)
        await suppressBinding(expected, false)
      }
      throw error
    }
  })
}
export function initializeWebNotifications(): void {
  if (window.agentDesktop) return
  if (supportsNotifications()) void registerNotificationWorker().catch(() => {})
  window.addEventListener('storage', event => {
    if (event.key === BINDING_KEY && supportsNotifications()) {
      void registerNotificationWorker().then(reg => syncWorkerBinding(reg, readBinding())).catch(() => {})
    } else if (event.key && /^(?:saas|portal)_(?:token|admin)(?:_|$)/.test(event.key)) {
      const binding = readBinding()
      if (binding && !authFor(binding)) void withBindingLock(() => revokeForIdentityChange(binding)).catch(() => {})
    }
  })
  window.addEventListener('online', () => { void withBindingLock(cleanupPending).catch(() => {}) })
  installIdentityChangeHook(async next => {
    const binding = readBinding()
    const finish = identityTransition()
    try {
      await withBindingLock(async () => {
        if (binding && !ownsBinding(binding, next)) await revokeForIdentityChange(binding)
      })
      return { complete: async () => {
        try {
          const current = readBinding()
          if (current && current.state === 'active' && !ownsBinding(current, next)) await revokeForIdentityChange(current)
        } finally { finish() }
      } }
    } catch {
      if (binding && !ownsBinding(binding, next)) await suppressBinding(binding, false).catch(() => {})
      return { complete: async () => { finish() } }
    }
  })
  installLogoutHook(async (): Promise<LogoutPreparation> => {
    const binding = readBinding()
    const finish = identityTransition()
    if (!binding || !ownsBinding(binding)) return { headers: {}, complete: async () => { finish() } }
    try { await withBindingLock(() => revokeForIdentityChange(binding)) }
    catch { await suppressBinding(binding, false).catch(() => {}) }
    const revoked = readBinding()
    const headerTarget = revoked && revoked.target_id === binding.target_id ? revoked : binding
    return { headers: { 'X-Notification-Target-Id': headerTarget.target_id, 'X-Notification-Binding-Version': String(headerTarget.binding_version) },
      complete: async (_success: boolean) => {
        try {
          // HTTP logout success alone does not prove its target CAS succeeded.
          // Explicit DELETE acknowledgement above is the revocation authority.
          const current = readBinding()
          if (current?.state === 'active' && ownsBinding(current, binding) && !authFor(current)) {
            savePending(current)
            await suppressBinding(current, false)
          }
        } finally { finish() }
      } }

  })
}
