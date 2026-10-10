// @vitest-environment node
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { describe, expect, it, vi } from 'vitest'
const source = readFileSync(new URL('../../../public/task-notifications-sw.js', import.meta.url), 'utf8')
function worker(binding: unknown) {
  const events: Record<string, Function> = {}
  const showNotification = vi.fn(), openWindow = vi.fn()
  let stored = binding
  const indexedDB = { open: () => {
    const request: any = {}
    queueMicrotask(() => {
      request.result = { close() {}, transaction: () => {
        const tx: any = { objectStore: () => ({
          get: () => { const q = { result: stored }; queueMicrotask(() => tx.oncomplete()); return q },
          put: (value: unknown) => { stored = value; const q = { result: value }; queueMicrotask(() => tx.oncomplete()); return q },
        }) }
        return tx
      } }
      request.onsuccess()
    })
    return request
  } }
  runInNewContext(source, { self: { addEventListener: (type: string, callback: Function) => { events[type] = callback }, registration: { showNotification }, clients: { openWindow }, location: { origin: 'https://agent.example.test' } }, indexedDB, URL, URLSearchParams, Date, Promise, Number, String })
  return { showNotification, openWindow, dispatch: async (type: string, event: any) => { let pending: Promise<unknown> | undefined; events[type]({ ...event, waitUntil: (p: Promise<unknown>) => { pending = p } }); await pending } }
}
const active = { installation_ref: 'install-1', binding_version: 2, tenant_id: 'tenant-1' }
const payload = { schema_version: 1, notification_ref: 'notification-1', installation_ref: 'install-1', binding_version: 2, expiry: Date.now() / 1000 + 60, event_kind: 'completed', session_title: '工资\u0000核算' }
describe('Web push worker privacy and routing', () => {
  it.each(['completed', 'failed'])('shows the fixed %s text and sanitized title', async event_kind => {
    const sw = worker(active)
    await sw.dispatch('push', { data: { json: () => ({ ...payload, event_kind }) } })
    expect(sw.showNotification).toHaveBeenCalledWith(`工资核算相关任务${event_kind === 'completed' ? '已完成' : '执行失败'}`, expect.objectContaining({ tag: 'notification-1', renotify: false, data: { ref: 'notification-1', tenant: 'tenant-1' } }))
  })
  it.each([{ schema_version: 2 }, { expiry: 1 }, { binding_version: 1 }, { installation_ref: 'other' }, { event_kind: 'progress' }])('rejects stale or invalid payload %s', async patch => {
    const sw = worker(active)
    await sw.dispatch('push', { data: { json: () => ({ ...payload, ...patch }) } })
    expect(sw.showNotification).not.toHaveBeenCalled()
  })
  it('does not show a push after local logout removed the binding', async () => {
    const sw = worker(null)
    await sw.dispatch('push', { data: { json: () => payload } })
    expect(sw.showNotification).not.toHaveBeenCalled()
  })
  it('opens only the fixed same-origin notification entry', async () => {
    const sw = worker(active), close = vi.fn()
    await sw.dispatch('notificationclick', { notification: { close, data: { ref: 'notification-1', tenant: 'tenant-1', url: 'https://evil.example' } } })
    expect(close).toHaveBeenCalled()
    expect(sw.openWindow).toHaveBeenCalledWith('https://agent.example.test/notification?ref=notification-1&tenant=tenant-1')
  })
  it('ignores invalid click references', async () => {
    const sw = worker(active)
    await sw.dispatch('notificationclick', { notification: { close() {}, data: { ref: 'https://evil.example' } } })
    expect(sw.openWindow).not.toHaveBeenCalled()
  })
})
