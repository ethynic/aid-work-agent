import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { controlledRunnerFetch } from '../mocks/runnerFetch'
import { runnerView } from '../mocks/runnerView'
import type { AskForm } from '@/api/runner'

const external = vi.hoisted(() => ({ token: 'fictional-A', video: { value: { duration_sec: 10 } }, legacy: vi.fn(), history: vi.fn() }))
vi.mock('@/api/agent', () => ({ SSEManager: class { connect = external.legacy; disconnect() {} }, uploadFile: vi.fn() }))
vi.mock('@/api/session', () => ({ getSessionMessages: external.history }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer ' + external.token }) }) }))
vi.mock('@/composables/useVideoGenParams', () => ({ useVideoGenParams: () => ({ params: external.video }) }))

const form: AskForm = {
  form_id: 'frm_1', wait_id: 'wait-1', title: '归档范围', intro: '说明', allow_skip: true, expires_at: null,
  questions: [
    { id: 'q1', tag: '归档范围', prompt: '以下哪些任务归档？', type: 'multi', required: true,
      options: [{ key: 'q1o1', label: '任务一' }, { key: 'q1o2', label: '任务二' }] },
    { id: 'q2', tag: '补充', prompt: '补充说明', type: 'text', required: false },
  ],
}
const answers = [{ question_id: 'q1', selected_keys: ['q1o1'], text: '自定义说明' }]
const questions = [{ message_id: 'wait-1:assistant', wait_id: 'wait-1', text: '需要澄清', created_at: '2026-10-14T10:00:00Z' }]
const waitingRow = () => runnerView({ status: 'waiting',
  snapshot: { waiting: { kind: 'clarification', wait_id: 'wait-1', target_execution_id: 'exec-1', question: '需要澄清', form },
    clarificationQuestions: questions } })

describe('useAgent structured ask submission (WP4 提交链路)', () => {
  let network: ReturnType<typeof controlledRunnerFetch>
  let agent: ReturnType<typeof import('@/composables/useAgent').useAgent>
  const flush = async () => { for (let index = 0; index < 16; index++) await Promise.resolve() }
  function request(path: string, method = 'GET') {
    const found = network.pending.find(item => new URL(item.url).pathname === '/api/chat' + path && item.method === method)
    expect(found, 'Expected controlled external request: ' + method + path).toBeDefined()
    return found!
  }
  function postCount(path: string) {
    return network.requests.filter(item => item.method === 'POST' && new URL(item.url).pathname === '/api/chat' + path).length
  }
  function capability(structured: boolean) {
    request('/runners/capabilities').respond({ web_enabled: true, observe_existing: true, contract_version: 1, transport: 'runner_poll',
      ...(structured ? { ask_structured_answers: true } : {}) })
  }
  async function discoverParked(structured = true) {
    const loading = agent.switchSession('A')
    await flush(); capability(structured); await flush()
    request('/sessions/A/runners').respond({ runners: [waitingRow()], active_runners: [waitingRow()], has_more: false, next_cursor: null })
    await loading; await flush()
  }
  const askInput = { runnerId: 'runner-A', waitId: 'wait-1', targetExecutionId: 'exec-1', form, answers }
  const askControl = { control_id: 'ask-control', runner_id: 'runner-A', action: 'reply' as const, status: 'accepted' as const }

  beforeEach(async () => {
    vi.resetModules(); vi.useFakeTimers()
    external.token = 'fictional-A'; external.video.value = { duration_sec: 10 }
    external.legacy.mockReset(); external.history.mockReset().mockResolvedValue({ messages: [] })
    network = controlledRunnerFetch(); vi.stubGlobal('fetch', network.fetch)
    agent = (await import('@/composables/useAgent')).useAgent()
    agent.sessionId.value = 'A'
  })
  afterEach(async () => { agent.clearSessionCache(); network.detachOutstanding(); await flush(); vi.unstubAllGlobals(); vi.useRealTimers() })

  it('submits structured answers bound to the original wait without a canonical answer key', async () => {
    await discoverParked(true)
    const pending = agent.submitAskAnswers(askInput)
    await flush()
    const posted = request('/runners/runner-A/controls', 'POST')
    const body = JSON.parse(posted.body!)
    expect(body).toEqual({ client_request_id: expect.any(String), action: 'reply', wait_id: 'wait-1',
      target_execution_id: 'exec-1', answers })
    expect('answer' in body).toBe(false)
    const key = body.client_request_id
    posted.respond({ success: true, created: true, control: { ...askControl, client_request_id: key },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 2, snapshot: {
        waiting: { kind: 'clarification', wait_id: 'wait-1', target_execution_id: 'exec-1', question: '需要澄清', form },
        clarificationQuestions: questions,
        supplementalInputs: [{ control_id: 'ask-control', client_request_id: key, message_id: 'ask-control:user',
          text: 'Q1【归档范围】任务一；自定义：自定义说明', wait_id: 'wait-1', accepted_at: '2026-10-14T10:01:00Z', form, answers }] } }) }, 202)
    expect(await pending).toBe('accepted')
    await flush()
    expect(agent.askSubmissionFor('runner-A', 'wait-1').value).toMatchObject({ status: 'accepted', controlId: 'ask-control' })
    // 受理事实进入历史投影：只读已答卡片挂在答案消息上，卡片与原 wait 绑定
    const answered = agent.messages.value.find(item => item.messageId === 'ask-control:user')
    expect(answered?.answeredForm).toMatchObject({ controlId: 'ask-control', terminal: false })
    expect(agent.messages.value.find(item => item.messageId === 'wait-1:assistant')?.questionCard?.waitId).toBe('wait-1')
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('explicit skip submits skip=true with the bound wait and no answers key', async () => {
    await discoverParked(true)
    const pending = agent.submitAskAnswers({ ...askInput, skip: true, answers: undefined })
    await flush()
    const body = JSON.parse(request('/runners/runner-A/controls', 'POST').body!)
    expect(body).toEqual({ client_request_id: expect.any(String), action: 'reply', wait_id: 'wait-1',
      target_execution_id: 'exec-1', skip: true })
    expect('answers' in body).toBe(false)
    const posted = request('/runners/runner-A/controls', 'POST')
    posted.respond({ success: true, created: true, control: { ...askControl, client_request_id: body.client_request_id },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 2 }) }, 202)
    expect(await pending).toBe('accepted')
  })

  it('without ask_structured_answers capability the card falls back to the client canonical text path', async () => {
    await discoverParked(false)
    const pending = agent.submitAskAnswers(askInput)
    await flush()
    const body = JSON.parse(request('/runners/runner-A/controls', 'POST').body!)
    expect(body).toEqual({ client_request_id: expect.any(String), action: 'reply', wait_id: 'wait-1',
      target_execution_id: 'exec-1', answer: 'Q1【归档范围】任务一；自定义：自定义说明\nQ2【补充】（未回答）' })
    expect('answers' in body).toBe(false)
    expect('skip' in body).toBe(false)
    const posted = request('/runners/runner-A/controls', 'POST')
    posted.respond({ success: true, created: true, control: { ...askControl, client_request_id: body.client_request_id },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 2 }) }, 202)
    expect(await pending).toBe('accepted')
  })

  it('lost network result retries the exact original key and body without degrading to the text path', async () => {
    await discoverParked(true)
    const pending = agent.submitAskAnswers(askInput)
    await flush()
    const first = request('/runners/runner-A/controls', 'POST')
    first.reject(new TypeError('fictional lost response'))
    expect(await pending).toBe('retrying')
    await vi.advanceTimersByTimeAsync(2000)
    const retry = request('/runners/runner-A/controls', 'POST')
    expect(retry.body).toBe(first.body)
    retry.respond({ success: true, created: false, control: { ...askControl, client_request_id: JSON.parse(first.body!).client_request_id },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 2 }) }, 202)
    await flush()
    expect(agent.askSubmissionFor('runner-A', 'wait-1').value).toMatchObject({ status: 'accepted' })
  })

  it('422 form errors surface per-question for correction and the resubmit mints a new key', async () => {
    await discoverParked(true)
    const pending = agent.submitAskAnswers(askInput)
    await flush()
    const first = request('/runners/runner-A/controls', 'POST')
    const firstKey = JSON.parse(first.body!).client_request_id
    first.respond({ success: false, code: 'FORM_ANSWER_INVALID', error: 'FORM_ANSWER_INVALID',
      errors: [{ question_id: 'q1', code: 'FORM_ANSWER_INVALID', message: '该题为必答' }] }, 422)
    expect(await pending).toBe('validation')
    expect(agent.askSubmissionFor('runner-A', 'wait-1').value).toMatchObject({ status: 'error',
      errors: [{ question_id: 'q1', code: 'FORM_ANSWER_INVALID', message: '该题为必答' }] })
    // 校验失败修正后直接重提（无专门键管理）：新键新 POST
    const second = agent.submitAskAnswers(askInput)
    await flush()
    const retryPost = request('/runners/runner-A/controls', 'POST')
    expect(JSON.parse(retryPost.body!).client_request_id).not.toBe(firstKey)
    retryPost.respond({ success: true, created: true, control: { ...askControl, client_request_id: JSON.parse(retryPost.body!).client_request_id },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 3 }) }, 202)
    expect(await second).toBe('accepted')
  })

  it('stale 409 keeps the original wait binding, surfaces the code and refreshes the snapshot instead of retargeting', async () => {
    await discoverParked(true)
    const getsBefore = network.requests.filter(item => item.method === 'GET' && new URL(item.url).pathname === '/api/chat/runners/runner-A').length
    const pending = agent.submitAskAnswers(askInput)
    await flush()
    request('/runners/runner-A/controls', 'POST').respond({ code: 'CONTROL_WAIT_STALE', error: 'CONTROL_WAIT_STALE' }, 409)
    expect(await pending).toBe('stale')
    expect(agent.askSubmissionFor('runner-A', 'wait-1').value).toMatchObject({ status: 'error', errorCode: 'CONTROL_WAIT_STALE' })
    await flush()
    const getsAfter = network.requests.filter(item => item.method === 'GET' && new URL(item.url).pathname === '/api/chat/runners/runner-A').length
    expect(getsAfter).toBeGreaterThan(getsBefore)
    // 未改投：只发过一次 controls POST
    expect(postCount('/runners/runner-A/controls')).toBe(1)
  })

  it('RUNNER_RESUME_PENDING and closed codes return distinct outcomes without a second POST', async () => {
    await discoverParked(true)
    const pending = agent.submitAskAnswers(askInput)
    await flush()
    request('/runners/runner-A/controls', 'POST').respond({ code: 'RUNNER_RESUME_PENDING', error: 'RUNNER_RESUME_PENDING' }, 409)
    expect(await pending).toBe('resume-pending')
    const closed = agent.submitAskAnswers(askInput)
    await flush()
    request('/runners/runner-A/controls', 'POST').respond({ code: 'CONTROL_EXECUTION_CLOSED', error: 'CONTROL_EXECUTION_CLOSED' }, 409)
    expect(await closed).toBe('closed')
    expect(postCount('/runners/runner-A/controls')).toBe(2)
  })

  it('a submitting card blocks duplicate submissions until the in-flight control settles', async () => {
    await discoverParked(true)
    const pending = agent.submitAskAnswers(askInput)
    await flush()
    expect(await agent.submitAskAnswers(askInput)).toBe('busy')
    expect(await agent.submitAskAnswers({ ...askInput, skip: true, answers: undefined })).toBe('busy')
    const posted = request('/runners/runner-A/controls', 'POST')
    posted.respond({ success: true, created: true, control: { ...askControl, client_request_id: JSON.parse(posted.body!).client_request_id },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 2 }) }, 202)
    expect(await pending).toBe('accepted')
    expect(postCount('/runners/runner-A/controls')).toBe(1)
  })

  it('resubmitting after acceptance mints a new client_request_id for the modified intent', async () => {
    await discoverParked(true)
    const first = agent.submitAskAnswers(askInput)
    await flush()
    const posted = request('/runners/runner-A/controls', 'POST')
    const firstKey = JSON.parse(posted.body!).client_request_id
    posted.respond({ success: true, created: true, control: { ...askControl, client_request_id: firstKey },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 2 }) }, 202)
    expect(await first).toBe('accepted')
    const modified = agent.submitAskAnswers({ ...askInput, answers: [{ question_id: 'q1', selected_keys: ['q1o2'], text: null }] })
    await flush()
    const secondPost = request('/runners/runner-A/controls', 'POST')
    expect(JSON.parse(secondPost.body!).client_request_id).not.toBe(firstKey)
    expect(JSON.parse(secondPost.body!).answers).toEqual([{ question_id: 'q1', selected_keys: ['q1o2'], text: null }])
    secondPost.respond({ success: true, created: true, control: { control_id: 'ask-control-2', runner_id: 'runner-A',
      action: 'reply', client_request_id: JSON.parse(secondPost.body!).client_request_id, status: 'accepted' },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 3 }) }, 202)
    expect(await modified).toBe('accepted')
  })

  it('terminal runners refuse submissions without issuing a control', async () => {
    await discoverParked(true)
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ status: 'cancelled', view_revision: 2,
      snapshot: { clarificationQuestions: questions } }) })
    await flush()
    const before = network.requests.length
    expect(await agent.submitAskAnswers(askInput)).toBe('closed')
    expect(network.requests.slice(before).every(item => item.method === 'GET')).toBe(true)
  })

  it('a free-text next message on a form wait still converts to a plain reply control (自由文本路径不变)', async () => {
    await discoverParked(true)
    const sending = agent.sendMessage('按已知信息继续吧', null, 'A')
    await flush()
    const posted = request('/runners/runner-A/controls', 'POST')
    const body = JSON.parse(posted.body!)
    expect(body).toEqual({ client_request_id: expect.any(String), action: 'reply', wait_id: 'wait-1',
      target_execution_id: 'exec-1', answer: '按已知信息继续吧' })
    expect('answers' in body).toBe(false)
    posted.respond({ success: true, created: true, control: { control_id: 'text-control', runner_id: 'runner-A',
      action: 'reply', client_request_id: body.client_request_id, status: 'accepted' },
      runner: runnerView({ status: 'waiting', resume_requested: true, view_revision: 2 }) }, 202)
    await sending; await flush()
    expect(external.legacy).not.toHaveBeenCalled()
  })
})
