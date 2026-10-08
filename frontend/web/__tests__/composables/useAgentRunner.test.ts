import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { controlledRunnerFetch } from '../mocks/runnerFetch'
import { runnerView } from '../mocks/runnerView'
import type { RunnerView } from '@/api/runner'

const external = vi.hoisted(() => ({ token: 'fictional-A', video: { value: { duration_sec: 10 } }, legacy: vi.fn(), history: vi.fn() }))
vi.mock('@/api/agent', () => ({ SSEManager: class { connect = external.legacy; disconnect() {} }, uploadFile: vi.fn() }))
vi.mock('@/api/session', () => ({ getSessionMessages: external.history }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer ' + external.token }) }) }))
vi.mock('@/composables/useVideoGenParams', () => ({ useVideoGenParams: () => ({ params: external.video }) }))

describe('Existing useAgent public interface with durable Runner transport', () => {
  let network: ReturnType<typeof controlledRunnerFetch>
  let agent: ReturnType<typeof import('@/composables/useAgent').useAgent>
  const flush = async () => { for (let index = 0; index < 16; index++) await Promise.resolve() }
  function request(path: string, method = 'GET') {
    const found = network.pending.find(item => new URL(item.url).pathname === '/api/chat' + path && item.method === method)
    expect(found, 'Expected controlled external request: ' + method + path).toBeDefined()
    return found!
  }
  function capability(enabled = true, observe = true) {
    request('/runners/capabilities').respond({ web_enabled: enabled, observe_existing: observe, contract_version: 1, transport: 'runner_poll' })
  }
  async function send(sid = 'A', row: RunnerView = runnerView()) {
    const task = agent.sendMessage('original input', 'video-agent', sid)
    await flush(); capability(); await flush()
    if (network.pending.some(item => new URL(item.url).pathname === '/api/chat/sessions/' + sid + '/runners')) {
      request('/sessions/' + sid + '/runners').respond({ runners: [], active_runners: [], has_more: false, next_cursor: null })
      await flush()
    }
    const submitted = request('/runners', 'POST')
    row.client_request_id = JSON.parse(submitted.body!).client_request_id
    submitted.respond({ success: true, runner: row }, 202)
    await task; await flush()
  }
  async function emptySwitch(sid: string) {
    const task = agent.switchSession(sid)
    await flush(); capability(false, false); await task; await flush()
  }
  beforeEach(async () => {
    vi.resetModules(); vi.useFakeTimers()
    external.token = 'fictional-A'; external.video.value = { duration_sec: 10 }
    external.legacy.mockReset(); external.history.mockReset().mockResolvedValue({ messages: [] })
    network = controlledRunnerFetch(); vi.stubGlobal('fetch', network.fetch)
    agent = (await import('@/composables/useAgent')).useAgent()
    agent.sessionId.value = 'A'
  })
  afterEach(async () => { agent.clearSessionCache(); network.detachOutstanding(); await flush(); vi.unstubAllGlobals(); vi.useRealTimers() })

  it('lost acceptance retries the exact original key/video/payload without capability re-selection or legacy execution', async () => {
    const task = agent.sendMessage('original input', 'video-agent', 'A')
    await flush(); capability(); await flush()
    request('/sessions/A/runners').respond({ runners: [], active_runners: [], has_more: false, next_cursor: null })
    await flush()
    const first = request('/runners', 'POST')
    const original = first.body
    first.reject(new TypeError('fictional lost response')); await task
    external.video.value = { duration_sec: 15 }
    await vi.advanceTimersByTimeAsync(2000)
    const retried = request('/runners', 'POST')
    expect(retried.body).toBe(original)
    expect(JSON.parse(retried.body!).video_params.duration_sec).toBe(10)
    expect(network.requests.filter(item => item.url.endsWith('/capabilities'))).toHaveLength(1)
    retried.respond({ success: true, runner: runnerView() }, 202); await flush()
    expect(external.legacy).not.toHaveBeenCalled()
    expect(agent.isSessionRunning('A')).toBe(true)
  })

  it('cumulative responses replace text and progress instead of appending duplicate snapshots', async () => {
    await send()
    const progress = [{ type: 'tool_start', toolName: 'read', toolCallId: 'call-1', toolArgs: {} }]
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ view_revision: 2, snapshot: { output: 'cumulative first', progressMessages: progress } }) })
    await flush(); await vi.advanceTimersByTimeAsync(1000)
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ view_revision: 3, snapshot: { output: 'cumulative first and second', progressMessages: progress } }) })
    await flush()
    expect(agent.currentResponse.value).toBe('cumulative first and second')
    expect(agent.messages.value).toHaveLength(2)
    expect(agent.progressMessages.value).toHaveLength(1)
  })

  it('background completion stays in its own session, shows unread completion, and does not block another session', async () => {
    await send()
    await emptySwitch('B')
    await send('B', runnerView({ runner_id: 'runner-B', client_request_id: 'request-B', session: { kind: 'web', session_id: 'B' } }))
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ status: 'completed', view_revision: 2, result: { status: 'completed', output: 'A finished' } }) })
    await flush()
    expect(agent.messages.value.map(item => item.content)).not.toContain('A finished')
    expect(agent.hasSessionUnreadCompletion('A')).toBe(true)
    expect(agent.isSessionRunning('B')).toBe(true)
    const returning = agent.switchSession('A'); await flush()
    if (network.pending.some(item => item.url.endsWith('/capabilities'))) { capability(false, true); await flush() }
    if (network.pending.some(item => item.url.includes('/sessions/A/runners'))) {
      request('/sessions/A/runners').respond({ runners: [runnerView({ status: 'completed', view_revision: 2,
        result: { status: 'completed', output: 'A finished' } })], active_runners: [], has_more: false, next_cursor: null })
    }
    await returning
    expect(agent.messages.value.map(item => item.content)).toContain('A finished')
    expect(agent.hasSessionUnreadCompletion('A')).toBe(false)
  })

  it('cancel failure retains running state and successful control waits for durable cancellation', async () => {
    await send()
    const failed = agent.abortStreaming('A'); await flush()
    request('/runners/runner-A/cancel', 'POST').respond({ code: 'RUNNER_SERVICE_UNAVAILABLE', error: 'fictional service unavailable' }, 503)
    await failed
    expect(agent.isSessionRunning('A')).toBe(true)
    const accepted = agent.abortStreaming('A'); await flush()
    request('/runners/runner-A/cancel', 'POST').respond({ success: true, runner: runnerView({ cancel_requested: true, view_revision: 2 }) })
    await accepted
    expect(agent.isSessionRunning('A')).toBe(true)
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ status: 'cancelled', cancel_requested: true, view_revision: 3 }) })
    await flush()
    expect(agent.isSessionRunning('A')).toBe(false)
    expect(agent.messages.value[1].cancelled).toBe(true)
  })

  it('detaching all page observations issues no cancel and stale observations cannot change the next authenticated session', async () => {
    await send()
    const old = request('/runners/runner-A')
    external.token = 'fictional-B'
    await emptySwitch('B')
    expect(old.aborted).toBe(true)
    expect(agent.messages.value).toEqual([])
    await vi.advanceTimersByTimeAsync(5000)
    expect(network.requests.filter(item => item.url.endsWith('/cancel'))).toEqual([])
    expect(network.requests.filter(item => item.url.endsWith('/runner-A'))).toHaveLength(1)
  })

  it('refresh merges recent terminal and independently active rows with stable IDs, retaining queued cancelled input', async () => {
    external.history.mockResolvedValue({ messages: [{ message_id: 'runner-A:user', role: 'user', content: 'original input', created_at: '2026-10-01T12:00:05Z',
      metadata: { runner_id: 'runner-A', runner_queue_order: 1, accepted_at: '2026-10-01T12:00:00Z' } }] })
    const task = agent.switchSession('A'); await flush(); capability(false, true); await flush()
    const completed = runnerView({ status: 'completed', result: { status: 'completed', output: 'A durable result' } })
    const cancelled = runnerView({ runner_id: 'runner-B', client_request_id: 'request-B', queue_order: 2, status: 'cancelled',
      snapshot: { input: { message_id: 'runner-B:user', text: 'cancelled queued input', attachments: [] } } })
    const active = runnerView({ runner_id: 'runner-C', client_request_id: 'request-C', queue_order: 3 })
    request('/sessions/A/runners').respond({ runners: [completed, cancelled], active_runners: [active], has_more: false, next_cursor: null })
    await task; await flush()
    expect(agent.messages.value).toHaveLength(6)
    expect(agent.messages.value.filter(item => item.messageId === 'runner-A:user')).toHaveLength(1)
    expect(agent.messages.value.map(item => item.content)).toContain('cancelled queued input')
    expect(agent.isSessionRunning('A')).toBe(true)
    expect(request('/runners/runner-C')).toBeDefined()
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('discovery failure can recover after switching away and back instead of trusting a completed history load', async () => {
    const first = agent.switchSession('A'); await flush(); capability(false, true); await flush()
    const failed = expect(first).rejects.toThrow('fictional discovery failure')
    request('/sessions/A/runners').respond({ error: 'fictional discovery failure', code: 'RUNNER_SERVICE_UNAVAILABLE' }, 503)
    await failed
    await emptySwitch('B')
    const recovered = agent.switchSession('A'); await flush()
    if (network.pending.some(item => item.url.endsWith('/capabilities'))) { capability(false, true); await flush() }
    request('/sessions/A/runners').respond({ runners: [runnerView()], active_runners: [runnerView()], has_more: false, next_cursor: null })
    await recovered
    expect(agent.isSessionRunning('A')).toBe(true)
    expect(request('/runners/runner-A')).toBeDefined()
  })

  it('sending during initial discovery waits for the owned parked runner then resumes its original input', async () => {
    const discovery = agent.switchSession('A'); await flush(); capability(false, true); await flush()
    const attachment = { file_id: 'fixture-file', name: 'kept.txt', size: 8, type: 'file' as const, mime_type: 'text/plain' }
    agent.currentFiles.value = [attachment]
    const sending = agent.sendMessage('keep this draft', null, 'A'); await flush()
    if (network.pending.some(item => item.url.endsWith('/capabilities'))) { capability(); await flush() }
    expect(network.requests.filter(item => item.method === 'POST')).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
    const paused = runnerView({ status: 'paused' })
    request('/sessions/A/runners').respond({ runners: [paused], active_runners: [paused], has_more: false, next_cursor: null })
    await discovery; await flush()
    const resumed = request('/runners/runner-A/controls', 'POST')
    const body = JSON.parse(resumed.body!)
    expect(body).toEqual({ client_request_id: expect.any(String), action: 'resume', answer: 'keep this draft', attachments: [attachment] })
    resumed.respond({ success: true, created: true, control: { control_id: 'discovered-resume', runner_id: 'runner-A',
      action: 'resume', client_request_id: body.client_request_id, status: 'accepted' }, runner: runnerView({ status: 'paused', resume_requested: true }) }, 202)
    await sending; await flush()
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('failed initial runner discovery blocks submission while preserving input and attachments for retry', async () => {
    const discovery = agent.switchSession('A'); await flush(); capability(false, true); await flush()
    const attachment = { file_id: 'fixture-file', name: 'kept.txt', size: 8, type: 'file' as const, mime_type: 'text/plain' }
    agent.currentFiles.value = [attachment]
    const sending = agent.sendMessage('do not lose this draft', null, 'A'); await flush()
    if (network.pending.some(item => item.url.endsWith('/capabilities'))) { capability(); await flush() }
    const failed = expect(discovery).rejects.toThrow('fictional discovery unavailable')
    request('/sessions/A/runners').respond({ code: 'RUNNER_SERVICE_UNAVAILABLE', error: 'fictional discovery unavailable' }, 503)
    await failed
    const disposition = await sending; await flush()
    expect(disposition).toMatchObject({ restoreInput: true })
    expect(disposition?.canRestore()).toBe(true)
    expect(agent.currentFiles.value).toEqual([attachment])
    expect(network.requests.filter(item => item.method === 'POST')).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
    expect(agent.error.value).toBeTruthy()
  })

  it('a late completed discovery row cannot clear the guard while a new send is selecting transport', async () => {
    const discovery = agent.switchSession('A'); await flush(); capability(true, true); await flush()
    const selected = agent.sendMessage('new intent', null, 'A'); await flush()
    request('/sessions/A/runners').respond({ runners: [runnerView({ status: 'completed', result: { status: 'completed', output: 'old result' } })],
      active_runners: [], has_more: false, next_cursor: null })
    await discovery; await flush()
    const duplicate = agent.sendMessage('must not be accepted concurrently', null, 'A'); await flush()
    expect(network.requests.filter(item => item.url.endsWith('/capabilities'))).toHaveLength(1)
    const accepted = request('/runners', 'POST')
    accepted.respond({ success: true, runner: runnerView({ runner_id: 'runner-new', queue_order: 2,
      client_request_id: JSON.parse(accepted.body!).client_request_id }) }, 202)
    await selected; await duplicate; await flush()
    expect(network.requests.filter(item => item.url.endsWith('/runners') && item.method === 'POST')).toHaveLength(1)
  })

  it.each(['capabilities', 'list', 'cancel'])('a completed %s HTTP response released across logout cannot submit or reopen old observers', async boundary => {
    let task: ReturnType<typeof agent.sendMessage> | ReturnType<typeof agent.switchSession> | ReturnType<typeof agent.abortStreaming>
    if (boundary === 'capabilities') {
      task = agent.sendMessage('old subject input', null, 'A'); await flush()
      capability()
    } else if (boundary === 'list') {
      task = agent.switchSession('A'); await flush(); capability(false, true); await flush()
      request('/sessions/A/runners').respond({ runners: [runnerView()], active_runners: [], has_more: false, next_cursor: null })
    } else {
      await send()
      task = agent.abortStreaming('A'); await flush()
      request('/runners/runner-A/cancel', 'POST').respond({ success: true, runner: runnerView({ cancel_requested: true, view_revision: 2 }) })
    }
    // The response arrived, but response.json/await continuation has not. This
    // models an ordinary HTTP completion race; fetch/core are not replaced.
    const before = network.requests.length
    agent.clearSessionCache()
    external.token = 'fictional-B'
    await task; await flush(); await vi.advanceTimersByTimeAsync(5000)
    expect(network.requests).toHaveLength(before)
    expect(agent.messages.value).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('stop before acceptance invalidates local selection without issuing legacy cancel or a late submission', async () => {
    const selected = agent.sendMessage('not accepted yet', null, 'A'); await flush()
    await agent.abortStreaming('A')
    capability(); await selected; await flush()
    expect(network.requests).toHaveLength(1)
    expect(network.requests[0].url).toContain('/capabilities')
    expect(agent.isSessionRunning('A')).toBe(false)
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('invalid capabilities cannot select legacy transport or submit a runner', async () => {
    const selection = agent.sendMessage('original input', null, 'A'); await flush()
    request('/runners/capabilities').respond({})
    const disposition = await selection
    expect(disposition).toMatchObject({ restoreInput: true })
    expect(disposition?.canRestore()).toBe(true)
    expect(external.legacy).not.toHaveBeenCalled()
    expect(network.requests).toHaveLength(1)
    expect(agent.isSessionRunning('A')).toBe(false)
    expect(agent.error.value).toContain('暂时无法确认原任务状态')
  })

  async function discoverParked(row: RunnerView) {
    const loading = agent.switchSession('A')
    await flush(); capability(false, true); await flush()
    request('/sessions/A/runners').respond({ runners: [row], active_runners: [row], has_more: false, next_cursor: null })
    await loading; await flush()
  }

  it.each(['paused', 'interrupted'] as const)('an owned %s runner resumes the original ID/key/input/attachments after lost control acceptance', async status => {
    await discoverParked(runnerView({ status }))
    const attachment = { file_id: 'fictional-kept-file', name: 'kept.txt', size: 4, type: 'file' as const, mime_type: 'text/plain' }
    agent.currentFiles.value = [attachment]
    const submitting = agent.sendMessage('new same-runner input', null, 'A'); await flush()
    const first = request('/runners/runner-A/controls', 'POST')
    const body = JSON.parse(first.body!)
    expect(body).toEqual({ client_request_id: expect.any(String), action: 'resume', answer: 'new same-runner input', attachments: [attachment] })
    first.reject(new TypeError('fictional accepted response lost')); await submitting
    agent.currentFiles.value = []
    external.video.value = { duration_sec: 15 }
    await vi.advanceTimersByTimeAsync(2000)
    const retry = request('/runners/runner-A/controls', 'POST')
    expect(retry.body).toBe(first.body)
    retry.respond({ success: true, created: false, control: { control_id: 'original-resume-control', runner_id: 'runner-A',
      action: 'resume', client_request_id: body.client_request_id, status: 'accepted' },
      runner: runnerView({ status, resume_requested: true, view_revision: 2, snapshot: { supplementalInputs: [{
        control_id: 'original-resume-control', client_request_id: body.client_request_id, message_id: 'original-resume-control:user',
        text: 'new same-runner input', accepted_at: '2026-10-02T00:00:00Z', attachments: [attachment],
      }] } }) }, 202)
    await flush()
    expect(agent.messages.value.filter(item => item.messageId === 'original-resume-control:user')).toHaveLength(1)
    request('/runners/runner-A/controls/original-resume-control').respond({ success: true, control: {
      control_id: 'original-resume-control', runner_id: 'runner-A', client_request_id: body.client_request_id,
      action: 'resume', status: 'consumed' } })
    await flush()
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('a browser assistance wait preserves input and does not pretend to reply to a clarification', async () => {
    await discoverParked(runnerView({ status: 'waiting', snapshot: { waiting: { kind: 'browser', wait_id: 'browser-wait' } } }))
    const before = network.requests.length
    expect(await agent.sendMessage('keep browser assistance answer', null, 'A')).toMatchObject({ restoreInput: true })
    expect(network.requests).toHaveLength(before)
    expect(agent.error.value).toContain('请先完成原任务的人工协助')
    expect(agent.error.value).toContain('本次输入已保留')
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('a nested child clarification retries the original reply key, wait and attachment after lost acceptance', async () => {
    const waiting = { kind: 'child_wait', child_wait: { kind: 'clarification', wait_id: 'original-wait', target_execution_id: 'original-child' } }
    await discoverParked(runnerView({ status: 'waiting', snapshot: { waiting } }))
    const attachment = { file_id: 'fixture-file', name: 'reply.txt', size: 9, type: 'file' as const, mime_type: 'text/plain' }
    agent.currentFiles.value = [attachment]
    const submitted = agent.sendMessage('original clarification answer', null, 'A')
    await flush()
    const first = request('/runners/runner-A/controls', 'POST')
    const payload = JSON.parse(first.body!)
    expect(payload).toEqual({ client_request_id: expect.any(String), action: 'reply', wait_id: 'original-wait',
      target_execution_id: 'original-child', answer: 'original clarification answer', attachments: [attachment] })
    first.reject(new TypeError('fictional response lost after accepted control')); await submitted
    external.video.value = { duration_sec: 15 }
    await vi.advanceTimersByTimeAsync(2000)
    const retry = request('/runners/runner-A/controls', 'POST')
    expect(retry.body).toBe(first.body)
    retry.respond({ success: true, created: false, control: { control_id: 'original-control', runner_id: 'runner-A', action: 'reply', client_request_id: payload.client_request_id, status: 'accepted' },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 2, snapshot: { waiting, supplementalInputs: [{
        control_id: 'original-control', client_request_id: payload.client_request_id, message_id: 'original-control:user',
        text: 'original clarification answer', wait_id: 'original-wait', accepted_at: '2026-10-02T00:00:00Z', attachments: [attachment],
      }] } }) }, 202)
    await flush()
    expect(agent.messages.value.filter(item => item.messageId === 'original-control:user')).toHaveLength(1)
    expect(agent.messages.value.filter(item => item.messageId === 'original-control:user')[0].content).toBe('original clarification answer')
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toHaveLength(0)
    expect(external.legacy).not.toHaveBeenCalled()
    request('/runners/runner-A/controls/original-control').respond({ success: true, control: {
      control_id: 'original-control', runner_id: 'runner-A', client_request_id: payload.client_request_id,
      action: 'reply', status: 'rejected', error_code: 'RECOVERY_PROFILE_CHANGED' } })
    await flush()
    expect(agent.error.value).toContain('尚未应用')
    expect(agent.messages.value.filter(item => item.messageId === 'original-control:user')).toHaveLength(1)
    const controlReads = network.requests.filter(item => item.url.endsWith('/controls/original-control')).length
    await vi.advanceTimersByTimeAsync(5000)
    expect(network.requests.filter(item => item.url.endsWith('/controls/original-control'))).toHaveLength(controlReads)
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toHaveLength(0)
  })

  it('a late accepted reply across logout cannot reopen the old runner observation', async () => {
    await discoverParked(runnerView({ status: 'waiting', snapshot: { waiting: {
      kind: 'clarification', wait_id: 'original-wait', target_execution_id: 'original-child' } } }))
    const sending = agent.sendMessage('old subject answer', null, 'A'); await flush()
    const pending = request('/runners/runner-A/controls', 'POST')
    pending.respond({ success: true, control: { control_id: 'old-control', runner_id: 'runner-A', client_request_id: JSON.parse(pending.body!).client_request_id,
      action: 'reply', status: 'accepted' }, runner: runnerView({ resume_requested: true }) }, 202)
    // HTTP has resolved, but its JSON/await continuation is still queued. An
    // aborted fetch is tested elsewhere and cannot be fulfilled a second time.
    const before = network.requests.length
    agent.clearSessionCache(); external.token = 'fictional-B'
    await sending; await flush(); await vi.advanceTimersByTimeAsync(5000)
    expect(network.requests).toHaveLength(before)
    expect(agent.messages.value).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('restoring historical failed runner and rejected controls remains silent while preserving history', async () => {
    const historical = runnerView({ status: 'paused', snapshot: { supplementalInputs: [{
      control_id: 'historical-control', client_request_id: 'historical-key', message_id: 'historical-control:user',
      text: 'historical preserved input', accepted_at: '2026-10-01T12:00:00Z', attachments: [] }] } })
    const loading = agent.switchSession('A'); await flush(); capability(false, true); await flush()
    request('/sessions/A/runners').respond({ runners: [historical, runnerView({ runner_id: 'old-failed', status: 'failed', queue_order: 2 })],
      active_runners: [historical], has_more: false, next_cursor: null })
    await loading; await flush()
    expect(agent.runnerFeedback.value).toBeNull()
    request('/runners/runner-A/controls/historical-control').respond({ success: true, control: {
      control_id: 'historical-control', runner_id: 'runner-A', client_request_id: 'historical-key', action: 'resume',
      status: 'rejected', error_code: 'RECOVERY_PROFILE_CHANGED' } })
    await flush()
    expect(agent.runnerFeedback.value).toBeNull()
    expect(agent.messages.value.map(item => item.content)).toContain('historical preserved input')
  })

  it('each new same-session rejected action notifies once and restores its own draft attachment', async () => {
    await discoverParked(runnerView({ status: 'paused' }))
    let previousFeedback = agent.runnerFeedback.value
    for (const index of [1, 2]) {
      const attachment = { file_id: 'fixture-draft-' + index, name: 'kept.txt', size: 4, type: 'file' as const, mime_type: 'text/plain' }
      agent.currentFiles.value = [attachment]
      const sending = agent.sendMessage('preserve rejected draft ' + index, null, 'A'); await flush()
      const pending = request('/runners/runner-A/controls', 'POST')
      const key = JSON.parse(pending.body!).client_request_id
      pending.respond({ success: true, control: { control_id: 'rejected-' + index, runner_id: 'runner-A',
        client_request_id: key, action: 'resume', status: 'rejected', error_code: 'RECOVERY_PROFILE_CHANGED' },
        runner: runnerView({ status: 'paused', view_revision: index + 1 }) }, 202)
      expect(await sending).toMatchObject({ restoreInput: true }); await flush()
      expect(agent.currentFiles.value).toEqual([attachment])
      const feedback = agent.runnerFeedback.value
      expect(feedback?.message).toContain('尚未应用'); expect(feedback?.canPresent()).toBe(true)
      expect(feedback).not.toBe(previousFeedback); previousFeedback = feedback
      await vi.advanceTimersByTimeAsync(1000)
      const polling = network.pending.find(item => new URL(item.url).pathname === '/api/chat/runners/runner-A')
      polling?.respond({ success: true, runner: runnerView({ status: 'paused', view_revision: index + 1 }) })
      await flush(); expect(agent.runnerFeedback.value).toBe(feedback)
    }
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('lost control acceptance recovered through snapshot still notifies a first rejected receipt without re-submission', async () => {
    await discoverParked(runnerView({ status: 'paused' }))
    const sending = agent.sendMessage('uncertain original input', null, 'A'); await flush()
    const posted = request('/runners/runner-A/controls', 'POST')
    const key = JSON.parse(posted.body!).client_request_id
    posted.reject(new TypeError('fictional response lost')); await sending
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ status: 'paused', view_revision: 2,
      snapshot: { supplementalInputs: [{ control_id: 'recovered-control', client_request_id: key,
        message_id: 'recovered-control:user', text: 'uncertain original input', accepted_at: '2026-10-01T12:00:00Z', attachments: [] }] } }) })
    await flush()
    request('/runners/runner-A/controls/recovered-control').respond({ success: true, control: {
      control_id: 'recovered-control', runner_id: 'runner-A', client_request_id: key, action: 'resume',
      status: 'rejected', error_code: 'RECOVERY_PROFILE_CHANGED' } })
    await flush()
    expect(agent.runnerFeedback.value?.message).toContain('尚未应用')
    const feedback = agent.runnerFeedback.value
    await vi.advanceTimersByTimeAsync(5000)
    expect(agent.runnerFeedback.value).toBe(feedback)
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/controls'))).toHaveLength(1)
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('a rejected old-session control cannot restore files or feedback into a different session draft', async () => {
    await discoverParked(runnerView({ status: 'paused' }))
    const oldAttachment = { file_id: 'fixture-old-draft', name: 'old.txt', size: 4, type: 'file' as const, mime_type: 'text/plain' }
    agent.currentFiles.value = [oldAttachment]
    const sending = agent.sendMessage('old session input', null, 'A'); await flush()
    const pending = request('/runners/runner-A/controls', 'POST')
    const key = JSON.parse(pending.body!).client_request_id
    await emptySwitch('B')
    const newAttachment = { ...oldAttachment, file_id: 'fixture-new-draft', name: 'new.txt' }
    agent.currentFiles.value = [newAttachment]
    pending.respond({ success: true, control: { control_id: 'old-rejected', runner_id: 'runner-A', client_request_id: key,
      action: 'resume', status: 'rejected', error_code: 'RECOVERY_PROFILE_CHANGED' }, runner: runnerView({ status: 'paused', view_revision: 2 }) }, 202)
    const disposition = await sending; await flush()
    // canRestore protects auth/generation; the original ChatContainer additionally
    // checks currentSessionId===sending sid before writing any textarea draft.
    expect(disposition?.restoreInput).toBe(true)
    expect(agent.sessionId.value).toBe('B')
    expect(agent.currentFiles.value).toEqual([newAttachment])
    expect(agent.runnerFeedback.value?.canPresent() ?? false).toBe(false)
    expect(agent.messages.value.map(item => item.content)).not.toContain('old session input')
  })

  // ---- 提交失败明确提示（M7 待办①）：403/网络失败不再残留「🚀 正在发送请求...」----

  async function sendToSubmission(sid = 'A') {
    const task = agent.sendMessage('submit failure probe', null, sid)
    await flush(); capability(); await flush()
    if (network.pending.some(item => new URL(item.url).pathname === '/api/chat/sessions/' + sid + '/runners')) {
      request('/sessions/' + sid + '/runners').respond({ runners: [], active_runners: [], has_more: false, next_cursor: null })
      await flush()
    }
    return { task, submitted: request('/runners', 'POST') }
  }

  it('definitive credit-blocked submission marks an explicit error state, restores the draft, and never auto-retries', async () => {
    const attachment = { file_id: 'fixture-credit-file', name: 'kept.txt', size: 8, type: 'file' as const, mime_type: 'text/plain' }
    agent.currentFiles.value = [attachment]
    const { task, submitted } = await sendToSubmission()
    const original = submitted.body!
    submitted.respond({ code: 'NO_CREDIT', error: 'fictional credit exhausted' }, 403)
    const disposition = await task; await flush()
    expect(disposition).toMatchObject({ restoreInput: true })
    expect(disposition?.canRestore()).toBe(true)
    expect(agent.currentFiles.value).toEqual([attachment])
    const key = JSON.parse(original).client_request_id
    const optimistic = agent.messages.value.find(item => item.role === 'assistant' && item.clientRequestId === key)
    expect(optimistic?.submitFailure).toMatchObject({ kind: 'definitive', message: '积分不足，请充值后重试。' })
    const progress = optimistic?.progressMessages || []
    expect(progress.some(item => item.content === '🚀 正在发送请求...')).toBe(false)
    expect(progress.some(item => item.type === 'error' && item.content.includes('积分不足'))).toBe(true)
    await vi.advanceTimersByTimeAsync(60000)
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toHaveLength(1)
    expect(agent.isSessionRunning('A')).toBe(false)
  })

  it('manual retry after a definitive failure replays the exact original key/body and merges the accepted runner view', async () => {
    const { task, submitted } = await sendToSubmission()
    const original = submitted.body!
    submitted.respond({ code: 'NO_CREDIT', error: 'fictional credit gone' }, 403)
    await task; await flush()
    const key = JSON.parse(original).client_request_id
    const optimistic = () => agent.messages.value.find(item => item.role === 'assistant' && item.clientRequestId === key)!
    expect(optimistic().submitFailure?.kind).toBe('definitive')
    optimistic().submitFailure?.retry()
    await flush()
    const retried = request('/runners', 'POST')
    expect(retried.body).toBe(original)
    retried.respond({ success: true, runner: runnerView({ client_request_id: key }) }, 202)
    await flush()
    expect(optimistic().submitFailure).toBeUndefined()
    expect(optimistic().runnerId).toBe('runner-A')
    expect(agent.isSessionRunning('A')).toBe(true)
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('network-lost submission keeps the existing automatic retry chain and offers immediate retry on the same body', async () => {
    const { task, submitted } = await sendToSubmission()
    const original = submitted.body!
    submitted.reject(new TypeError('fictional network down'))
    await task; await flush()
    const key = JSON.parse(original).client_request_id
    const optimistic = () => agent.messages.value.find(item => item.role === 'assistant' && item.clientRequestId === key)!
    expect(optimistic().submitFailure).toMatchObject({ kind: 'retrying', attempts: 1 })
    expect((optimistic().progressMessages || []).some(item => item.content.includes('自动重试'))).toBe(true)
    await vi.advanceTimersByTimeAsync(2000)
    const auto = request('/runners', 'POST')
    expect(auto.body).toBe(original)
    auto.reject(new TypeError('fictional network down again'))
    await flush()
    expect(optimistic().submitFailure?.attempts).toBe(2)
    optimistic().submitFailure?.retry()
    await flush()
    const immediate = request('/runners', 'POST')
    expect(immediate.body).toBe(original)
    immediate.respond({ success: true, runner: runnerView({ client_request_id: key }) }, 202)
    await flush()
    expect(optimistic().submitFailure).toBeUndefined()
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('an automatic retry chain that later receives a definitive rejection stops with the error state', async () => {
    const { task, submitted } = await sendToSubmission()
    submitted.reject(new TypeError('fictional first loss'))
    await task; await flush()
    await vi.advanceTimersByTimeAsync(2000)
    const retry = request('/runners', 'POST')
    retry.respond({ code: 'NO_CREDIT', error: 'fictional late credit exhaustion' }, 403)
    await flush()
    const key = JSON.parse(retry.body!).client_request_id
    const optimistic = agent.messages.value.find(item => item.role === 'assistant' && item.clientRequestId === key)
    expect(optimistic?.submitFailure).toMatchObject({ kind: 'definitive', message: '积分不足，请充值后重试。' })
    expect(agent.isSessionRunning('A')).toBe(false)
    await vi.advanceTimersByTimeAsync(60000)
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toHaveLength(2)
  })

  it('manual retry never overwrites a newer in-flight submission key after a definitive failure', async () => {
    const { task, submitted } = await sendToSubmission()
    submitted.respond({ code: 'NO_CREDIT', error: 'fictional credit gone' }, 403)
    await task; await flush()
    const failedKey = JSON.parse(submitted.body!).client_request_id
    const failedMessage = () => agent.messages.value.find(item => item.role === 'assistant' && item.clientRequestId === failedKey)!
    expect(failedMessage().submitFailure?.kind).toBe('definitive')
    // 失败后用户发送新消息：新 key 提交在飞行中，占据 pendingRunner 与并发 guard
    const second = agent.sendMessage('newer input', null, 'A')
    await flush(); capability(); await flush()
    const newer = request('/runners', 'POST')
    expect(JSON.parse(newer.body!).client_request_id).not.toBe(failedKey)
    // 旧失败消息的「重试」不得覆盖新提交的键（abortStreaming 同 key 重放互斥），也不得发起第三个 POST
    failedMessage().submitFailure?.retry()
    await flush()
    expect(network.requests.filter(item => item.method === 'POST' && item.url.endsWith('/runners'))).toHaveLength(2)
    expect(failedMessage().submitFailure?.kind).toBe('definitive')
    newer.respond({ success: true, runner: runnerView({ client_request_id: JSON.parse(newer.body!).client_request_id }) }, 202)
    await second; await flush()
    expect(external.legacy).not.toHaveBeenCalled()
  })
})
