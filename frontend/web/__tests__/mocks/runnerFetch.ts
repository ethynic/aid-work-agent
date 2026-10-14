/** Controlled external HTTP only; real RunnerClient/composable stays in use.
 * No runner DTO shape is assumed here. Every request remains pending until the
 * test releases it, so stale-response and overlapping-poll races are observable.
 */
import { vi } from 'vitest'

export interface PendingRunnerRequest {
  url: string
  method: string
  body: string | null
  settled: boolean
  aborted: boolean
  respond: (body: unknown, status?: number) => void
  reject: (error: Error) => void
}

export function controlledRunnerFetch() {
  const requests: PendingRunnerRequest[] = []
  let maximumPending = 0

  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    // In some Node/jsdom combinations the caller's AbortSignal comes from a
    // different realm than undici's Request, which rejects it outright
    // ("Expected signal ... to be an instance of AbortSignal"). The controlled
    // fetch never hands the request to a real transport, so the signal is
    // observed through an explicit listener instead of RequestInit.
    const signal = init?.signal
    const request = new Request(input instanceof Request ? input : new URL(String(input), 'http://localhost'),
      { ...init, signal: undefined })
    const url = new URL(request.url)
    if (url.origin !== 'http://localhost' || !url.pathname.startsWith('/api/')) {
      return Promise.reject(new Error('Unexpected test network target; no real fetch fallback'))
    }
    return new Promise<Response>((resolve, reject) => {
      let complete: () => void
      const observed: PendingRunnerRequest = {
        url: request.url,
        method: request.method,
        body: typeof init?.body === 'string' ? init.body : null,
        settled: false,
        aborted: false,
        respond: (body, status = 200) => {
          if (observed.settled) throw new Error('Test response already settled')
          complete()
          resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
        },
        reject: (error) => {
          if (observed.settled) return
          complete()
          reject(error)
        },
      }
      const aborted = () => {
        observed.aborted = true
        observed.reject(new DOMException('Observation detached', 'AbortError'))
      }
      complete = () => {
        observed.settled = true
        signal?.removeEventListener('abort', aborted)
      }
      requests.push(observed)
      maximumPending = Math.max(maximumPending, requests.filter(item => !item.settled).length)
      if (signal?.aborted) aborted()
      else signal?.addEventListener('abort', aborted, { once: true })
    })
  })

  return {
    fetch,
    requests,
    get pending() { return requests.filter(item => !item.settled) },
    get maximumPending() { return maximumPending },
    detachOutstanding() {
      for (const request of requests.filter(item => !item.settled)) {
        request.aborted = true
        request.reject(new DOMException('Test fixture teardown', 'AbortError'))
      }
    },
  }
}
