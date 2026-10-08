/** Controlled external HTTP bytes only. Original parser/client/reducer remain
 * in use. This is not a service/PG/Worker transport integration proof.
 */
import { vi } from 'vitest'

export function controlledRunnerEventFetch() {
  type Phase = 'pending' | 'json' | 'stream' | 'closed'
  const requests: Array<{
    url: string
    method: string
    body: string | null
    phase: Phase
    aborted: boolean
    readerCancelled: number
    respond: (value: unknown, status?: number) => void
    stream: (status?: number, contentType?: string) => void
    push: (bytes: Uint8Array) => void
    end: () => void
    fail: (error: Error) => void
  }> = []

  const fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input instanceof Request ? input : new URL(String(input), 'http://localhost'), init)
    const target = new URL(request.url)
    if (target.origin !== 'http://localhost' || !target.pathname.startsWith('/api/')) {
      return Promise.reject(new Error('Unexpected external test target; no real fetch fallback'))
    }
    return new Promise<Response>((resolve, reject) => {
      let controller: ReadableStreamDefaultController<Uint8Array> | null = null
      const removeAbort = () => request.signal.removeEventListener('abort', aborted)
      const observed = {
        url: request.url, method: request.method,
        body: typeof init?.body === 'string' ? init.body : null,
        phase: 'pending' as Phase, aborted: false, readerCancelled: 0,
        respond(value: unknown, status = 200) {
          if (observed.phase !== 'pending') throw new Error('Fixture response already settled')
          observed.phase = 'json'
          removeAbort()
          resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }))
        },
        stream(status = 200, contentType = 'text/event-stream') {
          if (observed.phase !== 'pending') throw new Error('Fixture response already settled')
          observed.phase = 'stream'
          const bytes = new ReadableStream<Uint8Array>({
            start(value) { controller = value },
            cancel() {
              observed.readerCancelled++
              observed.phase = 'closed'
              removeAbort()
            },
          })
          resolve(new Response(bytes, { status, headers: { 'Content-Type': contentType } }))
        },
        push(bytes: Uint8Array) {
          if (observed.phase !== 'stream' || !controller) throw new Error('Fixture stream is not open')
          controller.enqueue(bytes)
        },
        end() {
          if (observed.phase !== 'stream' || !controller) throw new Error('Fixture stream is not open')
          observed.phase = 'closed'
          controller.close()
          removeAbort()
        },
        fail(error: Error) {
          if (observed.phase === 'pending') {
            observed.phase = 'closed'
            removeAbort()
            reject(error)
          } else if (observed.phase === 'stream' && controller) {
            observed.phase = 'closed'
            removeAbort()
            controller.error(error)
          }
        },
      }
      function aborted() {
        observed.aborted = true
        observed.fail(new DOMException('Observation detached', 'AbortError'))
      }
      requests.push(observed)
      if (request.signal.aborted) aborted()
      else request.signal.addEventListener('abort', aborted, { once: true })
    })
  })
  return {
    fetch, requests,
    get pending() { return requests.filter(item => item.phase === 'pending') },
    detachOutstanding() {
      for (const item of requests) {
        if (item.phase === 'pending' || item.phase === 'stream') {
          item.fail(new DOMException('Fixture teardown', 'AbortError'))
        }
      }
    },
  }
}
