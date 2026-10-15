import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { controlledRunnerFetch } from '../mocks/runnerFetch'
import { runnerView } from '../mocks/runnerView'
import type { RunnerView } from '@/api/runner'

const external = vi.hoisted(() => ({ token: 'fictional-A', video: { value: { duration_sec: 10 } }, legacy: vi.fn(), history: vi.fn(), session: vi.fn(), credit: vi.fn(), preferences: vi.fn() }))
vi.mock('@/api/agent', () => ({ SSEManager: class { connect = external.legacy; disconnect() {} }, uploadFile: vi.fn() }))
vi.mock('@/api/session', () => ({ getSessionMessages: external.history, getSession: external.session, updateFailedMessageUI: external.preferences }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer ' + external.token }) }) }))
vi.mock('@/composables/useVideoGenParams', () => ({ useVideoGenParams: () => ({ params: external.video }) }))
vi.mock('@/composables/useCreditCheck', () => ({ useCreditCheck: () => ({ checkCreditBeforeAction: external.credit }) }))

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
    external.session.mockReset().mockResolvedValue({ session_id: 'A', subagent_id: 'original-agent', instance_id: 'original-instance' })
    external.credit.mockReset().mockResolvedValue({ allowed: true })
    external.preferences.mockReset().mockResolvedValue(undefined)
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

  it('execution failure shows on the original input; retry uses a new key, original files/profile and preserves the attachment draft', async () => {
    const originalFile = { file_id: 'original-file', name: 'original.txt', size: 1, type: 'file' as const, mime_type: 'text/plain' }
    const row = runnerView({ status: 'failed', profile_id: 'original-agent',
      snapshot: { input: { message_id: 'runner-A:user', text: '上海的', attachments: [originalFile] } },
      result: { status: 'failed', output: '', error_code: 'EXECUTION_FAILED' } })
    await send('A', row)
    expect(agent.messages.value).toHaveLength(1)
    const failed = agent.messages.value[0]
    expect(failed.runnerFailure?.retry).toBeTypeOf('function')
    const draft = { ...originalFile, file_id: 'draft-file', name: 'draft.txt' }
    agent.currentFiles.value = [draft]
    failed.runnerFailure?.retry?.()
    failed.runnerFailure?.retry?.()
    await flush(); capability(); await flush()
    const retry = request('/runners', 'POST')
    const payload = JSON.parse(retry.body!)
    expect(payload).toMatchObject({ message: '上海的', subagent: 'original-agent', instance_id: 'original-instance', files: [originalFile] })
    expect(payload.client_request_id).not.toBe(row.client_request_id)
    expect(external.session).toHaveBeenCalledTimes(1)
    expect(agent.currentFiles.value).toEqual([draft])
    retry.reject(new TypeError('lost retry acceptance')); await flush()
    expect(agent.messages.value.find(message => message.role === 'user' && message.clientRequestId === payload.client_request_id)?.submitFailure).toBeDefined()
    await vi.advanceTimersByTimeAsync(2000)
    const replay = request('/runners', 'POST')
    expect(replay.body).toBe(retry.body)
    replay.respond({ success: true, runner: runnerView({ runner_id: 'runner-retry', client_request_id: payload.client_request_id, queue_order: 2 }) }, 202)
    await flush()
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('historical failed tasks rebuild retry controls and stale callbacks cannot send after switching sessions or accounts', async () => {
    const loading = agent.switchSession('A'); await flush(); capability(); await flush()
    request('/sessions/A/runners').respond({ runners: [runnerView({ status: 'failed' })], active_runners: [], has_more: false, next_cursor: null })
    await loading; await flush()
    expect(agent.messages.value).toHaveLength(1)
    const retry = agent.messages.value[0].runnerFailure?.retry
    agent.sessionId.value = 'B'
    retry?.(); await flush()
    expect(external.session).not.toHaveBeenCalled()
    agent.sessionId.value = 'A'; external.token = 'different-account'
    retry?.(); await flush()
    expect(external.session).not.toHaveBeenCalled()
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(0)
  })

  it('a successful retry removes only its source and refresh reconstructs the same result from persisted links', async () => {
    const first = runnerView({ status: 'failed' })
    await send('A', first)
    const staleRetry = agent.messages.value[0].runnerFailure?.retry
    staleRetry?.(); await flush(); capability(); await flush()
    const retry = request('/runners', 'POST')
    const key = JSON.parse(retry.body!).client_request_id
    expect(external.preferences).toHaveBeenCalledWith('A', 'runner-A', { action: 'retry', client_request_id: key }, { Authorization: 'Bearer fictional-A' })
    expect(agent.messages.value.some(item => item.runnerFailure)).toBe(true)
    const retried = runnerView({ runner_id: 'retry-A', client_request_id: key, queue_order: 3 })
    retry.respond({ success: true, runner: retried }, 202); await flush()
    expect(agent.messages.value.find(item => item.runnerId === 'runner-A')?.runnerFailure?.retrying).toBe(true)
    const completed = { ...retried, status: 'completed' as const, settlement_status: 'settled' as const, view_revision: 2,
      result: { status: 'completed', output: '上海天气' } }
    request('/runners/retry-A').respond({ success: true, runner: completed }); await flush()
    expect(agent.messages.value.some(item => item.runnerId === 'runner-A')).toBe(false)
    staleRetry?.(); await flush()
    expect(external.preferences).toHaveBeenCalledTimes(1)
    agent.clearSessionCache('A')
    external.history.mockResolvedValue({ messages: [], failed_message_ui: { retry_sources: { [key]: 'runner-A' } } })
    const reloading = agent.switchSession('A'); await flush(); capability(); await flush()
    const other = runnerView({ runner_id: 'other-failed', client_request_id: 'other-key', status: 'failed', queue_order: 2 })
    request('/sessions/A/runners').respond({ runners: [first, other, completed], active_runners: [], has_more: false, next_cursor: null })
    await reloading; await flush()
    expect(agent.messages.value.some(item => item.runnerId === 'runner-A')).toBe(false)
    expect(agent.messages.value.filter(item => item.runnerFailure).map(item => item.runnerId)).toEqual(['other-failed'])
    expect(agent.messages.value.some(item => item.content === '上海天气')).toBe(true)
  })

  it('failed retry persistence prevents submission and leaves the original failed input and draft available', async () => {
    await send('A', runnerView({ status: 'failed' }))
    external.preferences.mockRejectedValueOnce(new Error('消息操作未成功，请稍后重试。'))
    agent.messages.value[0].runnerFailure?.retry?.(); await flush(); capability(); await flush()
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(agent.messages.value[0].runnerFailure?.retrying).toBe(false)
    expect(agent.isProcessing.value).toBe(false)
    expect(agent.error.value).toBe('消息操作未成功，请稍后重试。')
  })

  it('manual deletion persists before removing a failed input, keeps partial results and cannot be replayed from another account', async () => {
    await send('A', runnerView({ status: 'failed', result: { status: 'failed', output: '保留部分分析' } }))
    const input = agent.messages.value[0]
    const remove = input.runnerFailure?.remove
    let resolve!: () => void
    external.preferences.mockImplementationOnce(() => new Promise<void>(done => { resolve = done }))
    remove?.(); remove?.(); await flush()
    expect(input.runnerFailure?.deleting).toBe(true)
    expect(agent.messages.value).toHaveLength(2)
    expect(external.preferences).toHaveBeenCalledTimes(1)
    resolve(); await flush()
    expect(agent.messages.value.map(item => item.content)).toEqual(['保留部分分析'])
    agent.clearSessionCache('A')
    external.history.mockResolvedValue({ messages: [], failed_message_ui: { hidden_failed: { 'runner-A': true } } })
    const loading = agent.switchSession('A'); await flush(); capability(); await flush()
    request('/sessions/A/runners').respond({ runners: [runnerView({ status: 'failed' })], active_runners: [], has_more: false, next_cursor: null })
    await loading; await flush()
    expect(agent.messages.value).toEqual([])
    external.token = 'different-account'; remove?.(); await flush()
    expect(external.preferences).toHaveBeenCalledTimes(1)
  })

  it('a failed delete keeps the original message and reports that it was not removed', async () => {
    await send('A', runnerView({ status: 'failed' }))
    external.preferences.mockRejectedValueOnce(new Error('消息操作未成功，请稍后重试。'))
    agent.messages.value[0].runnerFailure?.remove?.(); await flush()
    expect(agent.messages.value[0].runnerFailure?.deleting).toBe(false)
    expect(agent.error.value).toBe('消息操作未成功，请稍后重试。')
  })

  it('deleting an earlier failed message cannot discard text from an active legacy response', async () => {
    await send('A', runnerView({ status: 'failed' }))
    const remove = agent.messages.value[0].runnerFailure?.remove
    const legacySend = agent.sendMessage('legacy next turn', null, 'A')
    await flush(); capability(false, true); await legacySend; await flush()
    const args = external.legacy.mock.calls[0]
    expect(args).toBeDefined()
    args[5]('第一段')
    expect(agent.messages.value[agent.messages.value.length - 1]?.content).toBe('第一段')
    remove?.(); await flush()
    args[5]('，第二段')
    args[6]()
    expect(agent.messages.value.map(item => item.content)).toEqual(['legacy next turn', '第一段，第二段'])
    expect(agent.isProcessing.value).toBe(false)
  })

  it('only definitive local rejection can be deleted; stale retry cannot submit a dismissed optimistic turn', async () => {
    const { task, submitted } = await sendToSubmission()
    submitted.respond({ code: 'NO_CREDIT', error: 'fictional error' }, 403)
    await task; await flush()
    const input = agent.messages.value.find(item => item.role === 'user')!
    const retry = input.submitFailure?.retry
    expect(input.submitFailure?.remove).toBeTypeOf('function')
    input.submitFailure?.remove?.(); retry?.(); await flush()
    expect(agent.messages.value).toEqual([])
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(external.preferences).not.toHaveBeenCalled()
  })

  it('leaving while retry loads the original session cannot send into a background session', async () => {
    await send('A', runnerView({ status: 'failed' }))
    let resolveSession!: (value: unknown) => void
    external.session.mockReturnValue(new Promise(resolve => { resolveSession = resolve }))
    agent.messages.value[0].runnerFailure?.retry?.()
    await flush()
    agent.sessionId.value = 'B'
    resolveSession({ session_id: 'A', subagent_id: 'original-agent' })
    await flush()
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(1)
  })

  it('retry cannot consume another in-flight turn or fall back to legacy transport', async () => {
    await send('A', runnerView({ status: 'failed' }))
    const retry = agent.messages.value[0].runnerFailure?.retry
    retry?.(); await flush(); capability(false, true); await flush()
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(external.legacy).not.toHaveBeenCalled()
    expect(agent.isProcessing.value).toBe(false)
    const next = agent.sendMessage('new turn', null, 'A')
    await flush()
    retry?.(); await flush()
    expect(external.session).toHaveBeenCalledTimes(1)
    capability(); await flush()
    const submitted = request('/runners', 'POST')
    submitted.respond({ success: true, runner: runnerView({ runner_id: 'runner-new', queue_order: 2,
      client_request_id: JSON.parse(submitted.body!).client_request_id }) }, 202)
    await next; await flush()
  })

  it('leaving during retry capabilities cancels the new send and releases its selection guard', async () => {
    await send('A', runnerView({ status: 'failed' }))
    const retry = agent.messages.value[0].runnerFailure?.retry
    retry?.(); await flush()
    agent.sessionId.value = 'B'
    capability(); await flush()
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(agent.isSessionRunning('A')).toBe(false)
    agent.sessionId.value = 'A'
    retry?.(); await flush(); capability(); await flush()
    const submitted = request('/runners', 'POST')
    submitted.respond({ success: true, runner: runnerView({ runner_id: 'runner-retry', queue_order: 2,
      client_request_id: JSON.parse(submitted.body!).client_request_id }) }, 202)
    await flush()
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(2)
  })

  it('leaving during retry credit checks cannot submit or consume attachment drafts', async () => {
    await send('A', runnerView({ status: 'failed' }))
    let resolveCredit!: (value: { allowed: boolean }) => void
    external.credit.mockReturnValue(new Promise(resolve => { resolveCredit = resolve }))
    const draft = { file_id: 'draft-file', name: 'draft.txt', size: 1, type: 'file' as const, mime_type: 'text/plain' }
    agent.currentFiles.value = [draft]
    window.history.replaceState({}, '', '/t/tenant-A/chat')
    try {
      agent.messages.value[0].runnerFailure?.retry?.(); await flush()
      agent.sessionId.value = 'B'
      resolveCredit({ allowed: true }); await flush()
      expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(1)
      expect(agent.currentFiles.value).toEqual([draft])
      expect(agent.isSessionRunning('A')).toBe(false)
    } finally {
      window.history.replaceState({}, '', '/')
    }
  })

  it('a delayed retry cannot release selection owned by another send while its credit check was pending', async () => {
    await send('A', runnerView({ status: 'failed' }))
    let resolveCredit!: (value: { allowed: boolean }) => void
    external.credit.mockImplementationOnce(() => new Promise(resolve => { resolveCredit = resolve }))
    window.history.replaceState({}, '', '/t/tenant-A/chat')
    try {
      agent.messages.value[0].runnerFailure?.retry?.(); await flush()
      const normal = agent.sendMessage('ordinary input', null, 'A'); await flush()
      agent.sessionId.value = 'B'
      resolveCredit({ allowed: true }); await flush()
      expect(agent.isSessionRunning('A')).toBe(true)
      agent.sessionId.value = 'A'
      await agent.sendMessage('must remain blocked', null, 'A'); await flush()
      capability(); await flush()
      const submitted = request('/runners', 'POST')
      expect(JSON.parse(submitted.body!).message).toBe('ordinary input')
      submitted.respond({ success: true, runner: runnerView({ runner_id: 'runner-ordinary', queue_order: 2,
        client_request_id: JSON.parse(submitted.body!).client_request_id }) }, 202)
      await normal; await flush()
      expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(2)
    } finally {
      window.history.replaceState({}, '', '/')
    }
  })

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
