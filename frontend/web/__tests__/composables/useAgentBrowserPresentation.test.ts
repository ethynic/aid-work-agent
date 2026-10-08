/** Real composable and RunnerClient; only external HTTP/auth/history seams are fictional. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BrowserHumanAssistance } from '@/types'
import type { RunnerView } from '@/api/runner'
import { controlledRunnerFetch } from '../mocks/runnerFetch'
import { runnerView } from '../mocks/runnerView'

const external = vi.hoisted(() => ({ token: 'fictional-A', legacy: vi.fn(), history: vi.fn() }))
vi.mock('@/api/agent', () => ({ SSEManager: class { connect = external.legacy; disconnect() {} }, uploadFile: vi.fn() }))
vi.mock('@/api/session', () => ({ getSessionMessages: external.history }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer ' + external.token }) }) }))
vi.mock('@/composables/useVideoGenParams', () => ({ useVideoGenParams: () => ({ params: { value: null } }) }))

const card = (aid: string): BrowserHumanAssistance => ({ assistance_id: aid, run_id: 'native-run-A',
  continuation_id: '', reason_code: 'PAGE_VERIFICATION', surface: 'server_web', title: '原页面验证',
  steps: [], completion_mode: 'confirm_only', completion_status: 'waiting',
  expires_at: '2099-01-01T00:00:00Z', state: 'controlling', view_available: true })

describe('Runner Browser query ownership and version ordering', () => {
  let network: ReturnType<typeof controlledRunnerFetch>
  let agent: ReturnType<typeof import('@/composables/useAgent').useAgent>
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
  const request = (path: string, method = 'GET') => {
    const found = network.pending.find(value => new URL(value.url).pathname === '/api/chat' + path && value.method === method)
    expect(found, 'Expected external request ' + method + path).toBeDefined()
    return found!
  }
  const headers = () => ({ Authorization: 'Bearer ' + external.token })
  async function discover(sid: string, rows: RunnerView[]) {
    const task = agent.switchSession(sid); await flush()
    const capabilities = network.pending.find(value => value.url.endsWith('/capabilities'))
    if (capabilities) { capabilities.respond({ web_enabled: true, observe_existing: true, contract_version: 1, transport: 'runner_poll' }); await flush() }
    request('/sessions/' + sid + '/runners').respond({ runners: rows, active_runners: rows, has_more: false, next_cursor: null })
    await task; await flush()
  }
  beforeEach(async () => {
    vi.resetModules(); vi.useFakeTimers(); external.token = 'fictional-A'
    external.legacy.mockReset(); external.history.mockReset().mockResolvedValue({ messages: [] })
    network = controlledRunnerFetch(); vi.stubGlobal('fetch', network.fetch)
    agent = (await import('@/composables/useAgent')).useAgent(); agent.sessionId.value = 'A'
  })
  afterEach(async () => { agent.clearSessionCache(); network.detachOutstanding(); await flush(); vi.unstubAllGlobals(); vi.useRealTimers() })

  it('an older concurrent action refresh cannot replace a newer wait, append messages or start BAC polling', async () => {
    await discover('A', [runnerView({ status: 'waiting', snapshot: { browserAssistance: card('wait-A') } })])
    const poll = request('/runners/runner-A')
    const refreshing = agent.refreshRunner('runner-A', headers()); await flush()
    const refresh = network.pending.find(value => value !== poll && value.url.endsWith('/runners/runner-A'))!
    poll.respond({ success: true, runner: runnerView({ status: 'waiting', view_revision: 4,
      snapshot: { browserAssistance: card('wait-B'), output: 'new wait' } }) }); await flush()
    refresh.respond({ success: true, runner: runnerView({ status: 'waiting', view_revision: 2,
      snapshot: { browserAssistance: card('wait-A'), output: 'stale wait' } }) }); await refreshing; await flush()
    expect(agent.messages.value).toHaveLength(2)
    expect(agent.messages.value[1].browserAssistance?.assistance_id).toBe('wait-B')
    expect(agent.currentResponse.value).toBe('new wait')
    expect(network.requests.every(value => new URL(value.url).pathname.startsWith('/api/chat/'))).toBe(true)
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('late original-session refresh cannot insert its card into another selected session', async () => {
    await discover('A', [runnerView({ status: 'waiting', snapshot: { browserAssistance: card('wait-A') } })])
    const refreshTask = agent.refreshRunner('runner-A', headers()); await flush()
    const refreshRequests = network.pending.filter(value => value.url.endsWith('/runners/runner-A'))
    const held = refreshRequests[refreshRequests.length - 1]!
    await discover('B', [])
    held.respond({ success: true, runner: runnerView({ status: 'waiting', view_revision: 5,
      snapshot: { browserAssistance: card('old-session-late') } }) }); await refreshTask; await flush()
    expect(agent.sessionId.value).toBe('B')
    expect(agent.messages.value).toEqual([])
    expect(agent.canStop.value).toBe(false)
    expect(network.requests.filter(value => value.method === 'POST')).toEqual([])
  })

  it('verification keeps Stop available while a real clarification wait can send a reply on the same Runner', async () => {
    await discover('A', [runnerView({ status: 'waiting', cancel_requested: true, snapshot: {
      browserAssistance: { ...card('wait-A'), completion_status: 'verification_required', view_available: false },
      waiting: { kind: 'child_wait', child_wait: { kind: 'verification', question: '原操作需要核对' } },
    } })])
    expect(agent.isProcessing.value).toBe(false)
    expect(agent.canStop.value).toBe(true)
    expect(agent.messages.value[1].waitingNotice).toBe('原操作需要核对')
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ status: 'waiting', view_revision: 2,
      snapshot: { browserAssistance: card('wait-A'), waiting: { kind: 'child_wait', child_wait: {
        kind: 'clarification', wait_id: 'clarify-wait', target_execution_id: 'owned-child', question: '补充资料？' } } } }) }); await flush()
    expect(agent.isWaitingHuman.value).toBe(false)
    expect(agent.canStop.value).toBe(true)
    const sending = agent.sendMessage('补充资料', null, 'A'); await flush()
    const reply = request('/runners/runner-A/controls', 'POST')
    const body = JSON.parse(reply.body!)
    expect(body).toMatchObject({ action: 'reply', wait_id: 'clarify-wait', target_execution_id: 'owned-child', answer: '补充资料' })
    reply.respond({ success: true, created: true, control: { control_id: 'reply-control', runner_id: 'runner-A',
      action: 'reply', client_request_id: body.client_request_id, status: 'accepted' },
      runner: runnerView({ status: 'waiting', view_revision: 3, resume_requested: true }) }, 202)
    await sending; await flush()
    expect(network.requests.filter(value => value.method === 'POST' && value.url.endsWith('/runners'))).toEqual([])
    expect(external.legacy).not.toHaveBeenCalled()
  })
})
