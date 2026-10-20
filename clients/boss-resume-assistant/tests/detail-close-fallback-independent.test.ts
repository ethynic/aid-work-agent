/** Closing a detail must target that resume's control, never another visible ×. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { DetailGreetExecutor } from '../src/main/boss/DetailGreetExecutor.js'
import { FILTER_SNAPSHOT_OPTIONS } from '../src/main/boss/FilterSetter.js'
import type { ClickPoint, DomDocument, DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { runVerifiedPageOperation } from '../src/main/operations/pageOperation.js'
import { CodedOperationError } from '../src/main/operations/types.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { createBossCloseDetailOperation } from '../src/main/operations/bossCloseDetail.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { BusinessOperationState, withBusinessRecovery } from '../src/main/boss/BusinessOperationState.js'
import { listSnap } from './detailGreetFixture.js'

interface Node { tag: string; parent: number; bounds: number[]; attrs?: Record<string, string>; style?: Record<string, string>; child?: number; text?: string }
function snapshot(options: { missing?: boolean; duplicate?: boolean; hidden?: boolean; rootScroll?: number; shifted?: number; noParents?: boolean; glyph?: boolean; nestedModal?: boolean; ownerWidth?: number; greeted?: boolean } = {}): DomSnapshot {
  const strings: string[] = []
  const str = (value: string) => { const existing = strings.indexOf(value); if (existing >= 0) return existing; strings.push(value); return strings.length - 1 }
  const doc = (nodes: Node[]): DomDocument => ({ nodes: {
    nodeName: nodes.map(n => str(n.tag)), nodeValue: nodes.map(n => n.text ? str(n.text) : -1), parentIndex: nodes.map(n => n.parent),
    attributes: nodes.map(n => Object.entries(n.attrs ?? {}).flatMap(([k, v]) => [str(k), str(v)])),
    contentDocumentIndex: { index: nodes.flatMap((n, i) => n.child === undefined ? [] : [i]), value: nodes.flatMap(n => n.child === undefined ? [] : [n.child]) },
  }, layout: { nodeIndex: nodes.map((_, i) => i), bounds: nodes.map(n => n.bounds),
    styles: nodes.map(n => FILTER_SNAPSHOT_OPTIONS.computedStyles.map(key => str(n.style?.[key] ?? ({ display: 'block', visibility: 'visible', opacity: '1', position: 'static' } as Record<string, string>)[key] ?? 'auto'))) }, scrollOffsetX: 0, scrollOffsetY: 0 })
  const root: Node[] = [
    { tag: 'HTML', parent: -1, bounds: [0, 0, 1261, 944] },
    { tag: 'BODY', parent: 0, bounds: [0, 0, 1261, 944] },
    { tag: 'IFRAME', parent: 1, bounds: [168, 40, options.ownerWidth ?? 1093, 904], child: 1 },
    { tag: 'DIV', parent: 1, bounds: [148, 854, 16, 16], attrs: { class: 'ad-banner-close close-btn' } },
  ]
  const inner: Node[] = [
    { tag: 'HTML', parent: -1, bounds: [0, 0, 1093, 904] },
    { tag: 'BODY', parent: 0, bounds: [0, 0, 1093, 904] },
    { tag: 'DIV', parent: 1, bounds: [0, 0, 1093, 904], attrs: { class: 'boss-popup__wrapper boss-dialog boss-dialog__wrapper dialog-lib-resume recommendV2' } },
    { tag: 'DIV', parent: 2, bounds: [0, 0, 1093, 904], attrs: { class: 'lib-standard-resume with-right-side wasm-resume-layout lib-resume-recommend css-type-2' } },
    { tag: 'IFRAME', parent: 3, bounds: [0, 0, 743, 904], child: 2, attrs: { src: '/web/frame/c-resume/' } },
  ]
  if (!options.missing) {
    inner.push({ tag: 'DIV', parent: 3, bounds: [1063 - (options.shifted ?? 0), 6, 24, 24], attrs: { class: 'close-btn' }, style: options.hidden ? { visibility: 'hidden' } : {} })
    if (options.glyph) inner.push({ tag: 'I', parent: 5, bounds: [1065, 8, 20, 20], attrs: { class: 'iboss-close' } })
  }
  if (options.duplicate) inner.push({ tag: 'DIV', parent: 3, bounds: [1000, 6, 24, 24], attrs: { class: 'close-btn' } })
  inner.push({ tag: 'I', parent: 1, bounds: [1027, 210, 16, 16], attrs: { class: 'icon iboss-close close-btn' } })
  if (options.nestedModal) {
    const modal = inner.length
    inner.push({ tag: 'DIV', parent: 3, bounds: [300, 200, 400, 400], attrs: { class: 'boss-dialog__wrapper dialog-chatbot' } },
      { tag: 'DIV', parent: modal, bounds: [650, 210, 24, 24], attrs: { class: 'close-btn' } })
  }
  const bar = inner.length
  inner.push({ tag: 'DIV', parent: 3, bounds: [850, 80, 200, 140] })
  const trio = inner.length
  inner.push({ tag: 'DIV', parent: bar, bounds: [854, 95, 156, 28] })
  inner.push({ tag: '#text', parent: trio, bounds: [854, 99, 32, 20], text: '收藏' },
    { tag: '#text', parent: trio, bounds: [890, 99, 32, 20], text: '举报' },
    { tag: '#text', parent: trio, bounds: [922, 99, 44, 20], text: '不合适' },
    { tag: '#text', parent: bar, bounds: [874, 148, 42, 16], text: options.greeted ? '继续沟通' : '打招呼' },
    { tag: '#text', parent: 3, bounds: [850, 250, 64, 20], text: '经历概览' })
  const canvas: Node[] = [
    { tag: 'HTML', parent: -1, bounds: [0, 0, 743, 904] },
    { tag: 'BODY', parent: 0, bounds: [0, 0, 743, 904] },
    { tag: 'CANVAS', parent: 1, bounds: [0, 0, 743, 904] },
  ]
  const s: DomSnapshot = { strings, documents: [doc(root), doc(inner), doc(canvas)] }
  s.documents[0]!.scrollOffsetY = options.rootScroll ?? 0
  if (options.noParents) for (const document of s.documents) delete document.nodes.parentIndex
  return s
}
function closedSnapshot(): DomSnapshot {
  const s = snapshot(); s.documents.pop(); s.documents[1]!.nodes.contentDocumentIndex = { index: [], value: [] }; return s
}
function executorScenario(options: Parameters<typeof snapshot>[0] = {}, mode: 'click' | 'escape' | 'neither' = 'click') {
  let closed = false, escapes = 0
  const clicks: ClickPoint[] = []
  const executor = new DetailGreetExecutor({ snapshot: async () => closed ? closedSnapshot() : snapshot(options),
    click: async point => { clicks.push(point); if (mode === 'click') closed = true },
    pressEscape: async () => { escapes++; if (mode === 'escape') closed = true }, sleep: async () => {} })
  return { executor, clicks, escapes: () => escapes }
}

test('independent: close clicks the owning resume × at iframe-adjusted coordinates and ignores other × controls', async () => {
  const h = executorScenario()
  assert.deepEqual(await h.executor.close(), { wasOpen: true })
  assert.deepEqual(h.clicks, [{ x: 1243, y: 58 }]); assert.equal(h.escapes(), 0)
})

test('independent: icon nested inside owning close button does not become a second ambiguous action', async () => {
  const h = executorScenario({ glyph: true })
  await h.executor.close(); assert.equal(h.clicks.length, 1); assert.equal(h.escapes(), 0)
})

test('independent: close coordinates subtract ancestor document scroll rather than using cached screen coordinates', async () => {
  const h = executorScenario({ rootScroll: 20 })
  await h.executor.close(); assert.deepEqual(h.clicks, [{ x: 1243, y: 38 }])
})

for (const [name, options] of [
  ['no owning close control', { missing: true }],
  ['two distinct owning close controls', { duplicate: true }],
  ['hidden owning close control', { hidden: true }],
  ['missing ancestry proof', { noParents: true }],
  ['outside-viewport owning close control', { shifted: -50 }],
  ['close outside its visible iframe but inside root viewport', { ownerWidth: 1000 }],
  ['only nested foreign modal close control', { missing: true, nestedModal: true }],
] as Array<[string, Parameters<typeof snapshot>[0]]>) {
  test(`independent: ${name} preserves verified business Escape without clicking unrelated controls`, async () => {
    const h = executorScenario(options, 'escape'); await h.executor.close()
    assert.deepEqual(h.clicks, []); assert.equal(h.escapes(), 1)
  })
}

test('independent: close makes one input per attempt; only existing outer postcondition retry can try again', async () => {
  let clicks = 0, escapes = 0, attempts = 0
  await assert.rejects(runVerifiedPageOperation({ name: 'boss_close_detail', operationClass: 'page_state', goal: '关闭原详情', expected: '画布消失' }, async attempt => {
    attempts++
    await new DetailGreetExecutor({ snapshot: async () => snapshot(), click: () => attempt.input(async () => { clicks++ }),
      pressEscape: () => attempt.input(async () => { escapes++ }), sleep: async () => {} }).close()
    attempt.verified({ status: 'met', evidence: ['画布消失'] })
  }), error => {
    assert.ok(error instanceof CodedOperationError)
    const facts = error.data?.operation_recovery as { execution: { attempts_used: number; ordinary_retries_used: number } }
    assert.equal(facts.execution.attempts_used, 2); assert.equal(facts.execution.ordinary_retries_used, 1)
    return true
  })
  assert.equal(attempts, 2); assert.equal(clicks, 2); assert.equal(escapes, 0)
})

test('independent: click errors propagate directly without fallback Escape or ordinary retry', async () => {
  let clicks = 0, escapes = 0, attempts = 0
  await assert.rejects(runVerifiedPageOperation({ name: 'boss_close_detail', operationClass: 'page_state', goal: '关闭原详情', expected: '画布消失' }, async attempt => {
    attempts++
    await new DetailGreetExecutor({ snapshot: async () => snapshot(), click: () => attempt.input(async () => { clicks++; throw new WinClickError('输入守卫拒绝', 2) }),
      pressEscape: () => attempt.input(async () => { escapes++ }), sleep: async () => {} }).close()
    attempt.verified({ status: 'met', evidence: ['画布消失'] })
  }))
  assert.equal(attempts, 1); assert.equal(clicks, 1); assert.equal(escapes, 0)
})

test('independent: a retry relocates a shifted original close control instead of reusing the first point', async () => {
  let closed = false, attempts = 0
  const points: ClickPoint[] = []
  const result = await runVerifiedPageOperation({ name: 'boss_close_detail', operationClass: 'page_state', goal: '关闭原详情', expected: '画布消失' }, async attempt => {
    attempts++
    const outcome = await new DetailGreetExecutor({ snapshot: async () => closed ? closedSnapshot() : snapshot({ shifted: attempts === 1 ? 0 : 50 }),
      click: point => attempt.input(async () => { points.push(point); if (attempts === 2) closed = true }),
      pressEscape: async () => assert.fail('no extra keyboard fallback'), sleep: async () => {} }).close()
    attempt.verified({ status: 'met', evidence: ['画布消失'] }); return outcome
  })
  assert.deepEqual(points, [{ x: 1243, y: 58 }, { x: 1193, y: 58 }])
  assert.equal((result.facts.execution as { ordinary_retries_used: number }).ordinary_retries_used, 1)
})

test('independent: cancellation prevents both owning close click and verified Escape', async () => {
  const controller = new AbortController(); controller.abort()
  let inputs = 0
  const executor = new DetailGreetExecutor({ snapshot: async () => snapshot(), click: async () => { inputs++ },
    pressEscape: async () => { inputs++ }, sleep: async () => {}, signal: controller.signal })
  await assert.rejects(executor.close()); assert.equal(inputs, 0)
})

test('independent: no resume canvas remains an idempotent close without any input', async () => {
  let inputs = 0
  const executor = new DetailGreetExecutor({ snapshot: async () => closedSnapshot(), click: async () => { inputs++ },
    pressEscape: async () => { inputs++ }, sleep: async () => {} })
  assert.deepEqual(await executor.close(), { wasOpen: false }); assert.equal(inputs, 0)
})

test('independent: removing the current canvas invalidates the old close target instead of clicking a remaining ×', () => {
  assert.deepEqual(DetailGreetExecutor.locateDetailClose(snapshot()), { x: 1243, y: 58 })
  assert.equal(DetailGreetExecutor.locateDetailClose(closedSnapshot()), null)
})

test('independent: public close operation requests visibility metadata and records its one real mouse input', async () => {
  let closed = false, clicks = 0, escapes = 0
  const options: unknown[] = []
  const session: BossSession = { snapshot: async opts => { options.push(opts); return closed ? closedSnapshot() : snapshot() },
    click: async point => { assert.deepEqual(point, { x: 1243, y: 58 }); clicks++; closed = true },
    pressEscape: async () => { escapes++ }, getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
    clickBrowse: async () => assert.fail('no other click channel'), clickAndType: async () => assert.fail('no typing'),
    mouseWheel: async () => assert.fail('no wheel'), captureFullpage: async () => { throw Error('no screenshot required') } }
  const result = await createBossCloseDetailOperation(async () => session).execute({}, { signal: new AbortController().signal, progress: () => {} })
  assert.equal(result.success, true); assert.equal(clicks, 1); assert.equal(escapes, 0)
  assert.ok(options.length >= 2)
  for (const opts of options) assert.deepEqual(opts, FILTER_SNAPSHOT_OPTIONS)
  const facts = result.data.operation_recovery as { execution: { input_status: string; ordinary_retries_used: number }; verification: { evidence: string[] } }
  assert.equal(facts.execution.input_status, 'attempted'); assert.equal(facts.execution.ordinary_retries_used, 0)
  assert.ok(facts.verification.evidence.every(text => !text.includes('Escape')))
})

test('independent: opening detail uses the same owned close click for residual cleanup when it is available', async () => {
  let closed = false, clicks = 0, escapes = 0
  const executor = new DetailGreetExecutor({ snapshot: async () => closed ? closedSnapshot() : snapshot(),
    click: async () => { clicks++; closed = true }, pressEscape: async () => { escapes++ }, sleep: async () => {} })
  await assert.rejects(executor.openDetail({ name: '列表没有此人' }))
  assert.equal(escapes, 0); assert.equal(clicks, 1)
})

type GreetPhase = 'initial' | 'list' | 'opened' | 'flipped' | 'closed'
function causalGreet(options: { missingClose?: boolean; failure?: 'prepare_error' | 'prepare_stall' | 'cleanup_error' | 'cleanup_stall' } = {}) {
  let phase: GreetPhase = 'initial', greets = 0, openings = 0, closes = 0, escapes = 0
  const state = new BusinessOperationState(), actions: string[] = [], name = '刘草威'
  const closeAction = () => {
    const preparing = phase === 'initial'
    if (options.failure === (preparing ? 'prepare_error' : 'cleanup_error')) throw new WinClickError('原自属关闭控件输入失败', 2)
    if (options.failure === (preparing ? 'prepare_stall' : 'cleanup_stall')) return
    phase = preparing ? 'list' : 'closed'
  }
  const executor = new DetailGreetExecutor({ state,
    snapshot: async () => phase === 'list' || phase === 'closed' ? listSnap([{ name, buttonY: 146 }])
      : snapshot({ missing: options.missingClose, greeted: phase === 'flipped' }),
    click: async point => {
      if (point.x === 1243 && point.y === 58) { closes++; actions.push('close'); closeAction() }
      else if (point.x === 342 && point.y === 138) { openings++; actions.push('open-original'); phase = 'opened' }
      else { assert.deepEqual(point, { x: 1063, y: 196 }); greets++; actions.push('greet'); phase = 'flipped' }
    },
    pressEscape: async () => { escapes++; actions.push('business-Escape'); closeAction() },
    sleep: async () => {},
  })
  return { executor, state, name, actions, counts: () => ({ greets, openings, closes, escapes }) }
}

const guardedGreet = (h: ReturnType<typeof causalGreet>) => withBusinessRecovery(h.state, {
  name: 'boss_greet_detail', operationClass: 'external_effect', target: h.name,
  goal: '向原候选人打招呼一次', expected: '原详情按钮翻转且完成页面收尾',
}, async () => ({ data: { outcome: await h.executor.greet({ name: h.name }) } }))

test('independent: canvas-only identity prepares via owned close and unique original card, commits once, then closes by owned control', async () => {
  const h = causalGreet()
  assert.deepEqual(await h.executor.greet({ name: h.name }), { greeted: true })
  assert.deepEqual(h.actions, ['close', 'open-original', 'greet', 'close'])
  assert.deepEqual(h.counts(), { greets: 1, openings: 1, closes: 2, escapes: 0 })
  assert.deepEqual(h.state.businessEffect, { status: 'confirmed', completed_count: 1 })
  assert.deepEqual(h.state.confirmedItems, [{ name: h.name, greeted: true }])
  assert.equal(h.state.execution.ordinaryRetriesUsed, 0)
})

test('independent: absent owned control preserves the existing business Escape in both preparation and confirmed cleanup', async () => {
  const h = causalGreet({ missingClose: true })
  assert.deepEqual(await h.executor.greet({ name: h.name }), { greeted: true })
  assert.deepEqual(h.actions, ['business-Escape', 'open-original', 'greet', 'business-Escape'])
  assert.deepEqual(h.counts(), { greets: 1, openings: 1, closes: 0, escapes: 2 })
  assert.deepEqual(h.state.businessEffect, { status: 'confirmed', completed_count: 1 })
})

for (const failure of ['prepare_error', 'prepare_stall'] as const) {
  test(`independent: ${failure} stops before greeting and preserves proven not-started status without extra keyboard fallback`, async () => {
    const h = causalGreet({ failure })
    await assert.rejects(guardedGreet(h), error => {
      assert.ok(error instanceof CodedOperationError); assert.ok(!error.message.includes('Escape'))
      const facts = error.data?.operation_recovery as { business_effect: unknown }
      assert.deepEqual(facts.business_effect, { status: 'not_started', completed_count: 0 }); return true
    })
    assert.deepEqual(h.counts(), { greets: 0, openings: 0, closes: 1, escapes: 0 })
    assert.deepEqual(h.state.businessEffect, { status: 'not_started', completed_count: 0 })
    assert.deepEqual(h.state.confirmedItems, [])
  })
}

for (const failure of ['cleanup_error', 'cleanup_stall'] as const) {
  test(`independent: ${failure} never reissues a confirmed greeting or downgrades it to unknown`, async () => {
    const h = causalGreet({ failure })
    await assert.rejects(guardedGreet(h), error => {
      assert.ok(error instanceof CodedOperationError); assert.ok(!error.message.includes('Escape'))
      const facts = error.data?.operation_recovery as { business_effect: unknown }
      assert.deepEqual(facts.business_effect, { status: 'confirmed', completed_count: 1 }); return true
    })
    assert.deepEqual(h.actions, ['close', 'open-original', 'greet', 'close'])
    assert.deepEqual(h.counts(), { greets: 1, openings: 1, closes: 2, escapes: 0 })
    assert.deepEqual(h.state.businessEffect, { status: 'confirmed', completed_count: 1 })
    assert.deepEqual(h.state.confirmedItems, [{ name: h.name, greeted: true }])
    assert.equal(h.state.execution.ordinaryRetriesUsed, 0)
  })
}
