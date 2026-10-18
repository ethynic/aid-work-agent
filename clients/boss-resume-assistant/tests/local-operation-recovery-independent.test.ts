import assert from 'node:assert/strict'
import test from 'node:test'
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import { operationRecoveryFacts, type PageExecution } from '../src/main/operations/pageOperation.js'
import { okResult, failResult, type BossOperation, type OpContext, type OperationResult } from '../src/main/operations/types.js'
import type { VisualInspectArgs } from '../src/main/boss/visualRecovery.js'

const targetRef = 'b2651b85-7bd3-43e6-a4e6-bd54b1697a5d'
const contactRef = 'a'.repeat(64)
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const env = { AID_BOSS_SERVER_URL: 'http://127.0.0.1:49553', AID_BOSS_SERVER_TOKEN: 'fake-test-binding' }
type Facts = Record<string, any>
function source(name = 'boss_open_detail', kind: 'page_state' | 'readonly' | 'external_effect' = 'page_state',
  execution: Partial<PageExecution> = {}): OperationResult {
  const facts = operationRecoveryFacts({ name, operationClass: kind, goal: '完成原授权目标', expected: '稳定领域证据', target: '原候选人' },
    { attemptsUsed: 1, ordinaryRetriesUsed: 0, stage: 'input', inputStatus: 'not_started',
      verification: { status: 'inconclusive', evidence: ['元素被遮挡，尚未提交'] }, ...execution }) as Facts
  facts.browser_target_ref = targetRef
  facts.error = { code: 'UI_CHANGED', source: 'input', summary: '目标按钮被遮挡' }
  return failResult('original-call', 'UI_CHANGED', '目标按钮被遮挡', 'none',
    { browser_target_ref: targetRef, operation_recovery: facts })
}
function observation(args: VisualInspectArgs, { restored = false, action = true }: { restored?: boolean; action?: boolean } = {}): OperationResult {
  return okResult('visual-call', 'fake observation', 'none', {
    ...(action ? { recovery_ref: `ref-${args.request_id}` } : {}),
    visual: { request_id: args.request_id, http_status: 200, state: 'completed',
      receipt: { request_id: args.request_id, factor: 20 },
      diagnosis: { obstruction: restored ? 'absent' : 'present', goal_state: restored ? 'satisfied' : 'not_satisfied',
        proposed_action: action ? { kind: 'dismiss' } : null } },
  })
}
interface HarnessOptions {
  result?: OperationResult
  final?: OperationResult
  throwResume?: boolean
  inspect?: (args: VisualInspectArgs, ctx: OpContext, index: number) => OperationResult
  dismiss?: (ctx: OpContext) => OperationResult
}
function harness(options: HarnessOptions = {}) {
  const result = options.result ?? source()
  const args = { name: '原候选人', original: ['keep-order'] }
  const business: { args: unknown; ctx: OpContext }[] = []
  const inspections: { args: VisualInspectArgs; ctx: OpContext }[] = []
  const inputs: { args: unknown; ctx: OpContext }[] = []
  const raw: BossOperation<typeof args> = { name: (result.data.operation_recovery as Facts)?.operation.name ?? 'boss_open_detail',
    async execute(value, context) { business.push({ args: structuredClone(value), ctx: { ...context } });
      if (business.length > 1 && options.throwResume) throw new Error('fake exception after possible business commit')
      return business.length === 1 ? result : options.final ?? okResult('resumed-call', '领域目标已达成', 'none', { stable: true }) } }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', async execute(value, context) {
    const index = inspections.length; inspections.push({ args: structuredClone(value), ctx: { ...context } })
    return options.inspect?.(value, context, index) ?? observation(value, { restored: value.visual_context?.purpose === 'verify',
      action: value.visual_context?.purpose !== 'verify' })
  } }
  const dismiss: BossOperation<{ recovery_ref?: string; text?: string }> = { name: 'boss_overlay_dismiss', async execute(value, context) {
    inputs.push({ args: structuredClone(value), ctx: { ...context } })
    return options.dismiss?.(context) ?? okResult('dismiss-call', '输入已返回', 'none')
  } }
  let ids = 0
  const operation = withLocalRecovery(raw, { inspect, dismiss, env, requestId: () => `4bf8cdc7-9cbe-4850-8a36-${String(++ids).padStart(12, '0')}` })
  return { operation, args, business, inspections, inputs, result }
}

test('independent: ordinary success is local-owned without paying for any visual observation', async () => {
  const h = harness({ result: okResult('done', '完成', 'none', { stable: true }) })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, true)
  assert.deepEqual([h.business.length, h.inspections.length, h.inputs.length], [1, 0, 0])
  assert.equal((value.data.visual_recovery as Facts).owner, 'cli')
  assert.equal((value.data.visual_recovery as Facts).schema_version, 1)
})

test('independent: direct input error diagnoses before repeating original work and binds the original tab', async () => {
  const h = harness()
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, true)
  assert.deepEqual([h.business.length, h.inspections.length, h.inputs.length], [2, 2, 1])
  assert.deepEqual(h.business[1]!.args, h.args)
  assert.equal(h.business[1]!.ctx.browserTargetRef, targetRef)
  for (const call of [...h.inspections, ...h.inputs]) assert.equal(call.ctx.browserTargetRef, targetRef)
  const context = h.inspections[0]!.args.visual_context as Facts
  assert.deepEqual(context.execution, (h.result.data.operation_recovery as Facts).execution)
  assert.equal(context.browser_target_ref, undefined, 'Local browser references do not become model context')
  assert.deepEqual((h.inspections[1]!.args.visual_context as Facts).available_actions, [])
  assert.equal((value.data.operation_recovery as Facts).schema_version, 2, 'Older servers cannot admit the local-owned receipt as legacy recovery facts')
  assert.equal((h.result.data.operation_recovery as Facts).schema_version, 1, 'Public projection must not mutate raw domain facts')
})

test('independent: exhausted ordinary domain retry is not duplicated by the recovery wrapper', async () => {
  const h = harness({ result: source('boss_open_detail', 'page_state', { attemptsUsed: 2, ordinaryRetriesUsed: 1,
    stage: 'postcheck', inputStatus: 'attempted', verification: { status: 'unmet', evidence: ['两次均缺详情锚点'] } }) })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, true)
  assert.equal(h.business.length, 2, 'The second raw call follows successful repair, not another ordinary retry')
  const context = h.inspections[0]!.args.visual_context as Facts
  assert.equal(context.execution.attempts_used, 2)
  assert.equal(context.execution.ordinary_retries_used, 1)
})

test('independent: failure after successful repair returns once without recursive visual recovery', async () => {
  const final = source()
  final.message = '恢复后仍无法打开原目标'
  const h = harness({ final })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, false)
  assert.equal(value.message, final.message)
  assert.deepEqual([h.business.length, h.inspections.length, h.inputs.length], [2, 2, 1])
  assert.equal((value.data.visual_recovery as Facts).owner, 'cli')
})

for (const status of ['unknown', 'confirmed'] as const) {
  test(`independent: external ${status} preserves business facts and never resends after repair`, async () => {
    const result = source('boss_send_current', 'external_effect', { inputStatus: 'attempted' })
    const facts = result.data.operation_recovery as Facts
    facts.business_effect = { status, completed_count: status === 'confirmed' ? 1 : 0 }
    facts.recovery_contact_ref = contactRef
    result.code = 'EXECUTION_UNKNOWN'
    result.effect = status === 'unknown' ? 'unknown' : 'partial'
    result.data.completed_items = ['already-completed-item']
    const h = harness({ result })
    const value = await h.operation.execute(h.args, ctx())
    assert.equal(value.effect, result.effect)
    assert.equal(value.success, false)
    assert.deepEqual(value.data.completed_items, ['already-completed-item'])
    assert.deepEqual((value.data.operation_recovery as Facts).business_effect, facts.business_effect)
    assert.equal(h.business.length, 1, 'Page repair cannot authorize another external message')
    assert.equal(h.inputs.length, 1)
  })
}

test('independent: proved not-started send resumes against the original conversation only', async () => {
  const result = source('boss_send_current', 'external_effect')
  const facts = result.data.operation_recovery as Facts
  facts.business_effect = { status: 'not_started', completed_count: 0 }
  facts.recovery_contact_ref = contactRef
  const h = harness({ result })
  assert.equal((await h.operation.execute(h.args, ctx())).success, true)
  assert.equal(h.business.length, 2)
  assert.equal(h.business[1]!.ctx.expectedContactRef, contactRef)
})

test('independent: a thrown resumed send cannot resurrect the initial not-started proof', async () => {
  const result = source('boss_send_current', 'external_effect')
  const facts = result.data.operation_recovery as Facts
  facts.business_effect = { status: 'not_started', completed_count: 0 }
  facts.recovery_contact_ref = contactRef
  const h = harness({ result, throwResume: true })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.code, 'EXECUTION_UNKNOWN')
  assert.equal(value.effect, 'unknown')
  assert.equal(h.business.length, 2)
  assert.deepEqual((value.data.operation_recovery as Facts).business_effect, { status: 'unknown', completed_count: 0 })
  assert.equal((value.data.visual_recovery as Facts).resumed, true)
  assert.equal((value.data.visual_recovery as Facts).original_execution.input_status, 'not_started')
  assert.equal((value.data.visual_recovery as Facts).resume_execution.input_status, 'unknown')
  assert.equal(h.inspections.length, 2, 'The resumed failure cannot enter a second recovery loop')
})

test('independent: not-started current-conversation actions without a contact proof cannot switch to the new active chat', async () => {
  const result = source('boss_send_current', 'external_effect')
  ;(result.data.operation_recovery as Facts).business_effect = { status: 'not_started', completed_count: 0 }
  const h = harness({ result })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, false)
  assert.equal(h.business.length, 1)
  assert.equal(h.inputs.length, 1)
  assert.equal((value.data.visual_recovery as Facts).page_restored, true)
})

test('independent: read payload survives cleanup without reading a second possibly different candidate', async () => {
  const result = source('boss_read_chat', 'readonly')
  const facts = result.data.operation_recovery as Facts
  facts.operation.result_kind = 'business_result'
  result.success = true; result.code = 'OK'
  result.data.messages = [{ text: 'original read content' }]
  result.data.cleanup_error = { code: 'UI_CHANGED', message: '预览未关闭' }
  const h = harness({ result })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, true)
  assert.deepEqual(value.data.messages, result.data.messages)
  assert.equal(h.business.length, 1)
  assert.equal(h.inputs.length, 1)
})

test('independent: invalid model action does not erase assessment and cannot cause an input', async () => {
  const h = harness({ inspect(args) {
    const value = observation(args, { action: false })
    ;(value.data.visual as Facts).action_error = 'INVALID_MODEL_ACTION'
    return value
  } })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, false)
  assert.equal(h.business.length, 1)
  assert.equal(h.inputs.length, 0)
  assert.match(JSON.stringify(value.data.visual_recovery), /present/, 'Correct page assessment remains available despite invalid advice')
})

test('independent: backend pending response retrieves the same paid request before any further action', async () => {
  const h = harness({ inspect(args, _ctx, index) {
    if (index === 0) return okResult('pending-call', 'pending', 'none', { visual: {
      request_id: args.request_id, http_status: 202, state: 'pending' } })
    if (args.retrieve_only) return observation(args)
    return observation(args, { restored: true, action: false })
  } })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, true)
  assert.equal(h.inspections[1]!.args.request_id, h.inspections[0]!.args.request_id)
  assert.equal(h.inspections[1]!.args.retrieve_only, true)
  assert.equal(h.inspections[1]!.args.visual_context, undefined)
  assert.equal(h.inputs.length, 1)
  assert.equal(h.business.length, 2)
})

test('independent: cancellation and invalid arguments never acquire automatic paid recovery', async () => {
  for (const code of ['CANCELLED', 'INVALID_ARGUMENT', 'NOT_LOGGED_IN', 'PAYWALL'] as const) {
    const result = source(); result.code = code
    const h = harness({ result })
    const value = await h.operation.execute(h.args, ctx())
    assert.equal(value.code, code)
    assert.equal(h.inspections.length, 0)
    assert.equal(h.business.length, 1)
    assert.equal((value.data.visual_recovery as Facts).owner, 'cli', 'Server cannot append a second recovery even to failed local outcomes')
  }
})

test('independent: unavailable post-action page capture uses window readback without clicking the close action twice', async () => {
  const h = harness({ inspect(args, _ctx, index) {
    if (index === 1) return failResult('capture-failed', 'CHROME_UNAVAILABLE', '页面采集被阻塞', 'none')
    return observation(args, { restored: index > 1, action: index === 0 })
  }, dismiss() { return failResult('close-unknown', 'EXECUTION_UNKNOWN', '点击响应未知', 'unknown') } })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, true)
  assert.deepEqual(h.inspections.map(v => v.args.observation_mode), ['page', 'page', 'window'])
  assert.equal(h.inputs.length, 1, 'Unknown input is never replayed; only fresh post-observation is allowed')
  assert.equal(h.business.length, 2)
  assert.equal((h.inspections[2]!.args.visual_context as Facts).execution.input_status, 'unknown')
  assert.deepEqual((h.inspections[2]!.args.visual_context as Facts).available_actions, [])
})

test('independent: a present obstruction after closing requires a fresh reference instead of replaying old coordinates', async () => {
  const h = harness({ inspect(args, _ctx, index) {
    return observation(args, { restored: index === 3, action: args.visual_context?.purpose !== 'verify' })
  } })
  const value = await h.operation.execute(h.args, ctx())
  assert.equal(value.success, true)
  assert.equal(h.inputs.length, 2)
  assert.notEqual((h.inputs[0]!.args as Facts).recovery_ref, (h.inputs[1]!.args as Facts).recovery_ref)
  assert.equal(h.business.length, 2)
  assert.equal(h.inspections.length, 4)
})

test('independent: cancellation during diagnosis preserves accumulated receipt and original result without issuing input', async () => {
  const controller = new AbortController()
  const result = source('boss_send_current', 'external_effect', { inputStatus: 'attempted' })
  result.code = 'EXECUTION_UNKNOWN'; result.effect = 'unknown'
  ;(result.data.operation_recovery as Facts).business_effect = { status: 'unknown', completed_count: 0 }
  const h = harness({ result, inspect(args) { controller.abort(); return observation(args) } })
  const value = await h.operation.execute(h.args, { ...ctx(), signal: controller.signal })
  assert.equal(value.code, 'EXECUTION_UNKNOWN')
  assert.equal(value.effect, 'unknown')
  assert.equal((value.data.visual_recovery as Facts).state, 'cancelled')
  assert.equal((value.data.visual_recovery as Facts).receipts.length, 1)
  assert.equal(h.inputs.length, 0)
  assert.equal(h.business.length, 1)
})
