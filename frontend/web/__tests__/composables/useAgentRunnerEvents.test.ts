/** Prepared useAgent transport contracts: external fetch/history/auth fixtures,
 * original RunnerClient/parser/reducer. Not real Web/PG/Worker/page E2E.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunnerView } from '@/api/runner'
import { controlledRunnerEventFetch } from '../mocks/runnerEventFetch'
import { runnerView } from '../mocks/runnerView'

const external = vi.hoisted(() => ({ token: 'fictional-event-A', legacy: vi.fn(), history: vi.fn() }))
vi.mock('@/api/agent', () => ({ SSEManager: class { connect = external.legacy; disconnect() {} }, uploadFile: vi.fn() }))
vi.mock('@/api/session', () => ({ getSessionMessages: external.history }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer ' + external.token }) }) }))
vi.mock('@/composables/useVideoGenParams', () => ({ useVideoGenParams: () => ({ params: { value: null } }) }))

const frame = (id: string, seq: number, revision: number) => new TextEncoder().encode(
  'id: ' + seq + '\nevent: revision_changed\ndata: ' + JSON.stringify({
    runner_id: id, version: 1, attempt: 1, status: 'running', settlement_status: 'pending',
    view_revision: revision, control_revision: 0, seq, kind: 'revision_changed', invalidate: true,
  }) + '\n\n')

describe('Existing useAgent presentation receives GET authority, not event bytes', () => {
  let network: ReturnType<typeof controlledRunnerEventFetch>
  let agent: ReturnType<typeof import('@/composables/useAgent').useAgent>
  const flush = async () => { for (let index = 0; index < 32; index++) await Promise.resolve() }
  const request = (path: string, method = 'GET') => {
    const found = network.pending.find(item => new URL(item.url).pathname === '/api/chat' + path && item.method === method)
    expect(found, 'Expected external request: ' + method + path).toBeDefined()
    return found!
  }
  const caps = (enabled = true, observe = true) => ({ web_enabled: enabled, observe_existing: observe,
    contract_version: 1, transport: 'runner_poll', events_supported: true })
  async function accepted(sid = 'A', row: RunnerView = runnerView()) {
    const sending = agent.sendMessage('original event-fixture input', 'video-agent', sid)
    await flush()
    request('/runners/capabilities').respond(caps())
    await flush()
    if (network.pending.some(item => new URL(item.url).pathname === '/api/chat/sessions/' + sid + '/runners')) {
      request('/sessions/' + sid + '/runners').respond({ runners: [], active_runners: [], has_more: false, next_cursor: null })
      await flush()
    }
    const submitted = request('/runners', 'POST')
    row.client_request_id = JSON.parse(submitted.body!).client_request_id
    submitted.respond({ success: true, runner: row }, 202)
    await sending
    await flush()
    return row
  }
  beforeEach(async () => {
    vi.resetModules()
    vi.useFakeTimers()
    external.token = 'fictional-event-A'
    external.legacy.mockReset()
    external.history.mockReset().mockResolvedValue({ messages: [] })
    network = controlledRunnerEventFetch()
    vi.stubGlobal('fetch', network.fetch)
    agent = (await import('@/composables/useAgent')).useAgent()
    agent.sessionId.value = 'A'
  })
  afterEach(async () => {
    agent.clearSessionCache()
    network.detachOutstanding()
    await flush()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('normal event bursts trigger cumulative GET replacement once per message, with no legacy execution or extra submit', async () => {
    await accepted()
    const events = request('/runners/runner-A/events')
    events.stream()
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ snapshot: { output: 'first authority' } }) })
    await flush()
    events.push(frame('runner-A', 1, 3))
    events.push(frame('runner-A', 2, 4))
    await flush()
    expect(agent.currentResponse.value).toBe('first authority')
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ view_revision: 4,
      snapshot: { output: 'full second authority' } }) })
    await flush()
    if (network.pending.some(item => new URL(item.url).pathname === '/api/chat/runners/runner-A')) {
      request('/runners/runner-A').respond({ success: true, runner: runnerView({ view_revision: 4,
        snapshot: { output: 'full second authority' } }) })
      await flush()
    }
    expect(agent.currentResponse.value).toBe('full second authority')
    expect(agent.messages.value.filter(item => item.role === 'assistant')).toHaveLength(1)
    expect(agent.messages.value.filter(item => item.role === 'user')).toHaveLength(1)
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('background Runner event/query stays in its own SID while a different current SID has its own input and output', async () => {
    await accepted()
    const eventsA = request('/runners/runner-A/events')
    eventsA.stream()
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ snapshot: { output: 'A initial' } }) })
    await flush()
    const changing = agent.switchSession('B')
    await flush()
    request('/runners/capabilities').respond(caps(false, false))
    await changing
    await flush()
    await accepted('B', runnerView({ runner_id: 'runner-B', session: { kind: 'web', session_id: 'B' } }))
    const eventsB = request('/runners/runner-B/events')
    eventsB.stream()
    request('/runners/runner-B').respond({ success: true, runner: runnerView({ runner_id: 'runner-B',
      session: { kind: 'web', session_id: 'B' }, snapshot: { output: 'B authority' } }) })
    await flush()
    eventsA.push(frame('runner-A', 1, 3))
    await flush()
    request('/runners/runner-A').respond({ success: true, runner: runnerView({ view_revision: 3,
      snapshot: { output: 'A background authority' } }) })
    await flush()
    expect(agent.sessionId.value).toBe('B')
    expect(agent.currentResponse.value).toBe('B authority')
    expect(agent.messages.value.some(item => item.content === 'A background authority')).toBe(false)
    expect(external.legacy).not.toHaveBeenCalled()
  })

  it('auth subject change and logout detach original stream/body requests without stale display or control cancellation', async () => {
    await accepted()
    const events = request('/runners/runner-A/events')
    events.stream()
    await flush()
    external.token = 'fictional-event-B'
    const changing = agent.switchSession('B')
    await flush()
    request('/runners/capabilities').respond(caps(false, false))
    await changing
    await flush()
    expect(events.aborted).toBe(true)
    expect(agent.sessionId.value).toBe('B')
    expect(agent.messages.value).toHaveLength(0)
    expect(agent.currentResponse.value).toBe('')
    agent.clearSessionCache()
    await flush()
    expect(network.requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(network.requests.some(item => new URL(item.url).pathname.endsWith('/cancel'))).toBe(false)
    expect(external.legacy).not.toHaveBeenCalled()
  })
})
