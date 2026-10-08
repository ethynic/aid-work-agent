/** Prepared original client over controlled external HTTP bytes; no serviceE2E claim. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RunnerClient } from '@/api/runner'
import { controlledRunnerEventFetch } from '../mocks/runnerEventFetch'
import { runnerView } from '../mocks/runnerView'

const caps = (supported?: boolean) => ({ web_enabled: true, observe_existing: true,
  contract_version: 1, transport: 'runner_poll', ...(supported === undefined ? {} : { events_supported: supported }) })
const headers = { Authorization: 'Bearer fictional-event-test-token' }
const bytes = (seq: number, view = 3, control = 1, overrides: Record<string, unknown> = {}, kind = 'revision_changed') =>
  new TextEncoder().encode('id: ' + seq + '\nevent: ' + kind + '\ndata: ' + JSON.stringify({
    runner_id: 'runner-A', version: 1, attempt: 2, status: 'running', settlement_status: 'pending',
    view_revision: view, control_revision: control, seq, kind, invalidate: true, ...overrides }) + '\n\n')

describe('Original RunnerClient event enhancement keeps authoritative GET as display owner', () => {
  let network: ReturnType<typeof controlledRunnerEventFetch>
  let client: RunnerClient
  const flush = async () => { for (let index = 0; index < 24; index++) await Promise.resolve() }
  const queries = () => network.requests.filter(item => new URL(item.url).pathname === '/api/chat/runners/runner-A')
  const streams = () => network.requests.filter(item => new URL(item.url).pathname.endsWith('/events'))
  const query = () => {
    const item = network.pending.find(item => new URL(item.url).pathname === '/api/chat/runners/runner-A')
    expect(item, 'Expected original authoritative GET').toBeDefined()
    return item!
  }
  const stream = () => {
    const item = network.pending.find(item => new URL(item.url).pathname.endsWith('/events'))
    expect(item, 'Expected event observation GET').toBeDefined()
    return item!
  }
  beforeEach(() => {
    vi.useFakeTimers()
    network = controlledRunnerEventFetch()
    vi.stubGlobal('fetch', network.fetch)
    client = new RunnerClient()
  })
  afterEach(async () => {
    client.detach()
    network.detachOutstanding()
    await flush()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('absent or false optional capability keeps original poll and never creates an event reader', async () => {
    client.installCapabilities(caps())
    client.observe('runner-A', headers, vi.fn(), vi.fn())
    query().respond({ success: true, runner: runnerView() })
    await flush()
    client.installCapabilities(caps(false))
    await vi.advanceTimersByTimeAsync(1000)
    expect(queries()).toHaveLength(2)
    expect(streams()).toHaveLength(0)
    expect(network.requests.every(item => item.method === 'GET')).toBe(true)
  })

  it('normal opt-in queries each typed hint and replaces cumulative output only from confirmed GET', async () => {
    const display = vi.fn(), error = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, error)
    const events = stream()
    events.stream()
    query().respond({ success: true, runner: runnerView({ snapshot: { output: 'first GET' } }) })
    await flush()
    events.push(bytes(1))
    await flush()
    expect(display.mock.calls.map(([row]) => row.snapshot.output)).toEqual(['first GET'])
    query().respond({ success: true, runner: runnerView({ view_revision: 3, control_revision: 1,
      snapshot: { output: 'full authoritative second GET' } }) })
    await flush()
    expect(display.mock.calls.map(([row]) => row.snapshot.output)).toEqual(['first GET', 'full authoritative second GET'])
    expect(error).not.toHaveBeenCalled()
    expect(network.requests.every(item => item.method === 'GET')).toBe(true)
    events.end()
    await flush()
    query().respond({ success: true, runner: runnerView({ view_revision: 3, control_revision: 1 }) })
    await flush()
    await vi.advanceTimersByTimeAsync(1000)
    expect(new URL(stream().url).searchParams.get('after_seq')).toBe('1')
  })

  it('pending GET remains single in flight across burst hints and cursor waits for both confirmed revisions', async () => {
    const display = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, vi.fn())
    const events = stream()
    events.stream()
    await flush()
    events.push(bytes(1, 5, 3))
    events.push(bytes(2, 4, 2))
    await flush()
    expect(queries()).toHaveLength(1)
    query().respond({ success: true, runner: runnerView({ view_revision: 4, control_revision: 2 }) })
    await flush()
    expect(display).not.toHaveBeenCalled()
    expect(queries()).toHaveLength(2)
    query().respond({ success: true, runner: runnerView({ view_revision: 5, control_revision: 3 }) })
    await flush()
    expect(display).toHaveBeenCalledOnce()
    expect(queries().filter(item => item.phase === 'pending')).toHaveLength(0)
    events.end()
    await flush()
    query().respond({ success: true, runner: runnerView({ view_revision: 5, control_revision: 3 }) })
    await flush()
    await vi.advanceTimersByTimeAsync(1000)
    expect(new URL(stream().url).searchParams.get('after_seq')).toBe('2')
  })

  it('malformed foreign event and EOF trigger authoritative fallback without event-body UI error or append', async () => {
    const display = vi.fn(), error = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, error)
    const events = stream()
    events.stream()
    query().respond({ success: true, runner: runnerView({ snapshot: { output: 'owned GET' } }) })
    await flush()
    events.push(bytes(1, 99, 99, { runner_id: 'foreign-runner' }))
    await flush()
    query().respond({ success: true, runner: runnerView({ snapshot: { output: 'fallback owned GET' } }) })
    await flush()
    expect(display.mock.calls.map(([row]) => row.snapshot.output)).toEqual(['owned GET', 'fallback owned GET'])
    expect(error).not.toHaveBeenCalled()
    expect(events.aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(1000)
    expect(new URL(stream().url).searchParams.get('after_seq')).toBe('0')
    expect(network.requests.every(item => item.method === 'GET')).toBe(true)
  })

  it('reset is confirmed by current GET, protects newer display and resumes from the proven small head', async () => {
    const display = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, vi.fn())
    const events = stream()
    events.stream()
    query().respond({ success: true, runner: runnerView({ view_revision: 4, control_revision: 2,
      snapshot: { output: 'current display' } }) })
    await flush()
    events.push(new TextEncoder().encode('id: 0\nevent: reset\ndata: ' + JSON.stringify({
      reset: true, query_required: true, head: 0, floor: 0, last_seq: 0,
      state: { runner_id: 'runner-A', version: 1, attempt: 2, status: 'running',
        settlement_status: 'pending', view_revision: 4, control_revision: 2 },
    }) + '\n\n'))
    await flush()
    query().respond({ success: true, runner: runnerView({ view_revision: 3, control_revision: 2,
      snapshot: { output: 'late stale GET' } }) })
    await flush()
    expect(display.mock.calls.map(([row]) => row.snapshot.output)).toEqual(['current display'])
    await vi.advanceTimersByTimeAsync(1000)
    query().respond({ success: true, runner: runnerView({ view_revision: 4, control_revision: 2,
      snapshot: { output: 'confirmed current GET' } }) })
    await flush()
    expect(display.mock.calls.map(([row]) => row.snapshot.output)).toEqual(['current display', 'confirmed current GET'])
    events.push(bytes(1, 5, 2))
    await flush()
    query().respond({ success: true, runner: runnerView({ view_revision: 5, control_revision: 2 }) })
    await flush()
    expect(display).toHaveBeenCalledTimes(3)
  })

  it('unacknowledged sequence gap cannot advance reconnect cursor or synthesize a terminal display', async () => {
    const display = vi.fn(), error = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, error)
    const events = stream()
    events.stream()
    query().respond({ success: true, runner: runnerView() })
    await flush()
    events.push(bytes(2, 7, 7, { status: 'completed', settlement_status: 'settled' }, 'terminal'))
    await flush()
    query().respond({ success: true, runner: runnerView() })
    await flush()
    expect(display.mock.calls.every(([row]) => row.status === 'running')).toBe(true)
    expect(error).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1000)
    expect(new URL(stream().url).searchParams.get('after_seq')).toBe('0')
  })

  it('strict duplicate event is ignored after an observed seq, while a later reset cannot lower unmet revision watermarks', async () => {
    const display = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, vi.fn())
    const events = stream()
    events.stream()
    query().respond({ success: true, runner: runnerView() })
    await flush()
    events.push(bytes(1, 5, 3))
    await flush()
    expect(queries()).toHaveLength(2)
    events.push(bytes(1, 5, 3))
    await flush()
    expect(queries()).toHaveLength(2)
    events.push(new TextEncoder().encode('id: 0\nevent: reset\ndata: ' + JSON.stringify({
      reset: true, query_required: true, head: 0, floor: 0, last_seq: 0,
      state: { runner_id: 'runner-A', version: 1, attempt: 2, status: 'running',
        settlement_status: 'pending', view_revision: 3, control_revision: 1 },
    }) + '\n\n'))
    await flush()
    query().respond({ success: true, runner: runnerView({ view_revision: 3, control_revision: 1 }) })
    await flush()
    expect(display).toHaveBeenCalledOnce()
    query().respond({ success: true, runner: runnerView({ view_revision: 5, control_revision: 3 }) })
    await flush()
    expect(display).toHaveBeenCalledTimes(2)
    events.end()
    await flush()
    query().respond({ success: true, runner: runnerView({ view_revision: 5, control_revision: 3 }) })
    await flush()
    await vi.advanceTimersByTimeAsync(1000)
    expect(new URL(stream().url).searchParams.get('after_seq')).toBe('0')
  })

  it('over64KiB chunk and readidle30s abort their own streams and query without transport error in product callbacks', async () => {
    const display = vi.fn(), error = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, error)
    const first = stream()
    first.stream()
    query().respond({ success: true, runner: runnerView() })
    await flush()
    first.push(new Uint8Array(65537).fill(58))
    await flush()
    expect(first.aborted).toBe(true)
    query().respond({ success: true, runner: runnerView() })
    await flush()
    await vi.advanceTimersByTimeAsync(1000)
    const idle = stream()
    idle.stream()
    await flush()
    for (let interval = 0; interval < 5; interval++) {
      await vi.advanceTimersByTimeAsync(5000)
      query().respond({ success: true, runner: runnerView() })
      await flush()
    }
    expect(idle.aborted).toBe(false)
    await vi.advanceTimersByTimeAsync(5000)
    await flush()
    expect(idle.aborted).toBe(true)
    expect(error).not.toHaveBeenCalled()
    expect(display.mock.calls.every(([row]) => row.status === 'running')).toBe(true)
    expect(network.requests.every(item => item.method === 'GET')).toBe(true)
  })

  it('terminal pending observation survives until original authoritative settlement GET then closes own reader', async () => {
    const display = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, vi.fn())
    const events = stream()
    events.stream()
    query().respond({ success: true, runner: runnerView({ status: 'completed', settlement_status: 'pending' }) })
    await flush()
    expect(events.aborted).toBe(false)
    events.push(bytes(1, 3, 1, { status: 'completed', settlement_status: 'settled' }, 'settlement_changed'))
    await flush()
    expect(events.aborted).toBe(false)
    query().respond({ success: true, runner: runnerView({ status: 'completed', settlement_status: 'settled',
      view_revision: 3, control_revision: 1 }) })
    await flush()
    expect(events.aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(20000)
    expect(queries()).toHaveLength(2)
    expect(streams()).toHaveLength(1)
    expect(display.mock.calls.map(([row]) => row.settlement_status)).toEqual(['pending', 'settled'])
  })

  it('healthy stream retains 5s authority checks, detach aborts only observations and stale GET never publishes', async () => {
    const display = vi.fn(), error = vi.fn()
    client.installCapabilities(caps(true))
    client.observe('runner-A', headers, display, error)
    const events = stream()
    events.stream()
    query().respond({ success: true, runner: runnerView() })
    await flush()
    await vi.advanceTimersByTimeAsync(4999)
    expect(queries()).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(queries()).toHaveLength(2)
    client.detach()
    await flush()
    expect(events.aborted).toBe(true)
    expect(queries()[1].aborted).toBe(true)
    await vi.advanceTimersByTimeAsync(20000)
    expect(queries()).toHaveLength(2)
    expect(display).toHaveBeenCalledOnce()
    expect(error).not.toHaveBeenCalled()
    expect(network.requests.every(item => item.method === 'GET')).toBe(true)
  })
})
