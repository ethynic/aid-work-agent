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

test('readonly零输入且网页已满足：不再请求窗口诊断来否决正常页面续做', async () => {
  const h = harness([visual('absent', 'satisfied')], 'boss_filter_options')
  const f = h.initial.data.operation_recovery as Dict
  f.operation_class = 'readonly'
  f.execution = { stage: 'input', input_status: 'not_started', attempts_used: 1, ordinary_retries_used: 0 }
  const result = await h.wrapped.execute({}, ctx())
  assert.equal(result.success, true)
  assert.equal(meta(result).state, 'recovered')
  assert.equal(h.calls.length, 2)
  assert.deepEqual(h.seen.map(call => call.args.observation_mode), ['page'])
})

test('filter draft resumes the original setter on a healthy edit page without treating input stage as window failure', async () => {
  for (const name of ['boss_filter', 'boss_clear_filter']) {
    const h = harness([visual('absent', 'satisfied')], name)
    const facts = h.initial.data.operation_recovery as Dict
    facts.execution = { stage: 'input', input_status: 'attempted', attempts_used: 1, ordinary_retries_used: 0 }
    facts.verification = { status: 'inconclusive', evidence: ['filter_phase=draft_edited'] }
    h.initial.effect = 'partial'
    const result = await h.wrapped.execute({ age: '不限' }, ctx())
    assert.equal(result.success, true); assert.equal(h.calls.length, 2)
    assert.deepEqual(h.seen.map(call => call.args.observation_mode), ['page'])
    assert.equal(h.calls[1]!.ctx.previousPageExecution!.inputStatus, 'attempted')
    assert.equal(h.calls[0]!.ctx.windowCaptureBudget, h.seen[0]!.ctx.windowCaptureBudget)
    assert.equal(h.seen[0]!.ctx.windowCaptureBudget, h.calls[1]!.ctx.windowCaptureBudget)
  }
})

test('a normal filter close suggestion returns to the setter while other obstructions still use the existing guarded dismiss', async () => {
  for (const label of ['关闭筛选面板', '关闭筛选面板中的推广广告']) {
    const observed = visual('present', 'not_satisfied', 'ref-filter')
    const diagnosis = ((observed.data.visual as Dict).diagnosis as Dict)
    diagnosis.proposed_action = { kind: 'dismiss', target_label: label }
    const h = harness([observed, visual('absent', 'satisfied')])
    const facts = h.initial.data.operation_recovery as Dict
    facts.verification = { status: 'inconclusive', evidence: ['filter_phase=draft_edited'] }
    const result = await h.wrapped.execute({}, ctx())
    assert.equal(result.success, true)
    assert.deepEqual(h.dismissals, label === '关闭筛选面板' ? [] : ['ref-filter'])
  }
})

test('window fallback also preserves the normal draft filter panel instead of dismissing it', async () => {
  const observed = visual('present','not_satisfied','window-filter')
  ;((observed.data.visual as Dict).diagnosis as Dict).proposed_action = {kind:'dismiss',target_label:'关闭筛选面板'}
  const h = harness([failResult('page','UI_CHANGED','page capture unavailable','none'),observed])
  ;(h.initial.data.operation_recovery as Dict).verification = {status:'inconclusive',evidence:['filter_phase=draft_edited']}
  const result = await h.wrapped.execute({},ctx())
  assert.equal(result.success,true); assert.equal(h.calls.length,2); assert.deepEqual(h.dismissals,[])
  assert.deepEqual(h.seen.map(c => c.args.observation_mode),['page','window'])
})

test('window capture fixed failure reasons and numeric geometry survive recovery without copying private diagnostics', async () => {
  const failure = failResult('capture', 'INTERNAL_ERROR', 'private diagnostic', 'none', {
    visual_failure: { stage: 'window_capture', original_code: 'INTERNAL_ERROR', reason: 'window_outside_screen', secret: 'no-leak',
      geometry: { window: [-100, 0, 100, 100], screen: [0, 0, 1920, 1080], work: [0, 0, 1920, 1040], dpi: 144, title: 'no-leak' } } })
  const h = harness([failResult('page', 'UI_CHANGED', 'page blocked', 'none'), failure])
  const result = await h.wrapped.execute({}, ctx())
  const observation = (meta(result).observations as Dict[])[1]!
  assert.equal(observation.reason, 'window_outside_screen')
  assert.deepEqual(observation.geometry, { window: [-100, 0, 100, 100], screen: [0, 0, 1920, 1080], work: [0, 0, 1920, 1040], dpi: 144 })
  assert.equal(JSON.stringify(meta(result)).includes('no-leak'), false)
})

test('all 22 business entries share local ownership and the two primitives cannot recursively recover', async () => {
  assert.equal(Object.keys(LOCAL_RECOVERY_POLICIES).length, 22)
  assert.equal(Object.keys(OPERATIONS).length, 25)
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

for (const [status, error] of [
  [401, '缺少设备 token'],
  [401, '设备 token 无效或设备已撤销'],
  [403, '设备租户不存在或已停用'],
  [403, '设备用户不存在、已停用或不属于当前租户'],
] as const) test(`backend ${status} rejection preserves its known reason without invoking page repair: ${error}`, async () => {
  const h = harness([okResult('denied', '视觉回执', 'none', { visual: {
    http_status: status, detail: { error, debug: 'private-debug-must-not-leak' }, headers: { Authorization: 'private-header-must-not-leak' },
  } })])
  const r = await h.wrapped.execute({}, ctx())
  assert.equal(meta(r).state, 'diagnosis_unavailable')
  assert.equal((meta(r).observations as Dict[])[0]!.api_error, error)
  assert.equal(r.code, h.initial.code); assert.equal(r.message, h.initial.message)
  assert.equal(h.calls.length, 1); assert.equal(h.seen.length, 1); assert.equal(h.dismissals.length, 0)
  assert.ok(!JSON.stringify(r).includes('private-'), 'unrelated backend diagnostic fields must stay private')
})

for (const detail of [
  { error: 'Bearer private-token-must-not-leak' }, { error: { token: 'private-token-must-not-leak' } },
  'private-detail-must-not-leak', { error: '缺少设备 token' }, undefined,
]) test(`unrecognised 403 body cannot leak into recovery observations: ${JSON.stringify(detail)}`, async () => {
  const h = harness([okResult('denied', '视觉回执', 'none', { visual: { http_status: 403, detail } })])
  const r = await h.wrapped.execute({}, ctx())
  assert.equal((meta(r).observations as Dict[])[0]!.api_error, '视觉恢复接口拒绝访问（HTTP 403）')
  assert.ok(!JSON.stringify(r).includes('private-'))
  assert.equal(h.calls.length, 1); assert.equal(h.dismissals.length, 0)
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


function windowFilterHarness(name = 'boss_filter', change: {
  initial?: (r: OperationResult) => void; observed?: (r: OperationResult) => void
  before?: number; after?: number; noContinuation?: boolean; failResume?: boolean; pending?: boolean; cancel?: boolean
} = {}) {
  const initial = original(name), facts = initial.data.operation_recovery as Dict
  facts.execution = { stage: 'input', input_status: 'not_started', attempts_used: 2, ordinary_retries_used: 1 }
  facts.verification = { status: 'inconclusive', evidence: ['filter_phase=not_started'] }
  change.initial?.(initial)
  const budget = { repositionsUsed: change.before ?? 0 }, abort = new AbortController()
  const modes: unknown[] = [], ids: string[] = [], contexts: OpContext[] = []
  let calls = 0, resumes = 0, dismissals = 0, pending = change.pending
  const operation: BossOperation<Dict> = { name, async execute(args, c) {
    calls++
    if (!change.noContinuation) c.recoveryContinuation!.resume = async fresh => {
      resumes++; contexts.push(fresh)
      assert.deepEqual(args, { experience: '3-5年', educations: ['本科'] })
      return change.failResume ? failResult('resume', 'UI_CHANGED', 'fresh定位仍失败', 'none') : okResult('resume', '领域后验完成', 'applied')
    }
    return initial
  } }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', async execute(args, c) {
    assert.equal(c.windowCaptureBudget, budget); assert.equal(c.browserTargetRef, target)
    ids.push(args.request_id!); const mode = args.retrieve_only ? 'window' : args.observation_mode
    modes.push(args.retrieve_only ? 'retrieve' : mode)
    const r = visual('absent', 'inconclusive'); r.data.browser_target_ref = target
    ;(r.data.visual as Dict).request_id = args.request_id
    if (mode === 'window') {
      budget.repositionsUsed = change.after ?? 1
      r.data.observation = { mode: 'window', space: 'window_image_px', width: 1412, height: 1354 }
      change.observed?.(r)
      if (pending && !args.retrieve_only) { pending = false; Object.assign(r.data.visual as Dict, { state: 'pending', http_status: 202, diagnosis: null }) }
      if (change.cancel) abort.abort()
    }
    return r
  } }
  const dismiss: BossOperation<{ recovery_ref?: string }> = { name: 'boss_overlay_dismiss', async execute() {
    dismissals++; return failResult('dismiss', 'UI_CHANGED', 'unexpected dismiss', 'none')
  } }
  return { budget, modes, ids, contexts, get counts() { return { calls, resumes, dismissals } },
    run: () => withLocalRecovery(operation, { inspect, dismiss, env }).execute({ experience: '3-5年', educations: ['本科'] }, { signal: abort.signal, progress: () => {}, windowCaptureBudget: budget }) }
}

for (const name of ['boss_filter', 'boss_clear_filter']) test(`${name}: successful reposition resumes the unstarted filter inside one invocation despite inconclusive business goal`, async () => {
  const h = windowFilterHarness(name), r = await h.run()
  assert.equal(r.success, true); assert.equal(meta(r).state, 'recovered')
  assert.deepEqual(h.counts, { calls: 1, resumes: 1, dismissals: 0 }); assert.deepEqual(h.modes, ['page', 'window'])
  assert.equal(h.contexts[0]!.windowCaptureBudget, h.budget); assert.equal(h.budget.repositionsUsed, 1)
  assert.equal(h.contexts[0]!.browserTargetRef, target)
  assert.equal(h.contexts[0]!.previousPageExecution!.attemptsUsed, 2)
  assert.equal(h.contexts[0]!.previousPageExecution!.ordinaryRetriesUsed, 1)
  assert.equal((meta(r).steps as Dict[])[0]!.branch, 'window_reposition_resume')
  const observation = (meta(r).observations as Dict[])[1]!
  assert.equal(observation.repositions_before, 0); assert.equal(observation.repositions_after, 1); assert.equal(observation.capture_completed, true)
})

test('repositioned window receipt can complete by same-request retrieval without another capture', async () => {
  const h = windowFilterHarness('boss_filter', { pending: true }), r = await h.run()
  assert.equal(r.success, true); assert.equal(h.counts.resumes, 1)
  assert.deepEqual(h.modes, ['page', 'window', 'retrieve']); assert.equal(h.ids[1], h.ids[2]); assert.notEqual(h.ids[0], h.ids[1])
})

test('resumed failure returns its actual failure without recursively recovering or dispatching again', async () => {
  const h = windowFilterHarness('boss_filter', { failResume: true }), r = await h.run()
  assert.equal(r.success, false); assert.equal(r.code, 'UI_CHANGED'); assert.equal(meta(r).state, 'original_goal_unconfirmed')
  assert.deepEqual(h.counts, { calls: 1, resumes: 1, dismissals: 0 }); assert.deepEqual(h.modes, ['page', 'window'])
})

type WindowChange = Parameters<typeof windowFilterHarness>[1]
const denied: Array<[string, WindowChange]> = [
  ['budget consumed but capture failed', { observed: r => { r.success = false; r.code = 'UI_CHANGED'; delete r.data.observation } }],
  ['no reposition', { after: 0 }], ['previously spent budget', { before: 1 }], ['no continuation', { noContinuation: true }],
  ['missing image width', { observed: r => { delete (r.data.observation as Dict).width } }],
  ['page image', { observed: r => { (r.data.observation as Dict).space = 'page_image_px' } }],
  ['other binding', { observed: r => { r.data.browser_target_ref = '22222222-2222-4222-8222-222222222222' } }],
  ['other request', { observed: r => { (r.data.visual as Dict).request_id = 'another-request' } }],
  ['diagnosis error', { observed: r => { (r.data.visual as Dict).diagnosis_error = 'INVALID_MODEL_DIAGNOSIS' } }],
  ['action error', { observed: r => { (r.data.visual as Dict).action_error = 'INVALID_MODEL_ACTION' } }],
  ['unknown receipt', { observed: r => { (r.data.visual as Dict).state = 'unknown' } }], ['cancelled', { cancel: true }],
  ...['STALE_OBSERVATION', 'ALREADY_CONSUMED'].map(error => [error, { observed: (r: OperationResult) => { r.data.recovery_action_error = error } }] as [string, WindowChange]),
  ...['attempted', 'unknown'].map(status => [status + ' input', { initial: (r: OperationResult) => { ((r.data.operation_recovery as Dict).execution as Dict).input_status = status } }] as [string, WindowChange]),
  ...['draft_edited', 'submitted'].map(phase => [phase + ' filter', { initial: (r: OperationResult) => { ((r.data.operation_recovery as Dict).verification as Dict).evidence = ['filter_phase=' + phase] } }] as [string, WindowChange]),
  ['conflicting phases', { initial: r => { ((r.data.operation_recovery as Dict).verification as Dict).evidence = ['filter_phase=not_started', 'filter_phase=submitted'] } }],
  ['unknown original', { initial: r => { r.code = 'EXECUTION_UNKNOWN' } }], ['partial original', { initial: r => { r.effect = 'partial' } }],
]
for (const [reason, change] of denied) test(`reposition must not authorize filter replay: ${reason}`, async () => {
  const h = windowFilterHarness('boss_filter', change), r = await h.run()
  assert.equal(r.success, false); assert.equal(h.counts.resumes, 0); assert.equal(h.counts.calls, 1)
})

test('reposition proof does not newly authorize other page-state tools', async () => {
  const h = windowFilterHarness('boss_goto'), r = await h.run()
  assert.equal(r.success, false); assert.equal(h.counts.resumes, 0)
})
