import assert from 'node:assert/strict'
import test from 'node:test'
import { FilterSetter, FilterSetError, FilterPostconditionError, type FilterApplyResult } from '../src/main/boss/FilterSetter.js'
import { createBossFilterOperation, createBossClearFilterOperation } from '../src/main/operations/bossFilter.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { CancelledError } from '../src/main/operations/types.js'
import { doctorCommand } from '../src/cli/commands/doctor.js'
import { closedListSnap } from './detailGreetFixture.js'

// These tests exercise the real operation/error envelope. Only the domain executor
// is scripted: browser/input correctness belongs to its separate snapshot tests.
const applied: FilterApplyResult = { filterCount: 1, substitutions: [],
  appliedRows: [{ label: '经验要求', options: ['5-10年'] }], firstDegreeOnly: false,
  membership: { status: 'unknown', source: 'unknown', evidence: [] }, skippedFilters: [], applied: true }
function harness() {
  let closed = 0
  const session: BossSession = {
    snapshot: async () => { throw new Error('unexpected external read') },
    getUrl: async () => 'https://www.zhipin.com/web/chat/recommend',
    click: async () => {}, clickBrowse: async () => {}, clickAndType: async () => {},
    mouseWheel: async () => {}, pressEscape: async () => {},
    captureFullpage: async () => Buffer.alloc(0), close: async () => { closed++ },
  }
  return { factory: async () => session, ctx: { signal: new AbortController().signal, progress: () => {} }, closed: () => closed }
}
function negative(status: 'unmet' | 'inconclusive' = 'unmet') {
  return { status, expectedCount: 1, observedCount: status === 'unmet' ? 0 : null, panelOpen: false }
}
async function input(setter: FilterSetter) {
  const deps = (setter as unknown as { deps: { click(point: { x: number; y: number }, viewport: { width: number; height: number }): Promise<void> } }).deps
  await deps.click({ x: 10, y: 10 }, { width: 100, height: 100 })
}

test('independent: definite postcondition mismatch retries exactly once and exports the shared budget', async (t) => {
  const h = harness(); let attempts = 0
  t.mock.method(FilterSetter.prototype, 'apply', async function (this: FilterSetter) { await input(this); attempts++; throw new FilterPostconditionError('badge mismatch') })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => negative())
  const r = await createBossFilterOperation(h.factory).execute({ experience: '5-10年' }, h.ctx)
  assert.equal(r.success, false); assert.equal(attempts, 2); assert.equal(h.closed(), 1)
  const facts = r.data.page_operation as Record<string, any>
  assert.equal(facts.schema_version, 1); assert.equal(facts.operation.name, 'boss_filter')
  assert.equal(facts.execution.attempts_used, 2); assert.equal(facts.execution.ordinary_retries_used, 1)
  assert.equal(facts.verification.status, 'not_satisfied')
})

test('independent: direct executor error never consumes an ordinary retry even with definite negative readback', async (t) => {
  const h = harness(); let attempts = 0
  t.mock.method(FilterSetter.prototype, 'apply', async function (this: FilterSetter) { await input(this); attempts++; throw new FilterSetError('target obscured') })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => negative())
  const r = await createBossFilterOperation(h.factory).execute({ experience: '5-10年' }, h.ctx)
  assert.equal(attempts, 1); assert.equal(r.success, false)
  assert.equal((r.data.page_operation as any).execution.ordinary_retries_used, 0)
})

test('independent: unreadable postcondition is inconclusive, never definite failure or ordinary retry', async (t) => {
  const h = harness(); let attempts = 0
  t.mock.method(FilterSetter.prototype, 'apply', async function (this: FilterSetter) { await input(this); attempts++; throw new FilterSetError('结果无法确认') })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => negative('inconclusive'))
  const r = await createBossFilterOperation(h.factory).execute({ experience: '5-10年' }, h.ctx)
  assert.equal(attempts, 1); assert.equal((r.data.page_operation as any).verification.status, 'inconclusive')
})

test('independent: an already proven row mismatch is not erased by a later unreadable toolbar', async (t) => {
  const h = harness(); let attempts = 0
  t.mock.method(FilterSetter.prototype, 'apply', async function (this: FilterSetter) { await input(this); attempts++; throw new FilterPostconditionError('已读取选中行但不匹配') })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => negative('inconclusive'))
  const r = await createBossFilterOperation(h.factory).execute({ experience: '5-10年' }, h.ctx)
  assert.equal(attempts, 2); assert.equal(r.success, false)
  assert.equal((r.data.page_operation as any).execution.ordinary_retries_used, 1)
})

test('independent: pre-input validation failure cannot claim an unrelated existing filter as late success', async (t) => {
  const h = harness(); let reads = 0
  t.mock.method(FilterSetter.prototype, 'apply', async () => { throw new FilterSetError('页面没有请求的档位') })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => { reads++; return { status: 'met', expectedCount: 1, observedCount: 1, panelOpen: false, result: applied } })
  const r = await createBossFilterOperation(h.factory).execute({ experience: '5-10年' }, h.ctx)
  assert.equal(r.success, false); assert.equal(reads, 0)
})

test('independent: late success after lost confirm response retains applied row facts without another input', async (t) => {
  const h = harness(); let attempts = 0
  t.mock.method(FilterSetter.prototype, 'apply', async function (this: FilterSetter) { await input(this); attempts++; throw new FilterSetError('confirm response lost') })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => ({ status: 'met', expectedCount: 1, observedCount: 1, panelOpen: false, result: applied }))
  const r = await createBossFilterOperation(h.factory).execute({ experience: '5-10年' }, h.ctx)
  assert.equal(r.success, true); assert.equal(attempts, 1); assert.deepEqual(r.data.applied_rows, applied.appliedRows)
  assert.equal((r.data.page_operation as any).verification.status, 'satisfied')
})

test('independent: filter count alone cannot fabricate applied rows after a failed executor', async (t) => {
  const h = harness()
  t.mock.method(FilterSetter.prototype, 'apply', async function (this: FilterSetter) { await input(this); throw new FilterSetError('row proof unavailable') })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => ({ status: 'met', expectedCount: 1, observedCount: 1, panelOpen: false }))
  const r = await createBossFilterOperation(h.factory).execute({ experience: '5-10年' }, h.ctx)
  assert.equal(r.success, false); assert.equal(r.data.applied_rows, undefined)
})

test('independent: clear can acknowledge late proven zero without requiring filter apply result', async (t) => {
  const h = harness(); let attempts = 0
  t.mock.method(FilterSetter.prototype, 'clear', async function (this: FilterSetter) { await input(this); attempts++; throw new FilterSetError('confirm response lost') })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => ({ status: 'met', expectedCount: 0, observedCount: 0, panelOpen: false }))
  const r = await createBossClearFilterOperation(h.factory).execute({}, h.ctx)
  assert.equal(r.success, true); assert.equal(attempts, 1); assert.equal(r.data.filter_count, 0)
})

test('independent: cancellation is not converted into a recoverable page failure and releases session', async (t) => {
  const h = harness(); let reads = 0
  t.mock.method(FilterSetter.prototype, 'clear', async () => { throw new CancelledError() })
  t.mock.method(FilterSetter.prototype, 'verifyLastPostcondition', async () => { reads++; return negative() })
  const r = await createBossClearFilterOperation(h.factory).execute({}, h.ctx)
  assert.equal(r.code, 'CANCELLED'); assert.equal(reads, 0); assert.equal(r.data.page_operation, undefined); assert.equal(h.closed(), 1)
})

test('independent: doctor reports the selected target and strips query/fragment from page inventory', async (t) => {
  const output: string[] = []; let closed = false
  t.mock.method(console, 'log', (value: unknown) => { output.push(String(value)) })
  const snapshot = closedListSnap(); snapshot.strings.push('筛选')
  const code = await doctorCommand({ json: true, gateway: {
    connect: async () => {},
    getTargets: async () => [
      { targetId: 'original', type: 'page', url: 'https://www.zhipin.com/web/chat/recommend?token=fictional#secret', title: 'BOSS' },
      { targetId: 'lookalike', type: 'page', url: 'https://www.zhipin.com.evil.example/web/chat/recommend', title: 'other' },
      { targetId: 'worker', type: 'worker', url: 'https://www.zhipin.com/worker', title: 'worker' },
    ],
    attachToRecommendPage: async () => ({ targetId: 'original', sessionId: 'bound-session' }),
    getTargetId: () => 'original', captureDomSnapshot: async () => snapshot,
    close: async () => { closed = true },
  } })
  assert.equal(code, 0); assert.equal(closed, true); assert.equal(output.length, 1)
  const result = JSON.parse(output[0]!)
  assert.equal(result.selected_target_id, 'original')
  assert.deepEqual(result.boss_pages, [{ target_id: 'original', url: 'https://www.zhipin.com/web/chat/recommend' }])
  assert.equal(output[0]!.includes('fictional'), false); assert.equal(output[0]!.includes('secret'), false)
})
