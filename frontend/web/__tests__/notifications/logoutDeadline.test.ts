import { afterEach, describe, expect, it, vi } from 'vitest'
const complete = vi.hoisted(() => vi.fn(async () => {}))
vi.mock('@/platform/logoutHooks', () => ({ prepareLogout: async () => ({ headers: {}, complete }) }))
import { logout } from '@/api/auth'
import { adminLogout } from '@/api/saasTenant'
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
describe('logout request deadlines preserve local cleanup', () => {
  it.each([['auth', logout], ['saas', adminLogout]] as const)('clears %s credentials and completes the hook after network deadline', async (_name, runLogout) => {
    vi.useFakeTimers(); complete.mockClear(); localStorage.clear()
    window.history.replaceState({}, '', '/t/tenant-1/notifications')
    localStorage.setItem('saas_token_tenant-1', 'mock-test-token')
    localStorage.setItem('saas_admin_tenant-1', JSON.stringify({ user_id: 'alice' }))
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    let signal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      signal = options.signal as AbortSignal
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })))
    const result = runLogout()
    await vi.advanceTimersByTimeAsync(15_000)
    await result
    expect(signal?.aborted).toBe(true)
    expect(localStorage.getItem('saas_token_tenant-1')).toBeNull()
    expect(complete).toHaveBeenCalledWith(false)
    expect(vi.getTimerCount()).toBe(0)
  })
})
