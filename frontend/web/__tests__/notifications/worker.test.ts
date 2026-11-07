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
  it.each([
    ['paused', '每日日报已自动暂停', '点击查看暂停原因，处理后可恢复'],
    ['needs_action', '每日日报需要处理', '点击查看需要处理的事项，处理后自动继续'],
  ])('shows %s wording and opens the notification entry on click', async (kind, title, body) => {
    const sw = worker(active)
    await sw.dispatch('push', { data: { json: () => ({ ...payload, event_kind: kind, session_title: '每日日报' }) } })
    expect(sw.showNotification).toHaveBeenCalledWith(title,
      expect.objectContaining({ body, data: { ref: 'notification-1', tenant: 'tenant-1' } }))
    const shown = sw.showNotification.mock.calls[0][1]
    const close = vi.fn()
    await sw.dispatch('notificationclick', { notification: { close, data: shown.data } })
    expect(sw.openWindow).toHaveBeenCalledWith('https://agent.example.test/notification?ref=notification-1&tenant=tenant-1')
  })

  it('shows manual_rejected wording and routes the click to the notification entry', async () => {
    // §15 评审 P2：窗口收尾任务手动执行被拒——推送文案 + 点击经
    // /notification?ref= 归属核验入口（Entry 路由任务视图）
    const sw = worker(active)
    await sw.dispatch('push', { data: { json: () => ({ ...payload, event_kind: 'manual_rejected', session_title: '每日日报' }) } })
    expect(sw.showNotification).toHaveBeenCalledWith('每日日报手动执行未成功',
      expect.objectContaining({
        body: '点击查看失败原因，可在任务视图重试',
        data: { ref: 'notification-1', tenant: 'tenant-1' },
      }))
    const shown = sw.showNotification.mock.calls[0][1]
    const close = vi.fn()
    await sw.dispatch('notificationclick', { notification: { close, data: shown.data } })
    expect(close).toHaveBeenCalled()
    expect(sw.openWindow).toHaveBeenCalledWith('https://agent.example.test/notification?ref=notification-1&tenant=tenant-1')
  })

  it('opens only the fixed same-origin notification entry', async () => {
    const sw = worker(active), close = vi.fn()
    await sw.dispatch('notificationclick', { notification: { close, data: { ref: 'notification-1', tenant: 'tenant-1', url: 'https://evil.example' } } })
    expect(close).toHaveBeenCalled()
    expect(sw.openWindow).toHaveBeenCalledWith('https://agent.example.test/notification?ref=notification-1&tenant=tenant-1')
  })
  it('opens a validated task result path and preserves old push fallback', async () => {
    const sw = worker(active)
    await sw.dispatch('push', { data: { json: () => ({ ...payload, share_url: '/share/task-result/safe_key' }) } })
    const shown = sw.showNotification.mock.calls[0][1]
    await sw.dispatch('notificationclick', { notification: { close() {}, data: shown.data } })
    expect(sw.openWindow).toHaveBeenCalledWith('https://agent.example.test/share/task-result/safe_key')
    const old = worker(active)
    await old.dispatch('push', { data: { json: () => payload } })
    await old.dispatch('notificationclick', { notification: { close() {}, data: old.showNotification.mock.calls[0][1].data } })
    expect(old.openWindow).toHaveBeenCalledWith('https://agent.example.test/notification?ref=notification-1&tenant=tenant-1')
  })
  it.each(['https://evil.example', '//evil.example', '/share/task-result/a/../b', '/share/task-result/a?next=//evil.example'])('rejects unsafe share_url %s and uses notification entry', async share_url => {
    const sw = worker(active)
    await sw.dispatch('push', { data: { json: () => ({ ...payload, share_url }) } })
    await sw.dispatch('notificationclick', { notification: { close() {}, data: sw.showNotification.mock.calls[0][1].data } })
    expect(sw.openWindow).toHaveBeenCalledWith('https://agent.example.test/notification?ref=notification-1&tenant=tenant-1')
  })
  it('keeps interactive events on their login route even with a share field', async () => {
    const sw = worker(active)
    await sw.dispatch('push', { data: { json: () => ({ ...payload, event_kind: 'paused', share_url: '/share/task-result/safe_key' }) } })
    const shown = sw.showNotification.mock.calls[0][1]
    expect(shown.data.share_url).toBeUndefined()
  })
  it('ignores invalid click references', async () => {
    const sw = worker(active)
    await sw.dispatch('notificationclick', { notification: { close() {}, data: { ref: 'https://evil.example' } } })
    expect(sw.openWindow).not.toHaveBeenCalled()
  })
})
