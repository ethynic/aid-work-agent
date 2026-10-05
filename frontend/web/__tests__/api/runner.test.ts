import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { RunnerClient, RunnerRequestError } from '@/api/runner'
import { controlledRunnerFetch } from '../mocks/runnerFetch'

const view = (status = 'running') => ({ runner_id: 'fixture-runner', session: { kind: 'web', session_id: 'A' }, status,
  revision: 1, view_revision: 1, control_revision: 0,
  settlement_status: ['completed', 'failed', 'cancelled'].includes(status) ? 'settled' : 'pending',
  snapshot: { output: 'full snapshot' } })

describe('RunnerClient independent observation and controls', () => {
  let network: ReturnType<typeof controlledRunnerFetch>
  let client: RunnerClient
  const headers = { Authorization: 'Bearer fictional-test-token' }
  const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve() }
  beforeEach(() => {
    vi.useFakeTimers()
    network = controlledRunnerFetch()
    vi.stubGlobal('fetch', network.fetch)
    client = new RunnerClient()
  })
  afterEach(() => { client.detach(); network.detachOutstanding(); vi.unstubAllGlobals(); vi.useRealTimers() })

  it('one physical poll remains in flight per runner even across elapsed intervals and repeated observe', async () => {
    const onView = vi.fn(), onError = vi.fn()
    client.observe('fixture-runner', headers, onView, onError)
    client.observe('fixture-runner', headers, onView, onError)
    await vi.advanceTimersByTimeAsync(4000)
    expect(network.requests).toHaveLength(1)
    network.requests[0].respond({ success: true, runner: view() })
    await flush()
    expect(onView).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(1000)
    expect(network.requests).toHaveLength(2)
    expect(network.maximumPending).toBe(1)
  })

  it('detach aborts observation without control requests or a synthetic terminal/error callback', async () => {
    const onView = vi.fn(), onError = vi.fn()
    client.observe('fixture-runner', headers, onView, onError)
    client.detach()
    await flush()
    await vi.advanceTimersByTimeAsync(4000)
    expect(network.requests).toHaveLength(1)
    expect(network.requests[0].aborted).toBe(true)
    expect(network.requests[0].method).toBe('GET')
    expect(onView).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('polls cumulative snapshots then stops only when durable terminal state arrives', async () => {
    const onView = vi.fn(), onError = vi.fn()
    client.observe('fixture-runner', headers, onView, onError)
    network.requests[0].respond({ success: true, runner: view() })
    await flush()
    await vi.advanceTimersByTimeAsync(1000)
    network.requests[1].respond({ success: true, runner: { ...view('completed'), view_revision: 2 } })
    await flush()
    await vi.advanceTimersByTimeAsync(5000)
    expect(network.requests).toHaveLength(2)
    expect(onView.mock.calls.map(([row]) => row.status)).toEqual(['running', 'completed'])
    expect(onError).not.toHaveBeenCalled()
  })

  it('401 stops new authenticated polls while temporary service errors continue observation', async () => {
    const error = vi.fn()
    client.observe('fixture-runner', headers, vi.fn(), error)
    network.requests[0].respond({ code: 'RUNNER_SERVICE_UNAVAILABLE' }, 503)
    await flush()
    await vi.advanceTimersByTimeAsync(2000)
    network.requests[1].respond({ code: 'USER_UNAUTHORIZED' }, 401)
    await flush()
    await vi.advanceTimersByTimeAsync(5000)
    expect(network.requests).toHaveLength(2)
    expect(error).toHaveBeenCalledTimes(2)
    expect(error.mock.calls[1][0]).toBeInstanceOf(RunnerRequestError)
  })

  it('explicit cancellation is a separate POST and returns running cancel-requested instead of fabricating terminal', async () => {
    const promise = client.cancel('fixture-runner', headers)
    expect(network.requests[0].method).toBe('POST')
    expect(network.requests[0].url).toContain('/fixture-runner/cancel')
    network.requests[0].respond({ success: true, runner: { ...view(), cancel_requested: true } })
    expect((await promise).status).toBe('running')
  })

  it('lost submission response does not rewrite the caller idempotency key or routing payload', async () => {
    const body = { client_request_id: 'fixed-key', session_id: 'A', message: 'original', subagent: 'video-agent', video_params: { duration_sec: 10 } }
    const first = client.submit(body, headers)
    const rejection = expect(first).rejects.toThrow('connection interrupted')
    network.requests[0].reject(new TypeError('connection interrupted'))
    await rejection
    const second = client.submit(body, headers)
    expect(network.requests[0].body).toBe(network.requests[1].body)
    network.requests[1].respond({ success: true, runner: view() }, 202)
    expect((await second).runner_id).toBe('fixture-runner')
  })

  it.each([{}, { web_enabled: 'false', observe_existing: true, contract_version: 1, transport: 'runner_poll' }])(
    'malformed capability response is an explicit protocol error', async body => {
      const promise = client.capabilities(headers)
      const failure = expect(promise).rejects.toMatchObject({ code: 'RUNNER_SERVICE_INVALID_RESPONSE', status: 502 })
      network.requests[0].respond(body)
      await failure
    })

  it.each([
    {},
    { success: true, control: { control_id: 'owned-control', status: 'consumed' } },
    { success: true, control: { control_id: 'owned-control', runner_id: 'foreign-runner', client_request_id: 'original-key', action: 'reply', status: 'consumed' } },
    { success: true, control: { control_id: 'wrong-control', runner_id: 'fixture-runner', client_request_id: 'original-key', action: 'reply', status: 'consumed' } },
  ])('an invalid successful control GET cannot be treated as an owned receipt', async response => {
    const result = client.getControl('fixture-runner', 'owned-control', headers)
    const error = expect(result).rejects.toMatchObject({ code: 'RUNNER_SERVICE_INVALID_RESPONSE', status: 502 })
    network.requests[0].respond(response)
    await error
    expect(network.requests).toHaveLength(1)
  })

  it('control observation reports durable rejection then stops without sending another control or cancelling execution', async () => {
    const receipt = { control_id: 'owned-control', runner_id: 'fixture-runner', client_request_id: 'original-key', action: 'reply', status: 'accepted' }
    const received = vi.fn(), failed = vi.fn()
    client.observeControl('fixture-runner', 'owned-control', headers, received, failed)
    network.requests[0].respond({ success: true, control: receipt })
    await flush(); await vi.advanceTimersByTimeAsync(1000)
    network.requests[1].respond({ success: true, control: { ...receipt, status: 'rejected', error_code: 'RECOVERY_PROFILE_CHANGED' } })
    await flush(); await vi.advanceTimersByTimeAsync(5000)
    expect(received.mock.calls.map(([value]) => value.status)).toEqual(['accepted', 'rejected'])
    expect(network.requests).toHaveLength(2)
    expect(network.requests.every(item => item.method === 'GET')).toBe(true)
    expect(failed).not.toHaveBeenCalled()
  })
})
