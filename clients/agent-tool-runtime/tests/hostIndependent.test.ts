/** Independent A1 regressions: stop is an admission boundary, not result cancellation. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { fork, spawn, type ChildProcess } from 'node:child_process'
import { once } from 'node:events'
import { mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'
import { randomUUID } from 'node:crypto'
import { ApiClient } from '../src/apiClient.js'
import { SessionTaskEngine, type ObserverResult } from '../src/sessionTasks/engine.js'
import { SessionStore, type SessionCrypto } from '../src/sessionTasks/sessionStore.js'
import { startTestStack } from './helpers/runtimeStack.js'
import { FakeCloud } from './helpers/fakeCloud.js'
import { main } from '../src/cli.js'
import { appendJournalEntry } from '../src/journal.js'
import { ResultOutbox, resultOutboxDir } from '../src/resultOutbox.js'
import { openRuntimeHost } from '../src/runtimeHost.js'
import { ManagementError } from '@aid/local-tool-host-core'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
async function waitFor(predicate: () => boolean, label: string, timeout = 10_000): Promise<void> {
  const end = Date.now() + timeout
  while (!predicate()) {
    assert.ok(Date.now() < end, `timeout: ${label}`)
    await delay(10)
  }
}
const crypto: SessionCrypto = {
  protect: async value => Buffer.from(value).toString('base64'),
  unprotect: async value => Buffer.from(value, 'base64').toString(),
}
const assignment = {
  task_id: randomUUID(), assignment_id: randomUUID(), fence: 1, control_epoch: 1,
  spec_revision: 1, lease_seconds: 60, conversation_binding_id: 'test-binding',
  binding_version: 0, account_identity_version: 1, spec: { opening_text: 'test opening' },
}
function observation(): ObserverResult {
  return { observation_id: randomUUID(), account_identity_version: 1,
    conversation_binding_id: 'test-binding', binding_version: 0, observed_at: new Date().toISOString(),
    coverage: 'complete_window', ordered_messages: [], window_fingerprint: 'baseline', gap_reason: null }
}
function sessionApi() {
  let claimed = false
  let ackedSeq = 0
  const eventTypes: string[] = []
  const api = {
    sessionTaskClaim: async () => { if (claimed) return null; claimed = true; return assignment },
    sessionTaskRenew: async () => ({ lease_seconds: 60, control: { status: 'active', control_epoch: 1, server_control_seq: 0 } }),
    sessionTaskEvents: async (_id: string, payload: { records: Array<{ local_seq: number; type: string }> }) => {
      eventTypes.push(...payload.records.map(record => record.type))
      ackedSeq = Math.max(ackedSeq, payload.records.at(-1)?.local_seq ?? 0)
      return { ack_seq: payload.records.at(-1)?.local_seq ?? 0, control: { status: 'active', control_epoch: 1, server_control_seq: 0 } }
    },
    sessionTaskCreateDecision: async () => ({ decision_id: 'test-decision', status: 'pending' }),
    sessionTaskGetDecision: async () => ({ decision_id: 'test-decision', status: 'ready', action: 'reply' }),
    sessionTaskPrepareSend: async () => ({ invocation_id: 'test-invocation', decision_status: 'ready', run_id: null }),
    sessionTaskClaimInvocation: async () => ({ invocation: { invocation_id: 'test-invocation', tool_name: 'weixin_message_send_v2', arguments: {},
      claim_token: 'test-only-claim', lease_expires_at: new Date(Date.now() + 60_000).toISOString(), provider: 'weixin' }, state: 'claimed' }),
  }
  return { api, eventTypes, ackedSeq: () => ackedSeq }
}

test('independent: shutdown during claim prevents a late assignment from starting desktop observation', async () => {
  const home = mkdtempSync(join(tmpdir(), 'host-independent-claim-'))
  const claim = deferred<typeof assignment>()
  let entered = false, observations = 0
  const fake = sessionApi()
  fake.api.sessionTaskClaim = async () => { entered = true; return claim.promise }
  const engine = new SessionTaskEngine({ api: fake.api as unknown as ApiClient, runtimeHome: home, crypto,
    runtimeInstanceId: 'independent', observer: async () => { observations++; return observation() } })
  const running = engine.run()
  try {
    await waitFor(() => entered, 'claim entered')
    engine.shutdown()
    claim.resolve(assignment)
    await running
    await engine.drain()
    assert.equal(observations, 0, 'Stop must close desktop admission even when an earlier claim responds late')
  } finally { engine.shutdown(); claim.resolve(assignment); await running; rmSync(home, { recursive: true, force: true }) }
})

test('independent: an event ACK returning after shutdown cannot submit a new opening decision', async () => {
  const home = mkdtempSync(join(tmpdir(), 'host-independent-sync-'))
  const sync = deferred<void>()
  let syncEntered = false, decisions = 0
  const fake = sessionApi()
  const ackEvents = fake.api.sessionTaskEvents
  fake.api.sessionTaskEvents = async (id, payload) => {
    syncEntered = true; await sync.promise
    return ackEvents(id, payload)
  }
  fake.api.sessionTaskCreateDecision = async () => { decisions++; return { decision_id: 'test-decision', status: 'pending' } }
  const engine = new SessionTaskEngine({ api: fake.api as unknown as ApiClient, runtimeHome: home, crypto,
    runtimeInstanceId: 'independent', observer: async () => observation() })
  const running = engine.run()
  try {
    await waitFor(() => syncEntered, 'baseline sync entered')
    engine.shutdown(); sync.resolve()
    await running; await engine.drain()
    assert.equal(decisions, 0, 'ACK must remain deliverable after Stop without opening a new cloud decision')
  } finally { engine.shutdown(); sync.resolve(); await running; rmSync(home, { recursive: true, force: true }) }
})

test('independent: drain awaits an accepted session runner and ACKs its final execution fact', async () => {
  const home = mkdtempSync(join(tmpdir(), 'host-independent-session-'))
  const runner = deferred<void>()
  let accepted = false, drained = false
  const fake = sessionApi()
  const engine = new SessionTaskEngine({ api: fake.api as unknown as ApiClient, runtimeHome: home, crypto,
    runtimeInstanceId: 'independent', observer: async () => observation(),
    runInvocation: async () => { accepted = true; await runner.promise } })
  const running = engine.run()
  try {
    await waitFor(() => accepted, 'session runner accepted')
    engine.shutdown()
    const draining = engine.drain().then(() => { drained = true })
    await delay(80)
    assert.equal(drained, false, 'Stop must wait for the accepted result chain')
    runner.resolve(); await running; await draining
    const store = new SessionStore({ runtimeHome: home, assignmentId: assignment.assignment_id, crypto })
    const replayed = await store.replay()
    const final = replayed.events.find(event => event.record.type === 'execution_phase' && (event.payload as Record<string, unknown>).outcome === 'reported')
    assert.ok(final, 'Accepted completion must survive restart in the durable session log')
    assert.ok(fake.ackedSeq() >= final.record.local_seq, 'Stop must flush completion through the original event ACK chain')
  } finally { engine.shutdown(); runner.resolve(); await running; rmSync(home, { recursive: true, force: true }) }
})

test('independent: PollLoop drain preserves progress and successful result while leaving the next invocation queued', async () => {
  const stack = await startTestStack({ progressIntervalMs: 40, heartbeatIntervalMs: 40 })
  try {
    const accepted = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 800, stepMs: 80 })
    await stack.cloud.waitFor(() => stack.cloud.callsFor(accepted).some(call => call.type === 'progress'), 10_000, 'accepted progress')
    const next = stack.cloud.enqueueInvocation('boss_greet', { durationMs: 10 })
    const stoppedAt = Date.now()
    const heartbeatsAtStop = stack.cloud.heartbeatCount(stack.token)
    await stack.loop.drain(); await stack.runPromise
    const result = stack.cloud.callsFor(accepted).find(call => call.type === 'result')
    assert.equal(result?.payload.success, true, 'Stop must not turn an accepted desktop action into CANCELLED')
    assert.ok(stack.cloud.callsFor(accepted).some(call => call.type === 'progress' && call.at > stoppedAt), 'Progress must remain live while draining')
    assert.ok(stack.cloud.heartbeatCount(stack.token) > heartbeatsAtStop, 'Heartbeats must stay alive while an action drains')
    assert.equal(stack.cloud.getInvocation(next)?.state, 'queued', 'Stop closes new claim admission')
  } finally { await stack.stop() }
})

test('independent: real managed child uses inherited IPC, excludes missing providers and releases lease only after parent disconnect', { skip: process.platform !== 'win32' }, async () => {
  const home = mkdtempSync(join(tmpdir(), 'host-independent-ipc-'))
  const cloud = new FakeCloud(); await cloud.start(); cloud.issueCode('IPC-INDEPENDENT')
  writeFileSync(join(home, 'config.json'), JSON.stringify({ server: cloud.baseUrl, device_id: 'old-test-device',
    providers: { weixin: { entry: join(home, 'absent-provider.js') } } }))
  const sourceDir = join(dirname(fileURLToPath(import.meta.url)), '../src')
  const env = { ...process.env, AIDWORK_RUNTIME_HOME: home }
  const child: ChildProcess = fork(join(sourceDir, 'managed-entry.js'), [], { env, stdio: ['ignore', 'pipe', 'pipe', 'ipc'] })
  const messages: Array<Record<string, any>> = []
  let diagnostics = ''
  child.on('message', message => messages.push(message as Record<string, any>))
  child.stderr?.on('data', chunk => { diagnostics += String(chunk) })
  let exited = false
  const exit = once(child, 'exit').then(([code]) => { exited = true; return code })
  async function request(method: string, params: Record<string, string> = {}) {
    const id = randomUUID(); child.send({ request_id: id, method, params })
    await waitFor(() => messages.some(message => message.request_id === id), `${method} reply; ${diagnostics}`)
    return messages.find(message => message.request_id === id)!
  }
  async function finishOperation(reply: Record<string, any>) {
    assert.equal(reply.code, 0)
    let operation = reply.result
    const deadline = Date.now() + 10_000
    while (operation.status === 'running') {
      assert.ok(Date.now() < deadline, 'Management operation must settle instead of silently leaving the test waiting')
      await delay(20)
      const poll = await request('operations.get', { operation_id: operation.operation_id })
      assert.equal(poll.code, 0); operation = poll.result
    }
    assert.equal(operation.status, 'succeeded', JSON.stringify(operation))
  }
  try {
    await waitFor(() => messages.some(message => message.event === 'state_changed'), `initial event; ${diagnostics}`)
    const describe = await request('describe')
    assert.equal(describe.result.api_major, 1)
    const state = await request('getState')
    assert.equal(state.result.instance_id, describe.result.instance_id)
    assert.equal(state.result.state, 'stopped')
    const inventory = await request('plugins.list')
    assert.deepEqual(inventory.result.plugins, [])
    const unsupported = await request('plugins.import', { request_key: 'independent-import', selection_ref: 'trusted-unused-ref' })
    assert.equal(unsupported.code, 3)
    await finishOperation(await request('pair', { request_key: 'independent-pair', server: cloud.baseUrl, device_name: 'IPC fixture', pairing_code: 'IPC-INDEPENDENT' }))
    await finishOperation(await request('start', { request_key: 'independent-start' }))
    const queued = cloud.enqueueInvocation('weixin_probe', {}, { provider: 'weixin' })
    await cloud.waitFor(() => cloud.heartbeatCount(cloud.lastPairToken!) > 0, 10_000, 'empty-host heartbeat')
    await delay(150)
    assert.equal(cloud.getInvocation(queued)?.state, 'queued', 'Missing provider must never claim a queued desktop task')
    const cli = spawn(process.execPath, [join(sourceDir, 'cli.js'), 'unpair'], { env, stdio: ['ignore', 'pipe', 'pipe'] })
    const [cliCode] = await once(cli, 'exit')
    assert.equal(cliCode, 1, 'CLI must respect the same-home managed Host lease')
    assert.ok(readFileSync(join(home, 'credentials.bin'), 'utf8').length > 0, 'Conflicting CLI cannot remove live device credentials')
    assert.equal(exited, false)
    child.disconnect()
    await waitFor(() => exited, `parent disconnect exit; ${diagnostics}`)
    assert.equal(await exit, 0, `Parent disconnect must drain and exit; ${diagnostics}`)
    if (process.env.AIDWORK_INDEPENDENT_IPC_TRACE) writeFileSync(process.env.AIDWORK_INDEPENDENT_IPC_TRACE, JSON.stringify(messages))
  } finally {
    if (!exited) { child.kill(); await exit }
    await cloud.stop(); rmSync(home, { recursive: true, force: true })
  }
})

test('independent: unresolved historical facts prevent pair/unpair without overwriting the old identity', { skip: process.platform !== 'win32' }, async t => {
  for (const scenario of ['journal-before-outbox', 'journal-after-result-ack', 'session-memory-acked', 'gave-up-outbox', 'corrupt-outbox']) {
    await t.test(scenario, async () => {
      const home = mkdtempSync(join(tmpdir(), 'host-independent-identity-'))
      const previousHome = process.env.AIDWORK_RUNTIME_HOME
      process.env.AIDWORK_RUNTIME_HOME = home
      const cloud = new FakeCloud(); await cloud.start()
      try {
        cloud.issueCode('OLD-IDENTITY')
        assert.equal(await main(['pair', '--code', 'OLD-IDENTITY', '--server', cloud.baseUrl]), 0)
        const config = readFileSync(join(home, 'config.json'), 'utf8')
        const cipher = readFileSync(join(home, 'credentials.bin'), 'utf8')
        const oldToken = cloud.lastPairToken
        if (scenario.startsWith('journal')) {
          appendJournalEntry(home, { ts: new Date().toISOString(), invocation_id: 'historical-invocation',
            request_id: 'historical-request', permit_id: 'historical-permit', phase: 'may_have_started' })
          if (scenario === 'journal-after-result-ack') {
            const outbox = new ResultOutbox(resultOutboxDir(home))
            outbox.save('historical-invocation', { claim_token: 'test-only-claim', request_id: 'historical-request', effect: 'applied', phase: 'verified', safe_to_retry: false })
            outbox.remove('historical-invocation')
          }
        } else if (scenario === 'session-memory-acked') {
          const store = new SessionStore({ runtimeHome: home, assignmentId: 'historical-assignment', crypto })
          const record = await store.appendEncrypted('historical-event', 'execution_phase', { outcome: 'reported', phase_to: 'waiting_peer' }, 1)
          store.ackUpTo(record.local_seq)
          assert.equal(store.pendingSyncCount, 0, 'Memory ACK is deliberately not a durable identity replacement proof')
        } else if (scenario === 'gave-up-outbox') {
          const outbox = new ResultOutbox(resultOutboxDir(home))
          outbox.save('historical-invocation', { claim_token: 'test-only-claim', request_id: 'historical-request', effect: 'unknown', phase: 'may_have_started', safe_to_retry: false })
          outbox.markGaveUp('historical-invocation', 'HTTP 409')
        } else {
          mkdirSync(resultOutboxDir(home), { recursive: true })
          writeFileSync(join(resultOutboxDir(home), 'historical-invocation.json'), '{broken')
        }
        cloud.issueCode('NEW-IDENTITY')
        assert.equal(await main(['pair', '--code', 'NEW-IDENTITY', '--server', cloud.baseUrl]), 1, 'Unsettled facts must block replacement before contacting cloud pairing')
        assert.equal(await main(['unpair']), 1, 'Unsettled facts must block removing the old result identity')
        assert.equal(readFileSync(join(home, 'config.json'), 'utf8'), config)
        assert.equal(readFileSync(join(home, 'credentials.bin'), 'utf8'), cipher)
        assert.equal(cloud.lastPairToken, oldToken, 'Blocked pairing cannot consume a new cloud identity')
      } finally {
        await cloud.stop()
        if (previousHome === undefined) delete process.env.AIDWORK_RUNTIME_HOME
        else process.env.AIDWORK_RUNTIME_HOME = previousHome
        rmSync(home, { recursive: true, force: true })
      }
    })
  }
})

test('independent: an old CLI without a Host lease blocks initialization before transaction or secret access', { skip: process.platform !== 'win32' }, async () => {
  const home = mkdtempSync(join(tmpdir(), 'host-independent-old-cli-'))
  const entry = join(home, 'agent-tool-runtime', 'dist', 'src', 'cli.js')
  mkdirSync(dirname(entry), { recursive: true })
  // Simulate only the historical process identity: no cloud, providers, lease or business action.
  writeFileSync(entry, "process.stdout.write('ready\\n'); setInterval(() => {}, 1000)")
  const config = JSON.stringify({ server: 'http://127.0.0.1:1', device_id: 'old-identity' })
  const pending = '{test-transaction-must-not-be-read'
  const secret = 'test-secret-must-not-be-decrypted'
  writeFileSync(join(home, 'config.json'), config)
  writeFileSync(join(home, 'pairing.pending.json'), pending)
  writeFileSync(join(home, 'management-secret.bin'), secret)
  writeFileSync(join(home, 'credentials.bin'), 'test-old-cipher')
  const child = spawn(process.execPath, [entry, 'start'], { stdio: ['ignore', 'pipe', 'pipe'] })
  const exit = once(child, 'exit')
  let ready = false
  const previousUsername = process.env.USERNAME
  child.stdout?.on('data', chunk => { if (String(chunk).includes('ready')) ready = true })
  try {
    await waitFor(() => ready, 'old CLI alive')
    await assert.rejects(openRuntimeHost({ home }), error => error instanceof ManagementError && error.code === 4,
      'An unverified old CLI must win over corrupt transaction/secret diagnostics because initialization cannot read them yet')
    delete process.env.USERNAME
    await assert.rejects(openRuntimeHost({ home }), error => error instanceof ManagementError && error.code === 4,
      'Missing USERNAME cannot hide a same-user legacy process from the Windows SID check')
    process.env.USERNAME = 'independent-not-the-current-windows-user'
    await assert.rejects(openRuntimeHost({ home }), error => error instanceof ManagementError && error.code === 4,
      'Caller-controlled USERNAME cannot defeat the same-user SID comparison')
    if (previousUsername === undefined) delete process.env.USERNAME
    else process.env.USERNAME = previousUsername
    assert.equal(child.exitCode, null, 'Detection must never kill an existing process')
    assert.equal(readFileSync(join(home, 'config.json'), 'utf8'), config)
    assert.equal(readFileSync(join(home, 'pairing.pending.json'), 'utf8'), pending)
    assert.equal(readFileSync(join(home, 'management-secret.bin'), 'utf8'), secret)
    assert.equal(readFileSync(join(home, 'credentials.bin'), 'utf8'), 'test-old-cipher')
    child.kill(); await exit
    rmSync(join(home, 'pairing.pending.json')); rmSync(join(home, 'management-secret.bin'))
    const host = await openRuntimeHost({ home })
    try { assert.equal(host.getState().state, 'stopped', 'An explicit old process stop permits ordinary existing-identity recovery') }
    finally { await host.dispose() }
  } finally {
    if (previousUsername === undefined) delete process.env.USERNAME
    else process.env.USERNAME = previousUsername
    if (child.exitCode === null) { child.kill(); await exit }
    rmSync(home, { recursive: true, force: true })
  }
})
