import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { ApiError, NetworkError, DeviceRevokedError, isRetryableTransportError } from '../dist/legacy/apiClient.js'
import { ResultOutbox, deliverOutboxEntry, replayPendingOutbox } from '../dist/legacy/resultOutbox.js'
import { runInvocation } from '../dist/legacy/invocationRunner.js'

function fixture(t) {
  const home = mkdtempSync(join(tmpdir(), 'aid-transport-recovery-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  return { home, outbox: new ResultOutbox(join(home, 'result-outbox')) }
}

test('transient HTTP and transport errors retry, authoritative refusals never do', () => {
  for (const status of [408, 429, 500, 502, 503, 504]) assert.equal(isRetryableTransportError(new ApiError(status, 'test')), true)
  for (const status of [400, 401, 403, 404, 409, 422]) assert.equal(isRetryableTransportError(new ApiError(status, 'test')), false)
  assert.equal(isRetryableTransportError(new ApiError(503, '', '', 'LEASE_EXPIRED')), false)
  assert.equal(isRetryableTransportError(new NetworkError('test')), true)
  assert.equal(isRetryableTransportError(new DeviceRevokedError('test')), false)
})

test('a legacy final result survives shutdown/reopen and is retransmitted unchanged to its original endpoint', async t => {
  const { home, outbox } = fixture(t)
  const payload = { claim_token: 'synthetic-original-claim', success: true, effect: 'none', data: { completed: 14 } }
  const entry = outbox.saveLegacy('original', payload)
  assert.deepEqual(readdirSync(outbox.dir).filter(name => name.endsWith('.json')), [], 'old v2-only reader cannot misroute v1')
  const controller = new AbortController(); controller.abort()
  assert.equal(await deliverOutboxEntry({ result: async () => { throw new ApiError(503, 'test') } }, outbox, entry,
    { retryBaseMs: 1, shutdownSignal: controller.signal, emit() {} }), 'shutdown')
  const reopened = new ResultOutbox(join(home, 'result-outbox'))
  assert.deepEqual(reopened.load('original').payload, payload)
  let sends = 0
  const result = await replayPendingOutbox({ result: async (id, actual) => {
    sends++; assert.equal(id, 'original'); assert.deepEqual(actual, payload)
  }, operationResult() { throw Error('wrong endpoint') } }, reopened, { emit() {} })
  assert.equal(sends, 1); assert.equal(result.acked, 1); assert.equal(reopened.load('original'), null)
})

test('408/429 respect Retry-After and keep the same receipt while 409 preserves refusal evidence', async t => {
  const { outbox } = fixture(t)
  const entry = outbox.saveLegacy('receipt', { claim_token: 'fixture', success: false, effect: 'unknown' })
  let calls = 0
  const times = []
  const api = { result: async () => { times.push(Date.now()); if (++calls < 3) throw new ApiError(calls === 1 ? 408 : 429, 'test', undefined, undefined, 15) } }
  assert.equal(await deliverOutboxEntry(api, outbox, entry, { retryBaseMs: 1, emit() {} }), 'acked')
  assert.equal(calls, 3); assert.ok(times[2] - times[1] >= 10)
  const refused = outbox.saveLegacy('refused', { claim_token: 'fixture', success: false, effect: 'unknown' })
  assert.equal(await deliverOutboxEntry({ result: async () => { throw new ApiError(409, 'test') } }, outbox, refused), 'gave-up')
  assert.equal(outbox.load('refused').gave_up, true)
})

function runner(t, changes = {}) {
  const { home, outbox } = fixture(t)
  let executed = 0, started = 0, results = 0
  const inv = { invocation_id: randomUUID(), claim_token: 'synthetic-claim', provider: 'isolated', tool_name: 'read_fixture', arguments: {} }
  const deps = { api: { async started(id, claim) { started++; assert.equal(id, inv.invocation_id); assert.equal(claim, inv.claim_token) },
      async progress() { return { cancel: false } }, async result() { results++ } },
    providers: { has() { return true }, get() { return { async callTool() { executed++; return { structuredContent: { success: true, effect: 'none', data: {} } } } } } },
    manifests: { isolated: { provider_key: 'isolated', provider_id: 'fixture', tools: ['read_fixture'], write_tools: new Set(), protocol_version: 1, shared_lock_capable: false } },
    desktopResourceKey: 'transport-test-' + randomUUID(), desktopCheck: async () => true,
    runtimeDataDir: home, resultOutbox: outbox, retryBaseMs: 1, retryMaxMs: 2, onEvent() {}, ...changes }
  return { inv, deps, counts: () => ({ executed, started, results }) }
}

test('503 then 429 on started yields exactly one provider execution; lease loss yields none', async t => {
  const f = runner(t)
  let tries = 0
  f.deps.api.started = async () => { if (++tries < 3) throw new ApiError(tries === 1 ? 503 : 429, 'test') }
  await runInvocation(f.inv, f.deps)
  assert.equal(tries, 3); assert.equal(f.counts().executed, 1)
  const expired = runner(t)
  expired.deps.api.started = async () => { throw new ApiError(409, 'lease expired') }
  await runInvocation(expired.inv, expired.deps)
  assert.equal(expired.counts().executed, 0)
})

test('authoritative progress rejection aborts the provider signal before another input', async t => {
  const f = runner(t)
  f.deps.progressIntervalMs = 5
  f.deps.api.progress = async () => { throw new ApiError(404, 'lost invocation') }
  let laterInput = false
  f.deps.providers.get = () => ({ async callTool(_name, _args, options) {
    options.onProgress({ progress: 1, message: 'fixture' })
    await new Promise(resolve => setTimeout(resolve, 25))
    if (!options.signal.aborted) laterInput = true
    return { structuredContent: { success: false, code: 'CANCELLED', effect: 'none' } }
  } })
  await runInvocation(f.inv, f.deps)
  assert.equal(laterInput, false)
})
