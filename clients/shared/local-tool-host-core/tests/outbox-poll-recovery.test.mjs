import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { PollLoop } from '../dist/legacy/pollLoop.js'
import { ResultOutbox, deliverOutboxEntry, replayPendingOutbox } from '../dist/legacy/resultOutbox.js'
import { AbortLoopError, ApiClient, ApiError, NetworkError } from '../dist/legacy/apiClient.js'

function fixture(t) {
  const home = mkdtempSync(join(tmpdir(), 'aid-outbox-poll-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  return new ResultOutbox(join(home, 'result-outbox'))
}

async function until(predicate) {
  const deadline = Date.now() + 3000
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error('test condition did not complete')
    await new Promise(resolve => setTimeout(resolve, 5))
  }
}

test('pending result delivery cannot delay initial heartbeats/claim and shutdown aborts it without deleting evidence', async t => {
  const outbox = fixture(t)
  const payload = { claim_token: 'fixture-original', success: false, effect: 'unknown' }
  outbox.saveLegacy('pending', payload)
  let heartbeats = 0, claims = 0, resultCalls = 0
  const api = {
    async heartbeat() { heartbeats++; return { selected: true } },
    async claim(_wait, signal) {
      claims++
      await new Promise(resolve => setTimeout(resolve, 5))
      if (signal.aborted) throw new AbortLoopError('stopped')
      return null
    },
    async result(id, value, signal) {
      resultCalls++
      assert.equal(id, 'pending'); assert.deepEqual(value, payload)
      await new Promise((_resolve, reject) => {
        const abort = () => reject(new AbortLoopError('stopped'))
        signal.addEventListener('abort', abort, { once: true })
        if (signal.aborted) abort()
      })
    },
  }
  const loop = new PollLoop({ api, runnerDeps: { resultOutbox: outbox },
    heartbeatIntervalMs: 5, backoffBaseMs: 5, onEvent() {} })
  const running = loop.run()
  try {
    await until(() => heartbeats >= 3 && claims >= 2 && resultCalls === 1)
    assert.equal(resultCalls, 1, 'only one replay may own a receipt at a time')
  } finally { loop.shutdown(); await running }
  assert.deepEqual(outbox.load('pending').payload, payload, 'shutdown keeps the original pending send evidence')
})

test('each replay pass is bounded and excludes the active invocation while other pending entries progress', async t => {
  const outbox = fixture(t)
  for (const id of ['active', 'pending-1', 'pending-2', 'pending-3']) {
    outbox.saveLegacy(id, { claim_token: 'fixture-' + id, success: true, effect: 'none' })
  }
  const calls = []
  const api = { async result(id) { calls.push(id) } }
  const first = await replayPendingOutbox(api, outbox, { maxEntries: 2, maxAttempts: 1,
    canReplay: id => id !== 'active', emit() {} })
  assert.equal(first.acked, 2); assert.equal(first.kept, 2)
  assert.equal(calls.length, 2); assert.ok(!calls.includes('active'))
  await replayPendingOutbox(api, outbox, { maxEntries: 2, maxAttempts: 1,
    canReplay: id => id !== 'active', emit() {} })
  assert.equal(calls.length, 3); assert.ok(outbox.load('active'))
  assert.deepEqual(outbox.loadAll().map(entry => entry.invocation_id), ['active'])
})

test('bounded replay remembers Retry-After and rotates failures so one old receipt cannot starve the rest', async t => {
  const outbox = fixture(t)
  for (const id of ['first', 'second']) outbox.saveLegacy(id, { claim_token: 'fixture', success: false, effect: 'unknown' })
  const calls = []
  const api = { async result(id) { calls.push(id); throw new ApiError(429, 'busy', undefined, undefined, 60_000) } }
  await replayPendingOutbox(api, outbox, { maxEntries: 1, maxAttempts: 1, emit() {} })
  await replayPendingOutbox(api, outbox, { maxEntries: 1, maxAttempts: 1, emit() {} })
  await replayPendingOutbox(api, outbox, { maxEntries: 1, maxAttempts: 1, emit() {} })
  assert.deepEqual(calls, ['first', 'second'])
  assert.ok(Date.parse(outbox.load('first').next_attempt_at) > Date.now() + 50_000)
  assert.equal(outbox.load('first').payload.effect, 'unknown')
})

test('network retries preserve the same original payload rather than creating another tool operation', async t => {
  const outbox = fixture(t)
  const entry = outbox.saveLegacy('unchanged', { claim_token: 'fixture', success: true, effect: 'applied', data: { receipt_id: 'original' } })
  const calls = []
  await deliverOutboxEntry({ async result(id, payload) { calls.push({ id, payload }); throw new NetworkError('offline') } },
    outbox, entry, { maxAttempts: 1, retryBaseMs: 1, emit() {} })
  outbox.markAttempt('unchanged', 'test retry now', 0)
  await replayPendingOutbox({ async result(id, payload) { calls.push({ id, payload }) } }, outbox,
    { maxEntries: 1, maxAttempts: 1, emit() {} })
  assert.equal(calls.length, 2); assert.deepEqual(calls[0], calls[1]); assert.equal(outbox.load('unchanged'), null)
})

test('session-task and ordinary invocation lanes share one delivery owner, so periodic replay cannot race their ACK write', async t => {
  const outbox = fixture(t)
  const entry = outbox.saveLegacy('other-lane', { claim_token: 'original', success: true, effect: 'applied' })
  let calls = 0, finish
  const api = { async result() { calls++; await new Promise(resolve => { finish = resolve }) } }
  const original = deliverOutboxEntry(api, outbox, entry)
  const replay = await replayPendingOutbox(api, outbox, { maxEntries: 8, maxAttempts: 1, emit() {} })
  assert.equal(replay.kept, 1); assert.equal(calls, 1)
  const duplicate = deliverOutboxEntry(api, outbox, entry)
  assert.equal(duplicate, original)
  finish()
  assert.equal(await original, 'acked'); assert.equal(await duplicate, 'acked')
  assert.equal(calls, 1); assert.equal(outbox.load('other-lane'), null)
})

async function httpFixture(t, handler) {
  const server = createServer(handler)
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(async () => {
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
  })
  return new ApiClient(`http://127.0.0.1:${server.address().port}`, 'fixture')
}

test('shutdown during a partial HTTP 200 body retains the unknown receipt instead of treating headers as ACK', async t => {
  const outbox = fixture(t)
  const payload = { claim_token: 'fixture', success: false, effect: 'unknown' }
  const entry = outbox.saveLegacy('partial-body', payload)
  let received
  const incoming = new Promise(resolve => { received = resolve })
  const api = await httpFixture(t, (req, res) => {
    req.resume()
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.write('{')
    res.flushHeaders()
    received()
  })
  const controller = new AbortController()
  const delivery = deliverOutboxEntry(api, outbox, entry, { shutdownSignal: controller.signal, emit() {} })
  await incoming
  await new Promise(resolve => setTimeout(resolve, 30))
  controller.abort()
  await delivery
  assert.deepEqual(outbox.load('partial-body')?.payload, payload, 'an incomplete body cannot acknowledge original send evidence')
})

test('a disconnected HTTP response body is a transport failure and preserves its original receipt', async t => {
  const outbox = fixture(t)
  const payload = { claim_token: 'fixture', success: false, effect: 'unknown' }
  const entry = outbox.saveLegacy('disconnected-body', payload)
  const api = await httpFixture(t, (req, res) => {
    req.resume()
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.write('{')
    setTimeout(() => res.destroy(), 30)
  })
  assert.equal(await deliverOutboxEntry(api, outbox, entry, { maxAttempts: 1, emit() {} }), 'retry-exhausted')
  assert.deepEqual(outbox.load('disconnected-body')?.payload, payload)
})

test('complete empty and non-JSON success bodies retain the legacy ACK compatibility', async t => {
  const api = await httpFixture(t, (req, res) => { req.resume(); res.end(req.url.includes('plain') ? 'OK' : '') })
  await api.result('empty', { claim_token: 'fixture', success: true, effect: 'none' })
  await api.result('plain', { claim_token: 'fixture', success: true, effect: 'none' })
})
