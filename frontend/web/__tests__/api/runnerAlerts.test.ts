import { beforeEach, describe, expect, it, vi } from 'vitest'
import { runnerAlertsAPI } from '@/api/runnerAlerts'

vi.mock('@/api/auth', () => ({
  getAuthHeader: () => ({ Authorization: 'Bearer platform', 'X-Tenant-Id': 'delegated-tenant' }),
}))

describe('platform runner alerts API', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('never forwards a delegated tenant header to global alert reads', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, items: [], total: 0 }),
    } as Response)

    await runnerAlertsAPI.list({ state: 'all', page: 1, page_size: 20, runner_id: 'run/a' })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toContain('state=all')
    expect(url).toContain('runner_id=run%2Fa')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer platform')
    expect((init?.headers as Record<string, string>)['X-Tenant-Id']).toBeUndefined()
  })

  it('uses the current binding version when replacing or revoking the platform target', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, target: null }),
    } as Response)

    await runnerAlertsAPI.saveTarget('https://open.feishu.cn/open-apis/bot/v2/hook/example', 7)
    await runnerAlertsAPI.deleteTarget(7)

    const put = fetchMock.mock.calls[0][1]
    const del = fetchMock.mock.calls[1][1]
    expect(JSON.parse(String(put?.body))).toEqual({
      webhook_url: 'https://open.feishu.cn/open-apis/bot/v2/hook/example',
      expected_binding_version: 7,
    })
    expect((del?.headers as Record<string, string>)['X-Notification-Binding-Version']).toBe('7')
    expect((del?.headers as Record<string, string>)['X-Tenant-Id']).toBeUndefined()
  })
})
