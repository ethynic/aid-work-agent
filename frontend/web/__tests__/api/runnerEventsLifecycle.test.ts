/** Original client lifecycle; controlled external IO, no physical socket claim. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { RunnerClient } from '@/api/runner'
import { controlledRunnerEventFetch } from '../mocks/runnerEventFetch'
import { runnerView } from '../mocks/runnerView'

const headers = { Authorization: 'Bearer fictional-lifecycle' }
const caps = { web_enabled: true, observe_existing: true, contract_version: 1,
  transport: 'runner_poll', events_supported: true }

describe('Original Runner event observation lifecycle remains bounded', () => {
  let client: RunnerClient
  const flush = async () => { for (let index = 0; index < 24; index++) await Promise.resolve() }
  beforeEach(() => { vi.useFakeTimers(); client = new RunnerClient(); client.installCapabilities(caps) })
  afterEach(() => { client.detach(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

  it('hidden healthy stream retains actual15s authority interval while event observation stays attached', async () => {
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    const network = controlledRunnerEventFetch()
    vi.stubGlobal('fetch', network.fetch)
    const display = vi.fn()
    client.observe('runner-A', headers, display, vi.fn())
    const events = network.pending.find(item => item.url.includes('/events'))!
    events.stream()
    network.pending.find(item => !item.url.includes('/events'))!.respond({ success: true, runner: runnerView() })
    await flush()
    await vi.advanceTimersByTimeAsync(14999)
    expect(network.requests.filter(item => !item.url.includes('/events'))).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(network.requests.filter(item => !item.url.includes('/events'))).toHaveLength(2)
    expect(events.aborted).toBe(false)
    client.detach()
    await flush()
    expect(events.aborted).toBe(true)
    expect(network.requests.filter(item => item.method !== 'GET')).toEqual([])
    network.detachOutstanding()
  })

  it('a late external handshake after detach cancels the unclaimed body without another reader or display', async () => {
    let late: (response: Response) => void = () => { throw new Error('No original handshake') }
    let streamSignal: AbortSignal | undefined
    const cancelled = vi.fn()
    const externalFetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes('/events')) {
        streamSignal = init?.signal as AbortSignal
        // IO timing DI: deliver the original body late despite aborted HTTP request.
        return new Promise<Response>(resolve => { late = resolve })
      }
      return Promise.resolve(new Response(JSON.stringify({ success: true, runner: runnerView() }),
        { headers: { 'Content-Type': 'application/json' } }))
    })
    vi.stubGlobal('fetch', externalFetch)
    const display = vi.fn(), error = vi.fn()
    client.observe('runner-A', headers, display, error)
    await flush()
    expect(display).toHaveBeenCalledOnce()
    client.detach()
    expect(streamSignal?.aborted).toBe(true)
    late(new Response(new ReadableStream<Uint8Array>({ cancel: cancelled }),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } }))
    await flush()
    expect(cancelled).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(30000)
    expect(display).toHaveBeenCalledOnce()
    expect(error).not.toHaveBeenCalled()
    expect(externalFetch).toHaveBeenCalledTimes(2)
  })
})
