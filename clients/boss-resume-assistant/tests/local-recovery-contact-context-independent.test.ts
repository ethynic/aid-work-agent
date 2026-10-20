/** A preparation error may precede domain binding; original caller identity must survive repair. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import { BusinessOperationState, chatContactRef, withBusinessRecovery } from '../src/main/boss/BusinessOperationState.js'
import { ResumeConsentExecutor, attachmentPreviewState } from '../src/main/boss/ResumeConsentExecutor.js'
import { actionSnapshot, text } from './chatActionFixture.js'
import { buildChatSnapshot } from './chatReadFixture.js'
import type { VisualInspectArgs } from '../src/main/boss/visualRecovery.js'
import { CodedOperationError, failResult, okResult, type BossOperation, type OpContext, type OperationResult } from '../src/main/operations/types.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

type Dict = Record<string, unknown>
const target = '11111111-1111-4111-8111-111111111111', originalRef = 'a'.repeat(64), otherRef = 'b'.repeat(64)
const env = { AIDWORK_RUNTIME_SERVER: 'https://offline.invalid', AIDWORK_RUNTIME_DEVICE_TOKEN: 'test-only' }
const context = (ref?: unknown): OpContext => ({ signal: new AbortController().signal, progress: () => {},
  ...(ref === undefined ? {} : { expectedContactRef: ref as string }) })
function source(name: string, factsRef?: unknown): OperationResult {
  return failResult('original', 'UI_CHANGED', '准备阶段未找到处理条', 'none', { browser_target_ref: target,
    operation_recovery: { schema_version: 1, operation_class: 'external_effect', browser_target_ref: target,
      operation: { name, goal: '对原会话执行已授权操作', expected: '原会话业务稳定后态', result_kind: 'business_result' },
      execution: { stage: 'input', input_status: 'attempted', attempts_used: 1, ordinary_retries_used: 0 },
      verification: { status: 'inconclusive', evidence: ['准备点击后未出现处理条，未提交业务'] },
      error: { code: 'UI_CHANGED', source: 'input', summary: '准备点击未进入原会话' },
      business_effect: { status: 'not_started', completed_count: 0 },
      ...(factsRef === undefined ? {} : { recovery_contact_ref: factsRef }) } })
}
function observation(repaired: boolean): OperationResult {
  return okResult('visual', '离线观察', 'none', { ...(repaired ? {} : { recovery_ref: 'offline-dismiss' }),
    // Deliberately untrusted extras cannot replace either caller or domain identity.
    recovery_contact_ref: otherRef, expectedContactRef: otherRef,
    visual: { http_status: 200, state: 'completed', diagnosis: {
      obstruction: repaired ? 'absent' : 'present', goal_state: repaired ? 'satisfied' : 'not_satisfied',
      proposed_action: repaired ? null : { kind: 'dismiss', recovery_contact_ref: otherRef } } } })
}
async function run(name: string, callerRef?: unknown, factsRef?: unknown,
  mutate?: (initial: OperationResult) => void) {
  const initial = source(name, factsRef), calls: OpContext[] = [], inspected: VisualInspectArgs[] = []
  mutate?.(initial)
  let repaired = false
  const raw: BossOperation<Dict> = { name, execute: async (_args, ctx) => {
    calls.push(ctx); return calls.length === 1 ? initial : okResult('resumed', '原业务完成', 'applied')
  } }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', execute: async args => {
    inspected.push(args); return observation(repaired)
  } }
  const dismiss: BossOperation<{ recovery_ref?: string }> = { name: 'boss_overlay_dismiss', execute: async () => {
    repaired = true; return okResult('dismiss', '关闭输入返回', 'none')
  } }
  const result = await withLocalRecovery(raw, { env, inspect, dismiss }).execute({}, context(callerRef))
  return { result, initial, calls, inspected }
}

for (const name of ['boss_accept_resume', 'boss_send_current', 'boss_reject_current']) {
  for (const [label, caller, facts] of [
    ['caller-only', originalRef, undefined], ['facts-only', undefined, originalRef], ['both agree', originalRef, originalRef],
  ] as Array<[string, unknown, unknown]>) {
    test(`independent: ${name} ${label} resumes exactly once with original proven identity`, async () => {
      const h = await run(name, caller, facts)
      assert.equal(h.result.success, true); assert.equal((h.result.data.visual_recovery as Dict).state, 'recovered')
      assert.equal(h.calls.length, 2); assert.equal(h.calls[1]!.expectedContactRef, originalRef)
      assert.equal(h.calls[1]!.browserTargetRef, target)
      assert.equal((h.initial.data.operation_recovery as Dict).recovery_contact_ref, facts)
      for (const request of h.inspected) assert.ok(!JSON.stringify(request.visual_context).includes(originalRef), 'Opaque contact binding stays local; model does not receive it')
    })
  }
  for (const [label, caller, facts] of [
    ['conflicting identities', originalRef, otherRef], ['no identity', undefined, undefined],
    ['invalid caller', 'z'.repeat(64), originalRef], ['invalid facts', originalRef, 'z'.repeat(64)],
    ['null caller', null, originalRef], ['null facts', originalRef, null],
    ['number caller', 42, originalRef], ['number facts', originalRef, 42],
    ['short caller', 'abc', undefined], ['uppercase caller', 'A'.repeat(64), undefined],
  ] as Array<[string, unknown, unknown]>) {
    test(`independent: ${name} ${label} never reissues business after page repair`, async () => {
      const h = await run(name, caller, facts)
      assert.equal(h.calls.length, 1); assert.equal(h.result.success, false)
      assert.equal((h.result.data.visual_recovery as Dict).resumed, undefined)
    })
  }
}

for (const [label, mutate] of [
  ['unknown effect', (r: OperationResult) => { r.effect = 'unknown'; (r.data.operation_recovery as Dict).business_effect = { status: 'unknown', completed_count: 0 } }],
  ['confirmed business', (r: OperationResult) => { r.effect = 'applied'; (r.data.operation_recovery as Dict).business_effect = { status: 'confirmed', completed_count: 1 } }],
  ['EXECUTION_UNKNOWN despite not-started claim', (r: OperationResult) => { r.code = 'EXECUTION_UNKNOWN' }],
  ['partial effect despite not-started claim', (r: OperationResult) => { r.effect = 'partial' }],
] as Array<[string, (r: OperationResult) => void]>) {
  test(`independent: valid caller ref never weakens the ${label} no-replay gate`, async () => {
    const h = await run('boss_accept_resume', originalRef, undefined, mutate)
    assert.equal(h.calls.length, 1)
    assert.equal((h.result.data.visual_recovery as Dict).resumed, undefined)
  })
}

const marker = '对方想发送附件简历给您，您是否同意'
const request = text(marker, 240, 500, 290, 25), prompt = text(marker, 650, 1000, 300, 25), agree = text('同意', 1050, 1000, 60, 25)
const preview = text('点击预览附件简历', 700, 800, 180, 25)
function attachmentSnapshot(): DomSnapshot {
  const snap = actionSnapshot({ contact: '霍子腾', extra: [preview,
    { tag: 'IFRAME', cls: 'attachment-iframe', bounds: [248, 50, 765, 894] }] })
  const child = buildChatSnapshot([{ tag: 'CANVAS', bounds: [0, 0, 749, 1059] }])
  const map = child.strings.map(s => { const i = snap.strings.indexOf(s); if (i >= 0) return i; snap.strings.push(s); return snap.strings.length - 1 })
  const nodes = child.documents[0]!.nodes
  nodes.nodeName = (nodes.nodeName as number[]).map(i => i < 0 ? i : map[i]!)
  nodes.nodeValue = (nodes.nodeValue as number[]).map(i => i < 0 ? i : map[i]!)
  nodes.attributes = (nodes.attributes as number[][]).map(a => a.map(i => map[i]!))
  const frame = (snap.documents[0]!.nodes.nodeName as number[]).findIndex(i => snap.strings[i] === 'IFRAME')
  snap.documents[0]!.nodes.contentDocumentIndex = { index: [frame], value: [1] }
  snap.documents.push(child.documents[0]!)
  return snap
}

async function realConsentChain(stillWrongContact = false) {
  let repaired = false, phase: 'list' | 'request' | 'accepted' | 'preview' = 'list', current = '陈建羽'
  let rawCalls = 0, agrees = 0, previews = 0, escapes = 0, repairClicks = 0
  const contexts: OpContext[] = []
  const expected = chatContactRef(actionSnapshot({ contact: '霍子腾' }))!
  const intent = { name: 'boss_accept_resume', operationClass: 'external_effect' as const,
    goal: '仅接收原授权会话的简历', expected: '原会话处理条消失、新附件出现并预览关闭' }
  const raw: BossOperation<Dict> = { name: intent.name, execute: async (_args, ctx) => {
    rawCalls++; contexts.push(ctx)
    const state = new BusinessOperationState()
    const executor = new ResumeConsentExecutor({ state,
      snapshot: async () => phase === 'preview' ? attachmentSnapshot() : actionSnapshot({ contact: current,
        extra: !repaired || phase === 'list' ? [request] : phase === 'request' ? [request, prompt, agree] : [preview] }),
      click: async point => {
        if (point.x < 548) { if (repaired) { current = stillWrongContact ? '陈建羽' : '霍子腾'; phase = 'request' } }
        else if (point.x > 1000) { agrees++; phase = 'accepted' }
        else { previews++; phase = 'preview' }
      }, pressEscape: async () => { escapes++; phase = 'accepted' }, sleep: async () => {},
    })
    try {
      const result = await withBusinessRecovery(state, intent, async () => ({ data: await executor.acceptAll({ limit: 1, preview: true, expectedContactRef: ctx.expectedContactRef }) }))
      return okResult(`raw-${rawCalls}`, '接收预览完成', 'applied', { ...result.data, browser_target_ref: target })
    } catch (error) {
      assert.ok(error instanceof CodedOperationError)
      return failResult(`raw-${rawCalls}`, error.code, error.message, state.businessEffect.status === 'unknown' ? 'unknown' : 'none', { ...error.data, browser_target_ref: target })
    }
  } }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', execute: async () => observation(repaired) }
  const dismiss: BossOperation<{ recovery_ref?: string }> = { name: 'boss_overlay_dismiss', execute: async () => {
    repairClicks++; repaired = true; return okResult('dismiss', '修复页面', 'none')
  } }
  const result = await withLocalRecovery(raw, { env, inspect, dismiss }).execute({}, context(expected))
  return { result, expected, contexts, rawCalls, agrees, previews, escapes, repairClicks, current, finalPreview: String(phase) === 'preview' ? attachmentPreviewState(attachmentSnapshot()) : 'closed' }
}

test('independent: real consent preparation fails before bind, then original caller ref survives repair through one acceptance and preview close', async () => {
  const h = await realConsentChain()
  assert.equal(h.result.success, true); assert.equal((h.result.data.visual_recovery as Dict).state, 'recovered')
  assert.equal(h.rawCalls, 2); assert.equal(h.repairClicks, 1); assert.equal(h.agrees, 1); assert.equal(h.previews, 1); assert.equal(h.escapes, 1)
  assert.equal(h.current, '霍子腾'); assert.equal(h.finalPreview, 'closed')
  assert.equal(h.contexts[1]!.expectedContactRef, h.expected)
  assert.deepEqual(h.result.data.confirmed_items, [{ contact: '霍子腾', accepted: true }])
  assert.equal(h.result.data.accepted, 1); assert.equal(h.result.data.previewed, 1)
})

test('independent: repair leaving another current contact cannot replace original binding or reach final agree', async () => {
  const h = await realConsentChain(true)
  assert.equal(h.result.success, false); assert.equal((h.result.data.visual_recovery as Dict).state, 'original_goal_unconfirmed')
  assert.equal(h.rawCalls, 2); assert.equal(h.repairClicks, 1); assert.equal(h.agrees, 0); assert.equal(h.previews, 0); assert.equal(h.escapes, 0)
  assert.equal(h.current, '陈建羽'); assert.equal(h.contexts[1]!.expectedContactRef, h.expected)
  assert.deepEqual((h.result.data.operation_recovery as Dict).business_effect, { status: 'not_started', completed_count: 0 })
})
