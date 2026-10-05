/** New capability network seam for tests exercising the unchanged legacy SSE. */
import { vi } from 'vitest'

export function legacyRunnerCapabilitiesFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const path = new URL(url, 'http://localhost').pathname
    if (path.endsWith('/chat/runners/capabilities')) return new Response(JSON.stringify({
      web_enabled: false, observe_existing: false, contract_version: 1, transport: 'runner_poll',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    if (/\/api\/chat\/[^/]+\/cancel$/.test(path)) return new Response('{}', { status: 200 })
    throw new Error('Unexpected legacy fixture HTTP target; no real fetch fallback')
  })
}
