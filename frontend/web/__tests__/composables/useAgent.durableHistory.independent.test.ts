/** External transport/history seams only; the real composable and reducers run. */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { controlledRunnerFetch } from '../mocks/runnerFetch'
import { runnerView } from '../mocks/runnerView'

const external = vi.hoisted(() => ({ token: 'history-subject-A', history: vi.fn(), legacy: vi.fn() }))
vi.mock('@/api/agent', () => ({ SSEManager: class { connect = external.legacy; disconnect() {} }, uploadFile: vi.fn() }))
vi.mock('@/api/session', () => ({ getSessionMessages: external.history }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer ' + external.token }) }) }))
vi.mock('@/composables/useVideoGenParams', () => ({ useVideoGenParams: () => ({ params: { value: null } }) }))
vi.mock('@/composables/useCreditCheck', () => ({ useCreditCheck: () => ({ checkCreditBeforeAction: async () => ({ allowed: true }) }) }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function page(messages: ReturnType<typeof archive>[], next_cursor = '100', has_more = false) {
  return { messages, next_cursor, has_more }
}

function archive(index: number) {
  return { message_id: `runner-A:history:artifacts:${index}`, session_id: 'A', role: 'assistant', content: '',
    created_at: '2026-10-22T12:00:00Z', metadata: { runner_id: 'runner-A', runner_queue_order: 1,
      images: [{ file_id: `image-${index}` }], downloadableFiles: [{ file_id: `file-${index}`, file_name: `file-${index}.txt` }] } }
}

describe('Live durable history remains visible without compromising session ownership', () => {
  let network: ReturnType<typeof controlledRunnerFetch>
  let agent: ReturnType<typeof import('@/composables/useAgent').useAgent>
  const flush = async () => { for (let index = 0; index < 32; index++) await Promise.resolve() }
  const request = (path: string, method = 'GET') => {
    const pending = network.pending.find(item => new URL(item.url).pathname === '/api/chat' + path && item.method === method)
    expect(pending, `Expected ${method} ${path}`).toBeDefined()
    return pending!
  }
  async function accept() {
    const sending = agent.sendMessage('continuous task', null, 'A')
    await flush()
    request('/runners/capabilities').respond({ web_enabled: true, observe_existing: true, contract_version: 1, transport: 'runner_poll' })
    await flush()
    request('/sessions/A/runners').respond({ runners: [], active_runners: [], has_more: false, next_cursor: null })
    await flush()
    const submit = request('/runners', 'POST')
    submit.respond({ success: true, runner: runnerView({ execution_mode: 'continuous',
      client_request_id: JSON.parse(submit.body!).client_request_id }) }, 202)
    await sending
    await flush()
    external.history.mockClear()
  }
  async function snapshot(revision: number, historyRevision?: number, output = '') {
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ execution_mode: 'continuous', view_revision: revision,
      snapshot: { output, history_version: 1, ...(historyRevision === undefined ? {} : { history_revision: historyRevision }) } }) })
    await flush()
  }
  async function nextSnapshot(revision: number, historyRevision?: number, output = '') {
    await vi.advanceTimersByTimeAsync(1000)
    await snapshot(revision, historyRevision, output)
  }
  beforeEach(async () => {
    vi.resetModules(); vi.useFakeTimers()
    external.token = 'history-subject-A'
    external.history.mockReset().mockResolvedValue({ messages: [] })
    external.legacy.mockReset()
    network = controlledRunnerFetch(); vi.stubGlobal('fetch', network.fetch)
    agent = (await import('@/composables/useAgent')).useAgent()
    agent.sessionId.value = 'A'
  })
  afterEach(async () => {
    agent.clearSessionCache(); network.detachOutstanding(); await flush()
    vi.unstubAllGlobals(); vi.useRealTimers()
  })

  it('older snapshots with no history revision do not trigger additional history requests', async () => {
    await accept()
    await snapshot(2)
    await nextSnapshot(3)
    expect(external.history).not.toHaveBeenCalled()
  })

  it('new archive watermarks load images and downloads once while preserving transient input', async () => {
    await accept()
    const pending = deferred<ReturnType<typeof page>>()
    external.history.mockReturnValueOnce(pending.promise)
    await snapshot(2, 1)
    agent.messages.value.push({ role: 'user', content: 'draft accepted locally', messageId: 'transient-user', clientRequestId: 'pending-key' })
    pending.resolve(page([archive(0)])); await flush()
    expect(agent.messages.value.find(item => item.messageId === archive(0).message_id)?.images?.[0].file_id).toBe('image-0')
    expect(agent.messages.value.find(item => item.messageId === archive(0).message_id)?.downloadableFiles?.[0].file_id).toBe('file-0')
    expect(agent.messages.value.some(item => item.messageId === 'transient-user')).toBe(true)
    await nextSnapshot(3, 1)
    expect(external.history).toHaveBeenCalledTimes(1)
  })

  it('a newer watermark arriving during one request requires a subsequent request', async () => {
    await accept()
    const first = deferred<ReturnType<typeof page>>()
    const second = deferred<ReturnType<typeof page>>()
    external.history.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    await snapshot(2, 1)
    await nextSnapshot(3, 2)
    expect(external.history).toHaveBeenCalledTimes(1)
    first.resolve(page([archive(0)])); await flush()
    // The next captured revision is scheduled on a zero-delay task so a long
    // archive does not monopolize the current snapshot callback.
    await vi.advanceTimersByTimeAsync(0); await flush()
    expect(external.history).toHaveBeenCalledTimes(2)
    second.resolve(page([archive(0), archive(1)], '200')); await flush()
    expect(agent.messages.value.filter(item => item.messageId === archive(0).message_id)).toHaveLength(1)
    expect(agent.messages.value.some(item => item.messageId === archive(1).message_id)).toBe(true)
  })

  it('failure never consumes the watermark, so a later snapshot retries it', async () => {
    await accept()
    external.history.mockRejectedValueOnce(new Error('fixture temporary history outage'))
      .mockResolvedValueOnce(page([archive(0)]))
    await snapshot(2, 1)
    await nextSnapshot(3, 1)
    expect(external.history).toHaveBeenCalledTimes(2)
    expect(agent.messages.value.some(item => item.messageId === archive(0).message_id)).toBe(true)
  })

  it('late history from a cleared identity cannot appear in the next authenticated chat', async () => {
    await accept()
    const old = deferred<ReturnType<typeof page>>()
    external.history.mockReturnValueOnce(old.promise)
    await snapshot(2, 1)
    agent.clearSessionCache(); external.token = 'history-subject-B'
    external.history.mockResolvedValue({ messages: [] })
    const changing = agent.switchSession('B'); await flush()
    request('/runners/capabilities').respond({ web_enabled: false, observe_existing: false, contract_version: 1, transport: 'runner_poll' })
    await changing; await flush()
    old.resolve(page([archive(0)])); await flush()
    expect(agent.sessionId.value).toBe('B')
    expect(agent.messages.value.some(item => item.runnerId === 'runner-A')).toBe(false)
    await vi.advanceTimersByTimeAsync(3000)
    expect(network.requests.some(item => item.url.endsWith('/cancel'))).toBe(false)
  })

  it('persisted control input survives after its live projection window disappears', async () => {
    await accept()
    external.history.mockResolvedValueOnce({ messages: [{ message_id: 'control_original:user', session_id: 'A', role: 'user',
      content: 'only answer important updates', created_at: '2026-10-22T12:00:00Z',
      metadata: { runner_id: 'runner-A', runner_queue_order: 1, control_id: 'control_original' } }], next_cursor: '100', has_more: false })
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ execution_mode: 'continuous', view_revision: 2,
      snapshot: { history_version: 1, history_revision: 1, supplementalInputs: [{ control_id: 'control_original',
        client_request_id: 'original-rule', message_id: 'control_original:user', text: 'only answer important updates',
        accepted_at: '2026-10-22T12:00:00Z' }] } }) })
    await flush()
    expect(agent.messages.value.some(item => item.messageId === 'control_original:user')).toBe(true)
    await nextSnapshot(3, 1)
    expect(agent.messages.value.filter(item => item.messageId === 'control_original:user')).toHaveLength(1)
    expect(agent.messages.value.find(item => item.messageId === 'control_original:user')?.runnerHistory).toBe(true)
  })

  it('two live archive refreshes replace the history window and preserve the actual current reply', async () => {
    await accept()
    external.history.mockResolvedValueOnce(page(Array.from({ length: 100 }, (_, index) => archive(index)), '1', true))
      .mockResolvedValueOnce(page(Array.from({ length: 100 }, (_, index) => archive(index + 100)), '101', true))
    await snapshot(2, 1, 'current task reply')
    expect(agent.currentResponse.value).toBe('current task reply')
    await nextSnapshot(3, 2, 'new current task reply')
    const visible = agent.messages.value.filter(item => item.messageId?.startsWith('runner-A:history:artifacts:'))
    expect(visible).toHaveLength(100)
    expect(visible.some(item => item.messageId === archive(0).message_id)).toBe(false)
    expect(visible.some(item => item.messageId === archive(199).message_id)).toBe(true)
    expect(agent.currentResponse.value).toBe('new current task reply')
  })

  it('failed older-page reads preserve the window, live updates do not move it, and latest is explicit', async () => {
    await accept()
    const latest = Array.from({ length: 100 }, (_, index) => archive(index + 100))
    const older = Array.from({ length: 100 }, (_, index) => archive(index))
    const newest = Array.from({ length: 100 }, (_, index) => archive(index + 200))
    external.history.mockResolvedValueOnce(page(latest, '101', true))
      .mockRejectedValueOnce(new Error('fixture old-page outage'))
      .mockResolvedValueOnce(page(older, '1', false))
      .mockResolvedValueOnce(page(newest, '201', true))
    await snapshot(2, 1, 'current response')
    agent.messages.value.push({ role: 'user', content: 'keep this transient draft', messageId: 'pending-input', clientRequestId: 'pending-key' })
    await agent.loadDurableHistory('older'); await flush()
    expect(agent.durableHistory.value?.error).toBeTruthy()
    expect(agent.durableHistory.value?.nextCursor).toBe('101')
    expect(agent.messages.value.some(item => item.messageId === archive(199).message_id)).toBe(true)
    await agent.loadDurableHistory('older'); await flush()
    expect(external.history.mock.calls[2][1]).toMatchObject({ runner_id: 'runner-A', before_id: '101' })
    expect(agent.durableHistory.value?.older).toBe(true)
    await nextSnapshot(3, 2, 'latest reply while reading history')
    expect(external.history).toHaveBeenCalledTimes(3)
    expect(agent.messages.value.some(item => item.messageId === archive(0).message_id)).toBe(true)
    expect(agent.messages.value.some(item => item.messageId === archive(199).message_id)).toBe(false)
    expect(agent.currentResponse.value).toBe('latest reply while reading history')
    await agent.loadDurableHistory('latest'); await flush()
    expect(external.history.mock.calls[3][1]).toMatchObject({ runner_id: 'runner-A', before_id: '0' })
    expect(agent.durableHistory.value?.older).toBe(false)
    expect(agent.messages.value.filter(item => item.messageId?.startsWith('runner-A:history:artifacts:'))).toHaveLength(100)
    expect(agent.messages.value.some(item => item.messageId === archive(299).message_id)).toBe(true)
    expect(agent.messages.value.some(item => item.messageId === archive(0).message_id)).toBe(false)
    expect(agent.messages.value.some(item => item.messageId === 'pending-input')).toBe(true)
  })
})
