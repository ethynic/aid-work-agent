/** Independent business regressions: settlement comes from the drained server ACK chain. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { fork } from 'node:child_process'
import { once } from 'node:events'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { ApiError, type ApiClient } from '../src/apiClient.js'
import { LegacyExecution } from '../src/runtimeHost.js'
import { dpapiProtect, dpapiUnprotect } from '../src/dpapi.js'
import { SessionTaskEngine, type ObserverResult } from '../src/sessionTasks/engine.js'
import { SessionStore, sessionTaskDir, type SessionCrypto } from '../src/sessionTasks/sessionStore.js'
import { hasSessionCompletion } from '../src/sessionTasks/completionProof.js'

const isolatedCrypto: SessionCrypto = { protect: async value => Buffer.from(value).toString('base64'), unprotect: async value => Buffer.from(value, 'base64').toString() }
const systemCrypto: SessionCrypto = { protect: dpapiProtect, unprotect: dpapiUnprotect }
const assignment = { task_id: 'independent-task', assignment_id: 'independent-assignment', fence: 1, control_epoch: 1,
  spec_revision: 1, lease_seconds: 60, conversation_binding_id: 'isolated-binding', binding_version: 0,
  account_identity_version: 1, spec: {} }
const terminal = { status: 'stopped', control_epoch: 2, server_control_seq: 1 }
function observe(): ObserverResult {
  return { observation_id: 'independent-observation', account_identity_version: 1, conversation_binding_id: 'isolated-binding',
    binding_version: 0, observed_at: new Date().toISOString(), coverage: 'complete_window', ordered_messages: [], window_fingerprint: 'test', gap_reason: null }
}
function temporary(t: { after(fn: () => void): void }): string {
  const home = mkdtempSync(join(tmpdir(), 'runtime-independent-completion-'))
  t.after(() => { assert.ok(resolve(home).startsWith(resolve(tmpdir()) + sep)); rmSync(home, { recursive: true, force: true }) })
  return home
}
async function bounded(work: Promise<unknown>): Promise<void> {
  let timer: NodeJS.Timeout | undefined
  try { await Promise.race([work, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Independent engine lifecycle exceeded 15 seconds')), 15_000) })]) }
  finally { clearTimeout(timer) }
}

test('independent: newly claimed terminal control plus final event ACK permits plugin changes after drain', { skip: process.platform !== 'win32' }, async t => {
  const home = temporary(t); let claimed = false; let batches = 0
  const engine = new SessionTaskEngine({ runtimeHome: home, runtimeInstanceId: 'independent', crypto: systemCrypto, observer: async () => observe(),
    api: {
      sessionTaskClaim: async () => { if (claimed) return null; claimed = true; return assignment },
      sessionTaskEvents: async (_: string, payload: { records: { local_seq: number }[] }) => {
        batches++; engine.shutdown()
        return { ack_seq: payload.records.at(-1)!.local_seq, control: terminal }
      },
    } as unknown as ApiClient,
  })
  try {
    await bounded(engine.run())
    const directory = sessionTaskDir(home, assignment.assignment_id)
    assert.ok(existsSync(join(directory, '.acked')), 'The normal engine writes its ACK marker before drain')
    assert.equal(await new LegacyExecution(process.execPath, home).canChangePlugins(), false, 'ACK marker alone cannot authorize changing the executable')
    await engine.drain()
    assert.ok(batches > 0)
    assert.equal(await hasSessionCompletion(directory, assignment.assignment_id, systemCrypto), true)
    assert.equal(await new LegacyExecution(process.execPath, home).canChangePlugins(), true, 'The drained original terminal ACK permits a normal upgrade')
    writeFileSync(join(directory, 'events.jsonl'), readFileSync(join(directory, 'events.jsonl'), 'utf8') + '\n')
    assert.equal(await new LegacyExecution(process.execPath, home).canChangePlugins(), false, 'Changed session facts invalidate the former authorization')
  } finally { engine.shutdown() }
})

test('independent: retired partial-ACK task stays blocked until a restarted recovery receives the final terminal ACK', { skip: process.platform !== 'win32' }, async t => {
  const home = temporary(t); let claimed = false; let batches = 0
  const engine = new SessionTaskEngine({ runtimeHome: home, runtimeInstanceId: 'independent-retired', crypto: systemCrypto, observer: async () => observe(),
    api: {
      sessionTaskClaim: async () => { if (claimed) return null; claimed = true; return assignment },
      sessionTaskEvents: async (_: string, payload: { records: { local_seq: number }[] }) => {
        batches++
        if (batches === 1) { assert.ok(payload.records.length > 0); return { ack_seq: payload.records[0]!.local_seq - 1, control: terminal } }
        engine.shutdown(); throw new ApiError(409, 'Synthetic stale assignment')
      },
    } as unknown as ApiClient,
  })
  try {
    await bounded(engine.run()); await engine.drain()
    assert.equal(engine.runningTaskCount, 0, 'The real stale-event path removes the task from scheduling')
    assert.equal(engine['retiredCompletionCandidates'].size, 1, 'The dropped terminal task remains a settlement candidate')
    const directory = sessionTaskDir(home, assignment.assignment_id)
    assert.equal(await hasSessionCompletion(directory, assignment.assignment_id, systemCrypto), false, 'Retirement cannot invent an ACK for remaining facts')
    assert.equal(await new LegacyExecution(process.execPath, home).canChangePlugins(), false)
    let recoveryBatches = 0
    const recovered = new SessionTaskEngine({ runtimeHome: home, runtimeInstanceId: 'independent-recovery', crypto: systemCrypto,
      observer: async () => { throw new Error('Terminal recovery cannot invoke desktop observation') },
      api: {
        sessionTaskEvents: async (_: string, payload: { records: { local_seq: number }[] }) => {
          recoveryBatches++; recovered.shutdown(); return { ack_seq: payload.records.at(-1)!.local_seq, control: terminal }
        },
        sessionTaskRenew: async () => { throw new ApiError(409, 'Synthetic retired lease') },
      } as unknown as ApiClient,
    })
    await bounded(recovered.run())
    assert.equal(recovered['recoveryCompletionCandidates'].size, 1)
    assert.equal(await new LegacyExecution(process.execPath, home).canChangePlugins(), false, 'Even full recovery ACK waits for drain before authorization')
    await recovered.drain()
    assert.equal(recoveryBatches, 1)
    assert.equal(await new LegacyExecution(process.execPath, home).canChangePlugins(), true)
  } finally { engine.shutdown() }
})

test('independent: recovery rejects active ACK, stale terminal epoch and unresolved unknown execution even with forged .acked', async t => {
  for (const scenario of ['active', 'stale-terminal', 'unknown-execution']) {
    await t.test(scenario, async t => {
      const home = temporary(t); const store = new SessionStore({ runtimeHome: home, assignmentId: assignment.assignment_id, crypto: isolatedCrypto })
      await store.writeMeta({ ...assignment, control_epoch: 2 })
      await store.appendEncrypted('independent-event', scenario === 'unknown-execution' ? 'execution_phase' : 'phase',
        scenario === 'unknown-execution' ? { phase_to: 'executing', invocation_id: 'old-unknown-invocation', decision_id: 'old-decision', input_version: 1 } : { to: 'waiting_peer' }, 1)
      writeFileSync(join(sessionTaskDir(home, assignment.assignment_id), '.acked'), '999')
      const control = scenario === 'active' ? { ...terminal, status: 'active' } : scenario === 'stale-terminal' ? { ...terminal, control_epoch: 1 } : terminal
      const engine = new SessionTaskEngine({ runtimeHome: home, runtimeInstanceId: 'independent-reject', crypto: isolatedCrypto,
        observer: async () => { throw new Error('Unsettled recovery cannot observe') },
        api: {
          sessionTaskEvents: async (_: string, payload: { records: { local_seq: number }[] }) => {
            engine.shutdown(); return { ack_seq: payload.records.at(-1)!.local_seq, control }
          },
          sessionTaskRenew: async () => { throw new ApiError(409, 'Synthetic expired lease') },
        } as unknown as ApiClient,
      })
      await bounded(engine.run()); await engine.drain()
      assert.equal(await hasSessionCompletion(sessionTaskDir(home, assignment.assignment_id), assignment.assignment_id, isolatedCrypto), false,
        'Only matching terminal control and settled execution can produce trusted completion')
      assert.equal(await new LegacyExecution(process.execPath, home).canChangePlugins(), false)
    })
  }
})

test('independent: managed entry replies to the real early describe with structured startup failure and exits', { skip: process.platform !== 'win32' }, async t => {
  const home = temporary(t)
  writeFileSync(join(home, 'pairing.pending.json'), '{synthetic-corrupt-transaction')
  const child = fork(resolve('dist/src/managed-entry.js'), [], { env: { ...process.env, AIDWORK_RUNTIME_HOME: home }, stdio: ['ignore', 'ignore', 'ignore', 'ipc'] })
  const exited = once(child, 'exit')
  try {
    const message = new Promise<Record<string, unknown>>((accept, reject) => {
      const timer = setTimeout(() => reject(new Error('No structured startup failure received')), 15_000)
      child.on('message', value => { const response = value as Record<string, unknown>; if (response.request_id === 'real-startup-describe') { clearTimeout(timer); accept(response) } })
    })
    child.send({ request_id: 'real-startup-describe', method: 'describe', params: {} })
    const response = await message
    assert.deepEqual(Object.keys(response).sort(), ['code', 'error', 'method', 'request_id', 'result'])
    assert.equal(response.method, 'describe'); assert.equal(response.code, 11); assert.equal(response.result, null)
    assert.ok(typeof response.error === 'string' && response.error.length > 0)
    assert.equal((await exited)[0], 1)
    assert.equal(readFileSync(join(home, 'pairing.pending.json'), 'utf8'), '{synthetic-corrupt-transaction', 'Startup failure must not overwrite unknown transaction evidence')
  } finally { if (child.exitCode === null) { child.kill(); await exited }; await delay(1) }
})
