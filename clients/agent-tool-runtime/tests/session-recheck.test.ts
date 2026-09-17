/**
 * C3 门禁 #2（Runtime 侧）：锁内会话复核的输入版本硬门禁。
 *
 * 场景（等价评审独立 Node 复现）：决策绑定冻结 input_version=1，本地事实已推进
 * 到版本 2（新批次落盘，可能尚未 ACK），或聚合中存在 pendingBatch——旧发送必须
 * 在锁内复核被阻断（许可/发送 0）；版本一致且无新消息时放行一次。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { SessionTaskEngine, type ObserverResult } from '../src/sessionTasks/engine.js'
import { SessionStore } from '../src/sessionTasks/sessionStore.js'
import type { SessionCrypto } from '../src/sessionTasks/sessionStore.js'

function fakeCrypto(): SessionCrypto {
  return {
    protect: async (plain: string) => Buffer.from(plain, 'utf8').toString('base64'),
    unprotect: async (cipher: string) => Buffer.from(cipher, 'base64').toString('utf8'),
  }
}

/** 空窗口观察（无新消息）：版本门禁通过后复核观察放行 */
const emptyObserver = async (): Promise<ObserverResult> => ({
  observation_id: 'ok',
  account_identity_version: 1,
  conversation_binding_id: 'b1',
  binding_version: 0,
  observed_at: new Date().toISOString(),
  coverage: 'complete_window',
  ordered_messages: [],
  window_fingerprint: 'fp',
  gap_reason: null,
})

interface TaskHandle {
  setVersion(v: number, pending: boolean): void
  setGate(gate: string, leaseDeadline?: number): void
  recheck(frozen?: number): Promise<void>
}

/** 构造带注入任务的引擎（不经 claim/网络）：直接操纵内部 tasks map */
async function makeEngine(options: { nameTarget?: boolean; observer?: typeof emptyObserver } = {}): Promise<{ engine: SessionTaskEngine; task: TaskHandle; home: string; cleanup: () => void }> {
  const home = mkdtempSync(join(tmpdir(), 'st-recheck-'))
  const engine = new SessionTaskEngine({
    api: { sessionTaskClaim: async () => null } as never,
    runtimeHome: home,
    crypto: fakeCrypto(),
    runtimeInstanceId: `rt-${randomUUID().slice(0, 8)}`,
    observer: options.observer ?? emptyObserver,
    emit: () => {},
  })
  const store = new SessionStore({ runtimeHome: home, assignmentId: 'a1', crypto: fakeCrypto() })
  const anyEngine = engine as unknown as {
    tasks: Map<string, Record<string, unknown>>
    lockedSessionRecheck(task: Record<string, unknown>, frozen?: number): Promise<void>
  }
  const task: Record<string, unknown> = {
    taskId: 't1',
    assignmentId: 'a1',
    conversationBindingId: 'b1',
    fence: 1,
    controlEpoch: 1,
    serverControlSeq: 0,
    specRevision: 1,
    spec: options.nameTarget ? { _runtime_target: { policy: 'current_login_name', target_name: 'SyntheticContact' } } : {},
    store,
    phase: 'executing',
    watermark: { last_local_message_id: 'm2', window_fingerprint: 'f' },
    inputVersion: 2,
    pendingBatch: null,
    decidedBatchIds: new Set(),
    inFlight: null,
    sendReady: null,
    execution: null,
    lastObservationAt: 0,
    observeDueAt: Number.MAX_SAFE_INTEGER,
    observeBackoffIndex: 0,
    renewDueAt: Number.MAX_SAFE_INTEGER,
    pendingEvents: new Map(),
    syncNextAttemptAt: 0,
    syncBackoff: 50,
    busy: false,
    submitting: false,
    gate: 'open',
    leaseDeadline: Date.now() + 600_000,
    decisionQueue: [],
    batchVersionById: new Map(),
    decidedByDecisionIds: new Set(),
    expectedBindingVersion: 0,
    expectedAccountIdentityVersion: 1,
    gapStreak: 0,
    metaPersistPending: false,
    lastKnownControlStatus: 'active',
  }
  anyEngine.tasks.set('t1', task)
  return {
    engine,
    home,
    task: {
      setVersion(v: number, pending: boolean) {
        task['inputVersion'] = v
        task['pendingBatch'] = pending
          ? { batchId: 'b-new', messages: [], firstNewAt: Date.now() }
          : null
      },
      recheck(frozen?: number) {
        return anyEngine.lockedSessionRecheck(task, frozen)
      },
      setGate(gate: string, leaseDeadline = Date.now() + 600_000) {
        task['gate'] = gate
        task['leaseDeadline'] = leaseDeadline
      },
    },
    cleanup: () => rmSync(home, { recursive: true, force: true }),
  }
}

test('本地输入版本（2）高于冻结版本（1）→ 复核拒绝（旧发送不得执行）', async (t) => {
  const { task, cleanup } = await makeEngine()
  t.after(cleanup)
  task.setVersion(2, false)
  await assert.rejects(() => task.recheck(1), (err: Error & { code?: string }) => {
    assert.equal(err.name, 'SessionPrecheckError')
    assert.equal(err.code, 'INPUT_VERSION_STALE')
    return true
  })
})

test('名称发送不重复观察，旧绑定发送仍执行观察', async (t) => {
  for (const nameTarget of [true, false]) {
    let observations = 0
    const { task, cleanup } = await makeEngine({ nameTarget, observer: async () => {
      observations++
      return emptyObserver()
    } })
    t.after(cleanup)
    task.setVersion(1, false)
    await task.recheck(1)
    assert.equal(observations, nameTarget ? 0 : 1)
  }
})

test('名称发送跳过观察仍拒绝过时版本、待聚合消息、关闭门禁和过期租约', async (t) => {
  const cases = [
    { version: 2, pending: false, gate: 'open', expired: false, code: 'INPUT_VERSION_STALE' },
    { version: 1, pending: true, gate: 'open', expired: false, code: 'INPUT_VERSION_STALE' },
    { version: 1, pending: false, gate: 'closed', expired: false, code: 'ASSIGNMENT_STALE' },
    { version: 1, pending: false, gate: 'open', expired: true, code: 'ASSIGNMENT_STALE' },
  ]
  for (const scenario of cases) {
    const { task, cleanup } = await makeEngine({ nameTarget: true, observer: async () => {
      assert.fail('名称路径不得重新观察')
    } })
    t.after(cleanup)
    task.setVersion(scenario.version, scenario.pending)
    task.setGate(scenario.gate, scenario.expired ? Date.now() - 1 : Date.now() + 600_000)
    await assert.rejects(() => task.recheck(1), (err: Error & { code?: string }) => {
      assert.equal(err.code, scenario.code)
      return true
    })
  }
})

test('聚合中批次（pendingBatch）存在 → 复核拒绝（即使版本号尚未推进）', async (t) => {
  const { task, cleanup } = await makeEngine()
  t.after(cleanup)
  task.setVersion(1, true)
  await assert.rejects(() => task.recheck(1), (err: Error & { code?: string }) => {
    assert.equal(err.code, 'INPUT_VERSION_STALE')
    return true
  })
})

test('版本一致且无聚合批次 → 复核放行（正常发送一次的路径）', async (t) => {
  const { task, cleanup } = await makeEngine()
  t.after(cleanup)
  task.setVersion(1, false)
  await task.recheck(1) // 不抛即通过；观察在版本门禁之后才发生（注入 observer 抛错可佐证顺序）
})
