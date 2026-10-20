import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import { VisualRecovery, type VisualContext, type VisualInspectArgs } from '../src/main/boss/visualRecovery.js'
import { DetailGreetExecutor } from '../src/main/boss/DetailGreetExecutor.js'
import { closedListSnap, detailSnap } from './detailGreetFixture.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { CodedOperationError, failResult, okResult, type BossOperation, type OperationResult } from '../src/main/operations/types.js'

type Dict = Record<string, unknown>
const id = '11111111-1111-4111-8111-111111111111'
const ref = '22222222-2222-4222-8222-222222222222'
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
const env = { AID_BOSS_SERVER_URL: 'https://test.invalid', AID_BOSS_SERVER_TOKEN: 'test-only' }
const context: VisualContext = { purpose: 'diagnose', available_actions: ['dismiss_click', 'press_escape'],
  operation: { name: 'boss_filter', result_kind: 'page_state', goal: '筛选', expected: '筛选按钮数字2' },
  execution: { stage: 'postcheck', input_status: 'attempted', attempts_used: 2, ordinary_retries_used: 1 },
  verification: { status: 'not_satisfied', evidence: ['筛选数字未变化'] } }
function original(): OperationResult {
  return failResult('original', 'UI_CHANGED', '筛选后验未达成', 'none', { browser_target_ref: id,
    operation_recovery: { schema_version: 1, operation_class: 'page_state', browser_target_ref: id,
      ...context, error: { code: 'UI_CHANGED', source: 'postcheck', summary: '筛选数字未变化' } } })
}
function visual(action: string | null, present = true): OperationResult {
  return okResult('visual', '诊断', 'none', { ...(action ? { recovery_ref: ref } : {}),
    visual: { http_status: 200, state: 'completed', receipt: { factor: 20, credit_cost: '0.01' },
      diagnosis: { obstruction: present ? 'present' : 'absent', goal_state: present ? 'not_satisfied' : 'satisfied',
        proposed_action: action ? { kind: action } : null } } })
}

test('independent: every fresh diagnostic step advertises only click, fresh postcheck advertises none', async () => {
  const seen: VisualInspectArgs[] = [], sequence = [visual('dismiss'), visual(null, false)]
  let calls = 0, clicks = 0
  const op: BossOperation<Dict> = { name: 'boss_filter', execute: async () => ++calls === 1 ? original() : okResult('resume', '筛选2', 'none') }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', execute: async args => {
    seen.push(args); return sequence.shift()!
  } }
  const dismiss: BossOperation<{ recovery_ref?: string }> = { name: 'boss_overlay_dismiss', execute: async () => { clicks++; return okResult('click', '输入已投递', 'none') } }
  const result = await withLocalRecovery(op, { inspect, dismiss, env }).execute({}, { signal: new AbortController().signal, progress: () => {} })
  assert.equal(result.success, true); assert.equal(calls, 2); assert.equal(clicks, 1)
  assert.deepEqual(seen.map(a => a.visual_context?.available_actions), [['dismiss_click'], []])
})

test('independent: malicious legacy Escape with even an executable ref never reaches the input primitive', async () => {
  let inputs = 0, calls = 0
  const seen: VisualInspectArgs[] = []
  const op: BossOperation<Dict> = { name: 'boss_filter', execute: async () => { calls++; return original() } }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', execute: async args => { seen.push(args); return visual('press_escape') } }
  const dismiss: BossOperation<{ recovery_ref?: string }> = { name: 'boss_overlay_dismiss', execute: async () => { inputs++; throw Error('must never execute') } }
  const result = await withLocalRecovery(op, { inspect, dismiss, env }).execute({}, { signal: new AbortController().signal, progress: () => {} })
  assert.equal((result.data.visual_recovery as Dict).state, 'no_supported_action')
  assert.equal(inputs, 0); assert.equal(calls, 1); assert.equal(seen.length, 1)
  assert.equal(((result.data.visual_recovery as Dict).receipts as unknown[]).length, 1)
})

test('independent: pending receipt polls same request id without adding keyboard capabilities or new billing', async () => {
  const seen: VisualInspectArgs[] = []
  const op: BossOperation<Dict> = { name: 'boss_filter', execute: async () => original() }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', execute: async args => {
    seen.push(args)
    return seen.length === 1 ? okResult('pending', '待定', 'none', { visual: { http_status: 202, state: 'pending' } }) : visual('press_escape')
  } }
  const result = await withLocalRecovery(op, { inspect, env }).execute({}, { signal: new AbortController().signal, progress: () => {} })
  assert.equal(seen.length, 2); assert.equal(seen[0]!.request_id, seen[1]!.request_id)
  assert.equal(seen[1]!.retrieve_only, true); assert.equal(seen[1]!.visual_context, undefined)
  assert.equal(((result.data.visual_recovery as Dict).receipts as unknown[]).length, 1)
})

test('independent: old on-disk Escape ref retains diagnosis but cannot inspect-grant or execute any input', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-click-only-independent-'))
  try {
    let inputs = 0, snapshots = 0, backendCalls = 0
    const session = { browserTargetRef: id, getUrl: async () => 'https://www.zhipin.com/web/chat/recommend',
      captureFullpage: async () => { snapshots++; return png }, clickBrowse: async () => { inputs++ },
      pressEscape: async () => { inputs++ } } as unknown as BossSession
    const body = { api_version: 2, request_id: id, state: 'completed', diagnosis: {
      page_summary: '打印预览', obstruction: 'present', goal_state: 'not_satisfied', reason: '遮住页面', missing_evidence: [],
      proposed_action: { kind: 'press_escape', target_label: '打印预览' } }, receipt: { factor: 50, credit_cost: '1.23' } }
    await mkdir(join(root, id))
    const record = { request_id: id, recovery_ref: ref, captured_at: new Date().toISOString(),
      url: 'https://www.zhipin.com/web/chat/recommend', browser_target_ref: id, context, image: { width: 1, height: 1 },
      response: { status: 200, body } }
    await writeFile(join(root, id, 'record.json'), JSON.stringify(record))
    await writeFile(join(root, id, 'page.png'), png)
    await writeFile(join(root, `${ref}.ref`), id)
    const recovery = new VisualRecovery({ root, diagnose: async () => { backendCalls++; throw Error('cached receipt must not redispatch') } })
    await assert.rejects(recovery.dismiss(ref, session, new AbortController().signal), error => {
      assert.ok(error instanceof CodedOperationError)
      assert.equal(error.data?.input_status, 'not_started')
      assert.equal(error.data?.recovery_action_error, 'NO_VALID_ACTION')
      return true
    })
    const result = await recovery.inspect({ request_id: id, retrieve_only: true }, session, new AbortController().signal)
    assert.equal(result.recovery_ref, undefined); assert.equal(result.recovery_action_error, 'UNSUPPORTED_ACTION')
    assert.deepEqual((result.visual as Dict).diagnosis, body.diagnosis)
    assert.deepEqual((result.visual as Dict).receipt, body.receipt)
    assert.deepEqual(JSON.parse(await readFile(join(root, id, 'record.json'), 'utf8')), record)
    assert.equal(inputs, 0); assert.equal(snapshots, 0); assert.equal(backendCalls, 0)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('independent: ordinary user-verified close-detail still uses its domain Escape and confirms canvas disappearance', async () => {
  let escaped = 0, clicks = 0
  const executor = new DetailGreetExecutor({ snapshot: async () => escaped ? closedListSnap() : detailSnap({ name: '测试候选人', state: 'greet' }),
    click: async () => { clicks++ }, pressEscape: async () => { escaped++ }, sleep: async () => {} })
  assert.deepEqual(await executor.close(), { wasOpen: true })
  assert.equal(escaped, 1); assert.equal(clicks, 0)
})

test('independent: cancellation after a paid diagnosis retains billing and never issues any recovery input', async () => {
  const controller = new AbortController()
  let inputs = 0
  const op: BossOperation<Dict> = { name: 'boss_filter', execute: async () => original() }
  const inspect: BossOperation<VisualInspectArgs> = { name: 'boss_overlay_inspect', execute: async () => { controller.abort(); return visual('dismiss') } }
  const dismiss: BossOperation<{ recovery_ref?: string }> = { name: 'boss_overlay_dismiss', execute: async () => { inputs++; throw Error('cancelled') } }
  const result = await withLocalRecovery(op, { inspect, dismiss, env }).execute({}, { signal: controller.signal, progress: () => {} })
  assert.equal((result.data.visual_recovery as Dict).state, 'cancelled')
  assert.equal(((result.data.visual_recovery as Dict).receipts as unknown[]).length, 1)
  assert.equal(inputs, 0)
})
