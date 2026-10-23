import assert from 'node:assert/strict'
import fs from 'node:fs'
import test, { type TestContext } from 'node:test'
import { FilterSetter, PanelScrollError } from '../src/main/boss/FilterSetter.js'
import { createBossFilterOperation } from '../src/main/operations/bossFilter.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { membershipAccountKey } from '../src/main/boss/MembershipInspector.js'
import { withAccount } from './filterFixture.js'

const read = (name: string): DomSnapshot => withAccount(JSON.parse(fs.readFileSync(new URL(`../../tests/fixtures/boss-vip-panel-${name}.json`, import.meta.url), 'utf8')))
const top = read('top'), down = read('down')
function selected(snapshot: DomSnapshot, text: string): DomSnapshot {
  const s = structuredClone(snapshot), d = s.documents[2]!
  s.strings.push('class', 'option active')
  const values = d.nodes.nodeValue as number[], parents = d.nodes.parentIndex as number[], attrs = d.nodes.attributes as number[][]
  const node = values.findIndex((v) => s.strings[v]?.trim() === text)
  attrs[parents[node]!] = [s.strings.length - 2, s.strings.length - 1]
  return s
}
function closed(count: number): DomSnapshot {
  return withAccount({ strings: ['', `筛选·${count}`], documents: [{ nodes: { nodeValue: [0, 1], contentDocumentIndex: [] }, layout: { nodeIndex: [0, 1], bounds: [[0, 0, 2506, 1476], [2200, 24, 60, 31]] } }] })
}
/** A real row's inner wrapper moves with panel content. Its hidden clip must still protect the click. */
function clippedRow(snapshot: DomSnapshot, option: string): DomSnapshot {
  const s = structuredClone(snapshot), d = s.documents[2]!
  const node = (d.nodes.nodeValue as number[]).findIndex((v) => s.strings[v]?.trim() === option)
  const parents = d.nodes.parentIndex as number[]
  const wrapper = parents[parents[parents[node]!]!]!
  s.strings.push('hidden')
  const index = d.layout.nodeIndex.indexOf(wrapper)
  d.layout.styles![index]![1] = s.strings.length - 1
  return s
}
function queue(snaps: DomSnapshot[]) { let i = 0; return async () => snaps[Math.min(i++, snaps.length - 1)]! }

test('nested hidden row wholly below the scrollport scrolls from its real viewport, then clicks the fresh row', async () => {
  const wheels: number[][] = [], clicks: Array<{ x: number; y: number }> = []
  const a = clippedRow(top, '20-50K'), b = clippedRow(down, '20-50K')
  const setter = new FilterSetter({ snapshot: queue([a, a, b, selected(b, '20-50K'), closed(1)]),
    click: async (p) => { clicks.push(p) }, sleep: async () => {}, mouseWheel: async (...args) => { wheels.push(args) } })
  assert.equal((await setter.apply({ salary: '20-50K' })).filterCount, 1)
  assert.equal(wheels.length, 1)
  assert.equal(wheels[0]![1], (80 + 106 + 1096 - 100) / 2)
  assert.ok(wheels[0]![2]! > 0)
  assert.ok(clicks[1]!.y < 1282, 'the old offscreen location is never clicked')
})

test('nested hidden row above the scrollport scrolls upward without broadening the click clip', async () => {
  const wheels: number[] = []
  const a = clippedRow(down, '男'), b = clippedRow(top, '男')
  const setter = new FilterSetter({ snapshot: queue([a, a, b, selected(b, '男'), closed(1)]),
    click: async () => {}, sleep: async () => {}, mouseWheel: async (_x, _y, dy) => { wheels.push(dy) } })
  assert.equal((await setter.apply({ membership_hint: 'vip', membership_account_ref: membershipAccountKey(top)!, filters: [{ label: '性别', options: ['男'] }] })).filterCount, 1)
  assert.equal(wheels.length, 1)
  assert.ok(wheels[0]! < 0)
})

test('a genuinely clipped outer scrollport still refuses both wheel and candidate input', async () => {
  const s = structuredClone(top), d = s.documents[2]!
  d.layout.bounds[d.layout.nodeIndex.indexOf(13)]![1] = 2000
  let wheels = 0, clicks = 0
  const setter = new FilterSetter({ snapshot: async () => s, click: async () => { clicks++ }, sleep: async () => {}, mouseWheel: async () => { wheels++ } })
  await assert.rejects(setter.apply({ salary: '20-50K' }), /真实可视滚动区域为空/)
  assert.equal(wheels, 0)
  assert.equal(clicks, 1, 'only the existing footer clear was clicked; no option is clicked through clipping')
})

test('stationary internal clipping is not bypassed just because the outer scrollport is visible', async () => {
  const s = clippedRow(down, '20-50K'), d = s.documents[2]!
  const node = (d.nodes.nodeValue as number[]).findIndex((v) => s.strings[v]?.trim() === '20-50K')
  const parents = d.nodes.parentIndex as number[], wrapper = parents[parents[parents[node]!]!]!
  d.layout.bounds[d.layout.nodeIndex.indexOf(wrapper)]![1] = 3000
  let wheels = 0, clicks = 0
  const setter = new FilterSetter({ snapshot: async () => s, click: async () => { clicks++ }, sleep: async () => {}, mouseWheel: async () => { wheels++ } })
  await assert.rejects(setter.apply({ salary: '20-50K' }), /内部裁剪/)
  assert.equal(wheels, 0); assert.equal(clicks, 1)
})

function immediateTimers(t: TestContext) {
  t.mock.method(globalThis, 'setTimeout', ((callback: () => void) => { queueMicrotask(callback); return 0 as unknown as ReturnType<typeof setTimeout> }) as typeof setTimeout)
}
function scriptedSessions(mode: 'draft_failure' | 'commit_lost' | 'cancel', abort?: AbortController, resumeRead?: DomSnapshot) {
  let connections = 0
  const clicks: number[] = [], snapshots: number[] = []
  const factory = async (): Promise<BossSession> => {
    const id = connections++; clicks[id] = 0; snapshots[id] = 0
    let page = top
    return {
      getUrl: async () => 'https://www.zhipin.com/web/chat/recommend',
      snapshot: async () => {
        snapshots[id] = snapshots[id]! + 1
        if (mode === 'commit_lost' && id === 0 && clicks[id]! >= 3) throw new PanelScrollError('提交后快照暂不可读')
        if (mode === 'commit_lost' && id > 0 && snapshots[id] === 1) return resumeRead ?? closed(1)
        return page
      },
      click: async () => {
        clicks[id] = clicks[id]! + 1
        if (mode === 'cancel') abort!.abort()
        if (clicks[id] === 2) page = selected(down, '20-50K')
        if (clicks[id] === 3) {
          page = closed(1)
          if (mode === 'commit_lost' && id === 0) throw new WinClickError('[BOSS_INPUT_UNKNOWN] confirm response lost', 1)
        }
      },
      mouseWheel: async () => {
        if (mode === 'draft_failure' && id === 0) throw new PanelScrollError('面板定位暂不可用')
        page = down
      },
      clickBrowse: async () => {}, clickAndType: async () => {}, pressEscape: async () => {},
      captureFullpage: async () => Buffer.alloc(0), close: async () => {},
    }
  }
  return { factory, clicks, snapshots }
}

test('clear then failure exports edited draft rather than applied/none; continuation reuses original spec and submits it', async (t) => {
  immediateTimers(t)
  const h = scriptedSessions('draft_failure'), continuation: NonNullable<OpContext['recoveryContinuation']> = {}
  const ctx: OpContext = { signal: new AbortController().signal, progress: () => {}, recoveryContinuation: continuation }
  const result = await createBossFilterOperation(h.factory).execute({ salary: '20-50K' }, ctx)
  assert.equal(result.success, false); assert.equal(result.effect, 'partial')
  const facts = result.data.operation_recovery as any
  assert.ok(facts.verification.evidence.includes('filter_phase=draft_edited'))
  assert.match(facts.operation.expected, /正常筛选编辑面板不是障碍/)
  assert.equal(result.data.applied, undefined)
  assert.deepEqual(h.clicks, [1])
  const resumed = await continuation.resume!(ctx)
  assert.equal(resumed.success, true); assert.equal(resumed.data.applied, true)
  assert.deepEqual(resumed.data.applied_rows, [{ label: '薪资待遇', options: ['20-50K'] }])
  assert.deepEqual(h.clicks, [1, 3])
  assert.equal((resumed.data.operation_recovery as any).execution.attempts_used, 2)
})

test('lost commit proof survives new session: fresh satisfied postcheck returns original rows with zero new inputs', async (t) => {
  immediateTimers(t)
  const h = scriptedSessions('commit_lost'), continuation: NonNullable<OpContext['recoveryContinuation']> = {}
  const ctx: OpContext = { signal: new AbortController().signal, progress: () => {}, recoveryContinuation: continuation }
  const result = await createBossFilterOperation(h.factory).execute({ salary: '20-50K' }, ctx)
  assert.equal(result.success, false); assert.equal(result.code, 'EXECUTION_UNKNOWN')
  assert.ok((result.data.operation_recovery as any).verification.evidence.includes('filter_phase=submitted'))
  const resumed = await continuation.resume!(ctx)
  assert.equal(resumed.success, true)
  assert.deepEqual(resumed.data.applied_rows, [{ label: '薪资待遇', options: ['20-50K'] }])
  assert.deepEqual(h.clicks, [3, 0]); assert.equal(h.snapshots[1], 1)
})

test('cancellation after clear preserves draft fact without attempting an option or commit', async (t) => {
  immediateTimers(t)
  const abort = new AbortController(), h = scriptedSessions('cancel', abort)
  const result = await createBossFilterOperation(h.factory).execute({ salary: '20-50K' }, { signal: abort.signal, progress: () => {} })
  assert.equal(result.code, 'CANCELLED'); assert.equal(result.effect, 'partial')
  assert.ok((result.data.operation_recovery as any).verification.evidence.includes('filter_phase=draft_edited'))
  assert.deepEqual(h.clicks, [1])
})

test('submitted state with an unreadable fresh toolbar cannot silently clear and replay the spec', async (t) => {
  immediateTimers(t)
  const unreadable = withAccount({ strings: [''], documents: [{ nodes: { nodeValue: [0], contentDocumentIndex: [] }, layout: { nodeIndex: [0], bounds: [[0, 0, 2506, 1476]] } }] })
  const h = scriptedSessions('commit_lost', undefined, unreadable), continuation: NonNullable<OpContext['recoveryContinuation']> = {}
  const ctx: OpContext = { signal: new AbortController().signal, progress: () => {}, recoveryContinuation: continuation }
  await createBossFilterOperation(h.factory).execute({ salary: '20-50K' }, ctx)
  // All fresh postchecks stay inconclusive; no option is submitted while its prior effect is unknown.
  const resumed = await continuation.resume!(ctx)
  assert.equal(resumed.success, false)
  assert.deepEqual(h.clicks, [3, 0])
  assert.ok((resumed.data.operation_recovery as any).verification.evidence.includes('filter_phase=submitted'))
})

test('a readable unmet submitted postcondition permits original-spec reset, then verifies actual selection and commit', async (t) => {
  immediateTimers(t)
  const h = scriptedSessions('commit_lost', undefined, closed(0)), continuation: NonNullable<OpContext['recoveryContinuation']> = {}
  const ctx: OpContext = { signal: new AbortController().signal, progress: () => {}, recoveryContinuation: continuation }
  await createBossFilterOperation(h.factory).execute({ salary: '20-50K' }, ctx)
  const resumed = await continuation.resume!(ctx)
  assert.equal(resumed.success, true)
  assert.deepEqual(resumed.data.applied_rows, [{ label: '薪资待遇', options: ['20-50K'] }])
  assert.deepEqual(h.clicks, [3, 3])
})

test('certified zero input at clear is not reported as edited draft, even with a matching old badge', async (t) => {
  immediateTimers(t)
  let calls = 0
  const session: BossSession = {
    getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', snapshot: async () => top,
    click: async () => { calls++; throw new WinClickError('render widget hidden', 2) },
    mouseWheel: async () => {}, clickBrowse: async () => {}, clickAndType: async () => {}, pressEscape: async () => {},
    captureFullpage: async () => Buffer.alloc(0), close: async () => {},
  }
  const result = await createBossFilterOperation(async () => session).execute({ salary: '20-50K' }, { signal: new AbortController().signal, progress: () => {} })
  assert.equal(result.success, false); assert.equal(result.effect, 'none'); assert.equal(calls, 1)
  const facts = result.data.operation_recovery as any
  assert.equal(facts.execution.input_status, 'not_started')
  assert.ok(facts.verification.evidence.includes('filter_phase=not_started'))
})
