import { afterEach, describe, expect, it, vi } from 'vitest'
import { TaskResultShareError, viewTaskResultShare } from '@/api/taskResultShare'

afterEach(() => vi.unstubAllGlobals())

describe('public task result API', () => {
  it('reads anonymously before checking one tenant credential', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ state: 'ready' }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    await viewTaskResultShare('share_key')
    await viewTaskResultShare('share_key', 'fixture-token', 'tenant_1')
    expect(fetchMock.mock.calls[0][1]).toEqual({ credentials: 'omit', headers: {} })
    expect(fetchMock.mock.calls[1][1]).toEqual({ credentials: 'omit', headers: {
      Authorization: 'Bearer fixture-token', 'X-Tenant-Id': 'tenant_1',
    } })
  })

  it('retains the HTTP status so the page distinguishes deletion from a temporary error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { error: '任务已删除' } }), { status: 404 })))
    await expect(viewTaskResultShare('gone')).rejects.toMatchObject({
      name: 'Error', message: '任务已删除', status: 404,
    } satisfies Partial<TaskResultShareError>)
  })
})
