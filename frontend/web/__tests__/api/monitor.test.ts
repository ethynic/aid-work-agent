import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getSessionTraces } from '@/api/monitor'

describe('monitor api', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('passes explicit include_intermediate flag', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      json: async () => ({ success: true, traces: [] }),
    } as Response)

    await getSessionTraces('sid/with slash', false)

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/sessions/sid%2Fwith%20slash/traces?include_intermediate=false'),
      expect.any(Object),
    )
  })
})
