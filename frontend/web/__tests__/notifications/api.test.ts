import { afterEach, describe, expect, it, vi } from 'vitest'
import { notificationApi, notificationRequest } from '@/notifications/api'
afterEach(() => vi.unstubAllGlobals())
describe('notification authenticated API', () => {
  it('uses tenant credentials and CAS for the target DELETE', async () => {
    window.history.replaceState({}, '', '/t/tenant-1/notifications')
    localStorage.setItem('saas_token_tenant-1', 'test-token')
    const fetch = vi.fn(async () => new Response(JSON.stringify({ success: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    await notificationApi.revokeTarget({ target_id: 'target-1', binding_version: 3 })
    expect(fetch).toHaveBeenCalledWith('/api/notifications/targets/target-1', expect.objectContaining({ method: 'DELETE', headers: expect.objectContaining({ Authorization: 'Bearer test-token', 'X-Tenant-Id': 'tenant-1', 'X-Notification-Binding-Version': '3' }) }))
  })
  it('aborts a stalled notification request after a bounded deadline', async () => {
    vi.useFakeTimers()
    let signal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      signal = options.signal as AbortSignal
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })))
    try {
      const assertion = expect(notificationRequest('/targets')).rejects.toThrow('请求超时')
      await vi.advanceTimersByTimeAsync(15_000)
      await assertion
      expect(signal?.aborted).toBe(true)
      expect(vi.getTimerCount()).toBe(0)
    } finally { vi.useRealTimers() }
  })
  it.each([[401, '请登录'], [403, '无权'], [404, '已不存在'], [409, '绑定已变化']])('presents a safe error for %i without exposing response data', async (status, text) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('other-tenant-sensitive-title', { status })))
    await expect(notificationRequest('/messages/ref')).rejects.toThrow(text)
  })
})
