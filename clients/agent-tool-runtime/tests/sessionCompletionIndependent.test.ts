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

test('independent: newly claimed terminal control plus final event ACK establishes completion only after drain', { skip: process.platform !== 'win32' }, async t => {
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
    assert.equal(await hasSessionCompletion(directory, assignment.assignment_id, systemCrypto), false, 'ACK marker alone cannot settle the original business task')
    await engine.drain()
    assert.ok(batches > 0)
    assert.equal(await hasSessionCompletion(directory, assignment.assignment_id, systemCrypto), true)
    writeFileSync(join(directory, 'events.jsonl'), readFileSync(join(directory, 'events.jsonl'), 'utf8') + '\n')
    assert.equal(await hasSessionCompletion(directory, assignment.assignment_id, systemCrypto), false, 'Changed session facts invalidate the former completion proof')
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
    assert.equal(await hasSessionCompletion(directory, assignment.assignment_id, systemCrypto), false, 'Even full recovery ACK waits for drain before business settlement')
    await recovered.drain()
    assert.equal(recoveryBatches, 1)
    assert.equal(await hasSessionCompletion(directory, assignment.assignment_id, systemCrypto), true)
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
    })
  }
})

test('independent: managed entry exposes readonly startup failure and can exit without changing old pairing facts', { skip: process.platform !== 'win32' }, async t => {
  const home = temporary(t)
  writeFileSync(join(home, 'pairing.pending.json'), '{synthetic-corrupt-transaction')
  const child = fork(resolve('dist/src/managed-entry.js'), [], { env: { ...process.env, AIDWORK_RUNTIME_HOME: home }, stdio: ['ignore', 'ignore', 'ignore', 'ipc'] })
  const exited = once(child, 'exit')
  const request = (frame: Record<string, unknown>): Promise<Record<string, unknown>> => new Promise((accept, reject) => {
    const listener = (value: unknown) => {
      const response = value as Record<string, unknown>
      if (response.request_id !== frame.request_id) return
      clearTimeout(timer); child.off('message', listener); accept(response)
    }
    const timer = setTimeout(() => { child.off('message', listener); reject(new Error('No managed startup response received')) }, 15_000)
    child.on('message', listener)
    child.send(frame)
  })
  try {
    const response = await request({ request_id: 'real-startup-describe', method: 'describe', params: {} })
    assert.deepEqual(Object.keys(response).sort(), ['code', 'error', 'method', 'request_id', 'result'])
    assert.equal(response.method, 'describe'); assert.equal(response.code, 0)
    const state = await request({ request_id: 'readonly-state', method: 'getState', params: {} })
    assert.equal(state.code, 0)
    assert.equal((state.result as Record<string, unknown>).state, 'blocked')
    assert.match(String((state.result as Record<string, unknown>).blocked_reason), /配对资料未能恢复/)
    for (const method of ['start', 'pair', 'plugins.list']) {
      const params = method === 'pair' ? { request_key: 'unused-pair', server: 'https://isolated.invalid', device_name: 'test', pairing_code: 'unused' }
        : method === 'start' ? { request_key: 'unused-start' } : {}
      const rejected = await request({ request_id: `readonly-${method}`, method, params })
      assert.equal(rejected.code, 11); assert.equal(rejected.result, null, 'Initialization failure must not pretend an empty installed inventory')
    }
    assert.equal(readFileSync(join(home, 'pairing.pending.json'), 'utf8'), '{synthetic-corrupt-transaction', 'Startup failure must not overwrite unknown transaction evidence')
    const shutdown = await request({ kind: 'runtime_shutdown_request', request_id: 'readonly-exit', instance_id: (response.result as Record<string, unknown>).instance_id })
    assert.equal(shutdown.code, 0)
    await bounded(exited); assert.equal(child.exitCode, 0)
  } finally { if (child.exitCode === null) { child.kill(); await exited }; await delay(1) }
})
