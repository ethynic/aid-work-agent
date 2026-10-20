import test from 'node:test'
import assert from 'node:assert/strict'
import { withLocalRecovery, LOCAL_RECOVERY_POLICIES } from '../src/main/boss/localOperationRecovery.js'
import { OPERATIONS } from '../src/main/operations/index.js'
import { okResult, failResult, type BossOperation, type OpContext, type OperationResult } from '../src/main/operations/types.js'
import type { VisualInspectArgs } from '../src/main/boss/visualRecovery.js'

const target = '11111111-1111-4111-8111-111111111111'
const env = { AIDWORK_RUNTIME_SERVER: 'https://backend.invalid', AIDWORK_RUNTIME_DEVICE_TOKEN: 'fake-test-only' }
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })
type Dict = Record<string, unknown>
function original(name = 'boss_filter'): OperationResult {
  return failResult('original', 'UI_CHANGED', '后验未达成', 'none', { browser_target_ref: target,
    operation_recovery: { schema_version: 1, operation_class: 'page_state', browser_target_ref: target,
      operation: { name, goal: '原意图', expected: '稳定后态', result_kind: 'page_state' },
      execution: { stage: 'postcheck', input_status: 'attempted', attempts_used: 2, ordinary_retries_used: 1 },
      verification: { status: 'not_satisfied', evidence: ['原按钮数量未改变'] },
      error: { code: 'UI_CHANGED', source: 'postcheck', summary: '原后验未满足' } } })
}
function visual(obstruction: string, goal = 'not_satisfied', ref?: string): OperationResult {
  return okResult('visual', '观察', 'none', { ...(ref ? { recovery_ref: ref } : {}), visual: { http_status: 200, state: 'completed',
    diagnosis: { obstruction, goal_state: goal, proposed_action: ref ? { kind: 'dismiss' } : null }, receipt: { usage: 1 } } })
}
function harness(sequence: OperationResult[], name = 'boss_filter') {
  const initial = original(name), seen: { args: VisualInspectArgs; ctx: OpContext }[] = [], calls: { args: Dict; ctx: OpContext }[] = []
  const dismissals: string[] = []
  const operation: BossOperation<Dict> = { name, async execute(args, context) {
    calls.push({ args, ctx: context }); return calls.length === 1 ? initial : okResult('resume', '完成', 'none')
  } }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', async execute(args, context) {
    seen.push({ args, ctx: context }); assert.equal(context.browserTargetRef, target)
    const result = sequence.shift(); assert.ok(result, 'unexpected extra paid observation'); return result
  } }
  const dismiss: BossOperation<{ recovery_ref?: string }> = { name: 'boss_overlay_dismiss', async execute(args, context) {
    assert.equal(context.browserTargetRef, target); dismissals.push(args.recovery_ref!); return okResult('dismiss', '输入已投递', 'none')
  } }
  return { initial, seen, calls, dismissals, operation, inspect, dismiss,
    wrapped: withLocalRecovery(operation, { inspect, dismiss, env }) }
}
const meta = (r: OperationResult) => r.data.visual_recovery as Dict

test('all 22 business entries share local ownership and the two primitives cannot recursively recover', async () => {
  assert.equal(Object.keys(LOCAL_RECOVERY_POLICIES).length, 22)
  assert.equal(Object.keys(OPERATIONS).length, 24)
  const primitive: BossOperation<Dict> = { name: 'boss_overlay_inspect', async execute() { throw Error('not called') } }
  assert.equal(withLocalRecovery(primitive), primitive)
  for (const name of Object.keys(LOCAL_RECOVERY_POLICIES)) {
    const r = await withLocalRecovery({ name, async execute() { return failResult('invalid', 'INVALID_ARGUMENT', '拒绝', 'none') } }).execute({}, ctx())
    assert.equal(meta(r).owner, 'cli'); assert.equal(meta(r).schema_version, 1)
    assert.deepEqual(r.data.operation_recovery, { schema_version: 2, owner: 'cli' })
  }
})

test('normal success makes no API call and v2 projection disables legacy server re-recovery', async () => {
  const h = harness([]); h.initial.success = true; h.initial.code = 'OK'
  const r = await h.wrapped.execute({}, ctx())
  assert.equal(h.calls.length, 1); assert.equal(h.seen.length, 0); assert.equal(meta(r).state, 'not_needed')
  assert.equal((r.data.operation_recovery as Dict).schema_version, 2)
  assert.equal((h.initial.data.operation_recovery as Dict).schema_version, 1, 'raw facts must not mutate')
})

test('missing backend config retains original error and explicitly owns the failure without server fallback', async () => {
  const h = harness([])
  const r = await withLocalRecovery(h.operation, { env: {}, inspect: h.inspect }).execute({}, ctx())
  assert.equal(r.code, 'UI_CHANGED'); assert.equal(r.message, h.initial.message); assert.equal(meta(r).state, 'not_configured')
  assert.equal(h.calls.length, 1); assert.equal(h.seen.length, 0)
})

test('fresh postcheck must succeed before resuming exact original arguments and bound tab once', async () => {
  const h = harness([visual('present', 'not_satisfied', 'ref1'), visual('absent', 'satisfied')])
  const args = { rows: [{ value: '本科' }], limit: 3 }
  const r = await h.wrapped.execute(args, ctx())
  assert.equal(r.success, true); assert.equal(meta(r).state, 'recovered'); assert.equal(h.calls.length, 2)
  assert.deepEqual(h.calls[1]!.args, args); assert.equal(h.calls[1]!.ctx.browserTargetRef, target)
  assert.deepEqual(h.dismissals, ['ref1'])
  assert.equal(h.seen[1]!.args.visual_context!.purpose, 'verify'); assert.deepEqual(h.seen[1]!.args.visual_context!.available_actions, [])
  assert.notEqual(h.seen[0]!.args.request_id, h.seen[1]!.args.request_id)
})

test('page acquisition failure uses existing bound window primitive, never another target', async () => {
  const h = harness([failResult('capture', 'CHROME_UNAVAILABLE', 'page blocked', 'none'),
    visual('present', 'not_satisfied', 'windowref'), visual('absent', 'satisfied')])
  const r = await h.wrapped.execute({}, ctx())
  assert.equal(meta(r).state, 'recovered')
  assert.deepEqual(h.seen.map(x => x.args.observation_mode), ['page', 'window', 'window'])
  assert.deepEqual(h.dismissals, ['windowref'])
})

test('unknown receipt uses same id at most twice and never creates a fresh paid window request', async () => {
  const pending = () => okResult('pending', '待取回', 'none', { visual: { http_status: 202, state: 'pending' } })
  const h = harness([pending(), pending(), pending()])
  const r = await h.wrapped.execute({}, ctx())
  assert.equal(meta(r).state, 'diagnosis_unavailable'); assert.equal(h.calls.length, 1)
  assert.equal(h.seen.length, 3); assert.equal(new Set(h.seen.map(x => x.args.request_id)).size, 1)
  assert.deepEqual(h.seen.slice(1).map(x => x.args.retrieve_only), [true, true]); assert.equal(h.dismissals.length, 0)
})

test('postcheck inconclusive cannot become explicit failure or permit original replay', async () => {
  const h = harness([visual('present', 'not_satisfied', 'ref1'), visual('absent', 'inconclusive')])
  const r = await h.wrapped.execute({}, ctx())
  assert.equal(meta(r).state, 'postcheck_inconclusive'); assert.equal(h.calls.length, 1); assert.equal(r.code, 'UI_CHANGED')
})

test('still-visible fresh obstruction requires fresh diagnoses, with three actions maximum', async () => {
  const seq: OperationResult[] = []
  for (let i = 0; i < 3; i++) seq.push(visual('present', 'not_satisfied', `fresh${i}`), visual('present'))
  const h = harness(seq), r = await h.wrapped.execute({}, ctx())
  assert.equal(meta(r).state, 'recovery_limit_reached'); assert.equal(h.calls.length, 1)
  assert.deepEqual(h.dismissals, ['fresh0', 'fresh1', 'fresh2']); assert.equal(h.seen.length, 6)
})

test('recovery exception preserves already-confirmed result and collected billing receipt', async () => {
  const h = harness([visual('present', 'not_satisfied', 'ref1')], 'boss_accept_resume')
  const f = h.initial.data.operation_recovery as Dict
  f.operation_class = 'external_effect'; (f.operation as Dict).result_kind = 'business_result'
  f.business_effect = { status: 'confirmed', completed_count: 1 }
  h.initial.effect = 'partial'; h.initial.data.confirmed_items = ['received']
  const r = await withLocalRecovery(h.operation, { env, inspect: h.inspect,
    dismiss: { name: 'boss_overlay_dismiss', async execute() { throw Error('do not leak underlying request or token') } } }).execute({}, ctx())
  assert.equal(r.effect, 'partial'); assert.deepEqual(r.data.confirmed_items, ['received'])
  assert.equal(meta(r).state, 'recovery_error'); assert.equal((meta(r).receipts as unknown[]).length, 1)
  assert.equal(h.calls.length, 1)
})

test('input-unknown dismiss is checked read-only before safe resume, not clicked twice', async () => {
  const h = harness([visual('present', 'not_satisfied', 'ref1'), visual('absent', 'satisfied')])
  const r = await withLocalRecovery(h.operation, { env, inspect: h.inspect, dismiss: { name: 'boss_overlay_dismiss', async execute() {
    return failResult('dismiss', 'EXECUTION_UNKNOWN', '输入回执未知', 'none')
  } } }).execute({}, ctx())
  assert.equal(meta(r).state, 'recovered'); assert.equal(h.seen[1]!.args.visual_context!.purpose, 'verify')
  assert.equal((meta(r).steps as Dict[])[0]!.input_status, 'unknown')
})

test('mismatched original tab binding does not inspect any page or pay for vision', async () => {
  const h = harness([]), r = await h.wrapped.execute({}, { ...ctx(), browserTargetRef: '22222222-2222-4222-8222-222222222222' })
  assert.equal(meta(r).state, 'not_eligible'); assert.equal(h.seen.length, 0)
})


const contactTools = ['boss_accept_resume', 'boss_send_current', 'boss_reject_current']
const lockedContact = 'a'.repeat(64), otherContact = 'b'.repeat(64)
function contactHarness(name: string) {
  const h = harness([visual('absent')], name)
  const f = h.initial.data.operation_recovery as Dict
  f.operation_class = 'external_effect'; (f.operation as Dict).result_kind = 'business_result'
  f.execution = { stage: 'prepared', input_status: 'not_started', attempts_used: 1, ordinary_retries_used: 0 }
  f.business_effect = { status: 'not_started', completed_count: 0 }
  return { ...h, facts: f }
}

for (const [label, recorded, expected, canResume] of [
  ['already locked context with absent preparation receipt', undefined, lockedContact, true],
  ['canonical facts without prior context binding', lockedContact, undefined, true],
  ['matching context and facts', lockedContact, lockedContact, true],
  ['no trusted contact binding', undefined, undefined, false],
  ['invalid facts cannot fall back to valid context', 'bad', lockedContact, false],
  ['invalid context alone', undefined, 'bad', false],
  ['conflicting valid bindings', lockedContact, otherContact, false],
  ['explicit null facts are invalid rather than absent', null, lockedContact, false],
  ['valid facts cannot override invalid caller binding', lockedContact, 'bad', false],
] as Array<[string, unknown, string | undefined, boolean]>) {
  test(`contact recovery: ${label}`, async () => {
    for (const name of contactTools) {
      const h = contactHarness(name)
      if (recorded !== undefined) h.facts.recovery_contact_ref = recorded
      const context = { ...ctx(), ...(expected !== undefined ? { expectedContactRef: expected } : {}) }
      const result = await h.wrapped.execute({ limit: 1 }, context)
      assert.equal(h.calls.length, canResume ? 2 : 1, name)
      assert.equal(meta(result).resumed === true, canResume, name)
      if (canResume) {
        assert.equal(h.calls[1]!.ctx.expectedContactRef, recorded ?? expected, name)
        assert.equal(h.calls[1]!.ctx.browserTargetRef, target, name)
        assert.deepEqual(h.calls[1]!.args, { limit: 1 })
      }
      assert.equal(context.expectedContactRef, expected)
    }
  })
}

for (const state of ['unknown', 'confirmed', 'execution_unknown'] as const) {
  test(`contact recovery: ${state} never becomes permission to replay even with an original caller lock`, async () => {
    for (const name of contactTools) {
      const h = contactHarness(name)
      h.facts.business_effect = { status: state === 'execution_unknown' ? 'not_started' : state, completed_count: state === 'confirmed' ? 1 : 0 }
      if (state === 'execution_unknown') h.initial.code = 'EXECUTION_UNKNOWN'
      if (state === 'confirmed') h.initial.effect = 'applied'
      const result = await h.wrapped.execute({}, { ...ctx(), expectedContactRef: lockedContact })
      assert.equal(h.calls.length, 1, name)
      assert.equal(meta(result).resumed, undefined)
      assert.equal(meta(result).state, state === 'confirmed' ? 'page_restored_business_preserved' : 'page_restored_business_unconfirmed')
    }
  })
}
