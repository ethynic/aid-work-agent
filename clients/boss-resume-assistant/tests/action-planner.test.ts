import assert from 'node:assert/strict'
import test from 'node:test'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { ActionPlanner, DEFAULT_ACTION_LIMITS } from '../src/main/actions/ActionPlanner.js'
import { ActionStore } from '../src/main/actions/ActionStore.js'
import { mapRejectReason } from '../src/main/actions/reasonMapping.js'

/** 构造单 document 的 DOMSnapshot：每个 item 一个有布局的文本节点 */
function snapWith(items: Array<{ text: string; bounds: [number, number, number, number] }>): DomSnapshot {
  const strings: string[] = []
  const nodeValue: number[] = []
  const layoutNodeIndex: number[] = []
  const boundsArr: number[][] = []
  items.forEach((it, nodeIdx) => {
    let si = strings.findIndex((s) => s === it.text)
    if (si < 0) {
      strings.push(it.text)
      si = strings.length - 1
    }
    nodeValue.push(si)
    layoutNodeIndex.push(nodeIdx)
    boundsArr.push([...it.bounds])
  })
  return {
    strings,
    documents: [
      { nodes: { nodeValue, contentDocumentIndex: [] }, layout: { nodeIndex: layoutNodeIndex, bounds: boundsArr } },
    ],
  }
}

const VIEWPORT = { width: 1000, height: 800 }
const GREET_SNAP = snapWith([{ text: '打招呼', bounds: [400, 400, 80, 32] }])
const REJECT_SNAP = snapWith([{ text: '不合适', bounds: [400, 400, 80, 32] }])

function baseInput(overrides: Record<string, unknown> = {}) {
  return {
    conclusion: 'QUALIFIED' as const,
    evalReason: 'all hard rules passed',
    evalCandidateFingerprint: 'fp-abc',
    currentCandidateFingerprint: 'fp-abc',
    snapshot: GREET_SNAP,
    viewport: VIEWPORT,
    sessionAttemptCount: 0,
    dailyAttemptCount: 0,
    alreadyRecorded: false,
    ...overrides,
  }
}

const planner = new ActionPlanner()

test('QUALIFIED + 唯一打招呼按钮 → GREET 计划，unique_key 为 指纹|GREET', () => {
  const plan = planner.plan(baseInput())
  assert.equal(plan.kind, 'GREET')
  if (plan.kind === 'GREET') {
    assert.equal(plan.uniqueKey, 'fp-abc|GREET')
  }
})

test('REJECTED + 可映射理由 → REJECT 计划并携带原因选项', () => {
  const plan = planner.plan(
    baseInput({ conclusion: 'REJECTED', evalReason: '经验不足3年', snapshot: REJECT_SNAP }),
  )
  assert.equal(plan.kind, 'REJECT')
  if (plan.kind === 'REJECT') {
    assert.equal(plan.reasonOption, '经验不匹配')
    assert.equal(plan.uniqueKey, 'fp-abc|REJECT')
  }
})

test('UNCERTAIN → NO_ACTION（进人工复核）', () => {
  const plan = planner.plan(baseInput({ conclusion: 'UNCERTAIN' }))
  assert.equal(plan.kind, 'NO_ACTION')
})

test('前置拦截：当前指纹与评估记录不一致 → NO_ACTION', () => {
  const plan = planner.plan(baseInput({ currentCandidateFingerprint: 'fp-other' }))
  assert.equal(plan.kind, 'NO_ACTION')
  if (plan.kind === 'NO_ACTION') assert.match(plan.blockedReason, /指纹/)
})

test('前置拦截：按钮歧义（两个打招呼）→ NO_ACTION', () => {
  const ambiguous = snapWith([
    { text: '打招呼', bounds: [100, 400, 80, 32] },
    { text: '打招呼', bounds: [400, 400, 80, 32] },
  ])
  const plan = planner.plan(baseInput({ snapshot: ambiguous }))
  assert.equal(plan.kind, 'NO_ACTION')
  if (plan.kind === 'NO_ACTION') assert.match(plan.blockedReason, /按钮定位失败/)
})

test('前置拦截：按钮不存在 → NO_ACTION', () => {
  const plan = planner.plan(baseInput({ snapshot: snapWith([{ text: '继续沟通', bounds: [400, 400, 80, 32] }]) }))
  assert.equal(plan.kind, 'NO_ACTION')
})

test('前置拦截：超会话上限 → NO_ACTION', () => {
  const plan = planner.plan(baseInput({ sessionAttemptCount: 50 }))
  assert.equal(plan.kind, 'NO_ACTION')
  if (plan.kind === 'NO_ACTION') assert.match(plan.blockedReason, /会话动作上限/)
})

test('前置拦截：超每日上限 → NO_ACTION', () => {
  const plan = planner.plan(baseInput({ dailyAttemptCount: 100 }))
  assert.equal(plan.kind, 'NO_ACTION')
  if (plan.kind === 'NO_ACTION') assert.match(plan.blockedReason, /每日动作上限/)
})

test('上限可配置：自定义 perSession 立即生效', () => {
  const plan = planner.plan(baseInput({ sessionAttemptCount: 3, limits: { perSession: 3 } }))
  assert.equal(plan.kind, 'NO_ACTION')
  const ok = planner.plan(baseInput({ sessionAttemptCount: 3, limits: { perSession: 10 } }))
  assert.equal(ok.kind, 'GREET')
})

test('默认上限为会话 50 / 日 100', () => {
  assert.deepEqual(DEFAULT_ACTION_LIMITS, { perSession: 50, perDay: 100 })
})

test('前置拦截：该动作已在本地记录 → NO_ACTION（幂等）', () => {
  const plan = planner.plan(baseInput({ alreadyRecorded: true }))
  assert.equal(plan.kind, 'NO_ACTION')
  if (plan.kind === 'NO_ACTION') assert.match(plan.blockedReason, /幂等/)
})

test('REJECT 理由无法映射 → NO_ACTION（不执行，进复核）', () => {
  const plan = planner.plan(
    baseInput({ conclusion: 'REJECTED', evalReason: '无法归类的综合考量', snapshot: REJECT_SNAP }),
  )
  assert.equal(plan.kind, 'NO_ACTION')
  if (plan.kind === 'NO_ACTION') assert.match(plan.blockedReason, /无法映射/)
})

test('unique_key 构成：candidate_fingerprint|action_type', () => {
  assert.equal(ActionStore.makeUniqueKey('fp-1', 'GREET'), 'fp-1|GREET')
  assert.equal(ActionStore.makeUniqueKey('fp-1', 'REJECT'), 'fp-1|REJECT')
})

test('原因映射：关键词命中各选项', () => {
  assert.equal(mapRejectReason('工作年限不足'), '经验不匹配')
  assert.equal(mapRejectReason('要求本科以上学历'), '学历不匹配')
  assert.equal(mapRejectReason('专业不符'), '专业不匹配')
  assert.equal(mapRejectReason('期望薪资过高'), '薪资期望不符')
  assert.equal(mapRejectReason('通勤距离太远'), '通勤距离不合适')
  assert.equal(mapRejectReason('跳槽频繁'), '稳定性不足')
})

test('原因映射：未命中与空输入 → undefined', () => {
  assert.equal(mapRejectReason('各方面都不错但不合适'), undefined)
  assert.equal(mapRejectReason(''), undefined)
})
