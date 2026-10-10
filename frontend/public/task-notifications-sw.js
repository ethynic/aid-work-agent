/* Web-only push worker: no fetch handler, API calls, tokens, or background tasks. */
const DB = 'task-notifications-v1'
function bindingStore(write, value) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1)
    request.onupgradeneeded = () => request.result.createObjectStore('state')
    request.onerror = () => reject(request.error)
    request.onsuccess = () => {
      const db = request.result
      const tx = db.transaction('state', write ? 'readwrite' : 'readonly')
      const store = tx.objectStore('state')
      const query = write ? store.put(value, 'binding') : store.get('binding')
      tx.oncomplete = () => { db.close(); resolve(query.result) }
      tx.onerror = () => { db.close(); reject(tx.error) }
      tx.onabort = () => { db.close(); reject(tx.error) }
    }
  })
}
self.addEventListener('message', event => {
  if (event.data?.type !== 'notification-binding') return
  const value = event.data.binding
  const binding = value && typeof value.installation_ref === 'string' && Number.isSafeInteger(value.binding_version)
    ? { installation_ref: value.installation_ref, binding_version: value.binding_version, tenant_id: value.tenant_id || null } : null
  event.waitUntil(bindingStore(true, binding).then(() => event.ports[0]?.postMessage({ ok: true })).catch(() => event.ports[0]?.postMessage({ ok: false })))
})
self.addEventListener('push', event => {
  event.waitUntil((async () => {
    let payload
    try { payload = event.data?.json() } catch { return }
    if (!payload || payload.schema_version !== 1 || !/^[a-zA-Z0-9_-]{1,160}$/.test(payload.notification_ref || '') ||
      !['completed', 'failed', 'test'].includes(payload.event_kind) || !Number.isFinite(payload.expiry) || payload.expiry <= Date.now() / 1000) return
    const binding = await bindingStore(false)
    if (!binding || binding.installation_ref !== payload.installation_ref || binding.binding_version !== payload.binding_version) return
    const sessionTitle = String(payload.session_title || '').replace(/[\u0000-\u001f\u007f-\u009f]/g, '').trim().slice(0, 80) || '未命名会话'
    const title = payload.event_kind === 'test' ? '浏览器通知测试' : `${sessionTitle}相关任务${payload.event_kind === 'completed' ? '已完成' : '执行失败'}`
    await self.registration.showNotification(title, {
      body: payload.event_kind === 'test' ? '本浏览器已可以接收任务提醒。' : '点击查看结果或详情',
      icon: '/favicon.ico', tag: payload.notification_ref, renotify: false,
      data: { ref: payload.notification_ref, tenant: binding.tenant_id },
    })
  })())
})
self.addEventListener('notificationclick', event => {
  event.notification.close()
  const data = event.notification.data || {}
  if (!/^[a-zA-Z0-9_-]{1,160}$/.test(data.ref || '')) return
  const query = new URLSearchParams({ ref: data.ref })
  if (typeof data.tenant === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(data.tenant)) query.set('tenant', data.tenant)
  const url = new URL(`/notification?${query}`, self.location.origin).href
  event.waitUntil(self.clients.openWindow(url))
})
