import { beforeEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../mocks/server'
import { getSessionMessages } from '@/api/session'

describe('Runner session history request compatibility', () => {
  beforeEach(() => localStorage.clear())

  it('keeps the scheduled task runner filter on its first history request', async () => {
    let query: URLSearchParams | undefined
    server.use(http.get('/api/sessions/session-one/messages', ({ request }) => {
      query = new URL(request.url).searchParams
      return HttpResponse.json({ messages: [] })
    }))

    await getSessionMessages('session-one', 100, 'scheduled-runner')
    expect(query?.get('runner_id')).toBe('scheduled-runner')
    expect(query?.get('limit')).toBe('100')
    expect(query?.has('before_id')).toBe(false)
  })

  it('retains the continuous runner and cursor when loading older messages', async () => {
    let query: URLSearchParams | undefined
    server.use(http.get('/api/sessions/session-one/messages', ({ request }) => {
      query = new URL(request.url).searchParams
      return HttpResponse.json({ messages: [], has_more: false, next_cursor: '0' })
    }))

    const result = await getSessionMessages('session-one', { runner_id: 'continuous-runner', before_id: '123' })
    expect(query?.get('runner_id')).toBe('continuous-runner')
    expect(query?.get('before_id')).toBe('123')
    expect(result.next_cursor).toBe('0')
  })

  it('does not accept an unpageable result as a completed history page', async () => {
    server.use(http.get('/api/sessions/session-one/messages', () => HttpResponse.json({ messages: [] })))
    await expect(getSessionMessages('session-one', { runner_id: 'continuous-runner', before_id: '123' }))
      .rejects.toThrow('历史分页响应无效')
  })
})
