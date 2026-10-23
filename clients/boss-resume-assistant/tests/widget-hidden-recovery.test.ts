import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { nativeWindowInputFailure, WinClickError, WinMouseClicker } from '../src/main/input/WinMouseClicker.js'
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import { ResumeReader } from '../src/main/boss/ResumeReader.js'
import { createBossResumeBatchOperation } from '../src/main/operations/bossResumeBatch.js'
import { createBossOverlayInspectOperation } from '../src/main/operations/bossOverlayInspect.js'
import { failResult, okResult, CancelledError, type BossOperation } from '../src/main/operations/types.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { loadedSnapshot } from './loadedCandidateFixture.js'
import { detailSnap } from './detailGreetFixture.js'

const hidden = () => new WinClickError('STALE_OBSERVATION: render widget hidden; input refused', 2)
test('lifecycle classification needs typed certified zero input; stale cursors and unknown input are excluded', () => {
  assert.equal(nativeWindowInputFailure(hidden()), 'render_widget_hidden')
  for (const error of [new Error(hidden().message), new WinClickError(hidden().message, 1),
    new WinClickError('STALE_OBSERVATION: cursor_changed', 2),
    new WinClickError('[BOSS_INPUT_UNKNOWN] STALE_OBSERVATION: render widget hidden; input refused', 2)]) {
    assert.equal(nativeWindowInputFailure(error), undefined)
  }
})

test('native lifecycle recovery has two no-progress reconnects; transport semantics and writes remain separate', async () => {
  for (const name of ['boss_resume_next', 'boss_send_current']) {
    let resumed = 0
    const failure = () => failResult('native', 'UI_CHANGED', hidden().message, 'none', {
      operation_recovery: { native_input_failure: 'render_widget_hidden' }, resumes: [],
    })
    const op: BossOperation<{}> = { name, async execute(_args, ctx) {
      ctx.recoveryContinuation!.resume = async restored => {
        resumed++; assert.equal(restored.recoverBrowserTarget, true); return failure()
      }
      return failure()
    } }
    await withLocalRecovery(op, { env: {}, sleep: async () => {} }).execute({}, { signal: new AbortController().signal, progress() {} })
    assert.equal(resumed, name === 'boss_resume_next' ? 2 : 0)
  }
})

test('partial read with historical input and a certified current zero-input refusal can resume from a healthy page alone', async () => {
  const payload = { image: 'already-captured' }
  const target = '11111111-1111-4111-8111-111111111111'
  const initial = okResult('batch', 'partial', 'none', { resumes: [payload], browser_target_ref: target,
    operation_recovery: { schema_version: 1, operation_class: 'readonly', native_input_failure: 'render_widget_hidden',
      operation: { name: 'boss_resume_batch', goal: '读取剩余数量', expected: '资料已读取并关闭', result_kind: 'business_result' },
      execution: { stage: 'input', input_status: 'attempted', attempts_used: 1, ordinary_retries_used: 0 },
      verification: { status: 'inconclusive', evidence: [] },
      error: { code: 'UI_CHANGED', source: 'input', summary: hidden().message } } })
  let resumes = 0, observations = 0
  const op: BossOperation<{}> = { name: 'boss_resume_batch', async execute(_args, ctx) {
    ctx.recoveryContinuation!.resume = async () => ++resumes <= 2 ? initial : okResult('batch', 'done', 'none', { resumes: [payload, { image: 'next' }] })
    return initial
  } }
  const result = await withLocalRecovery(op, { sleep: async () => {},
    env: { AIDWORK_RUNTIME_SERVER: 'https://fixture.invalid', AIDWORK_RUNTIME_DEVICE_TOKEN: 'test-only' },
    inspect: { name: 'boss_overlay_inspect', async execute(args) {
      observations++; assert.equal(args.observation_mode, 'page')
      assert.equal((args.visual_context as Record<string, unknown>).native_input_failure, undefined)
      return okResult('observe', '', 'none', { visual: { http_status: 200, state: 'completed', diagnosis: { obstruction: 'absent', goal_state: 'satisfied' } } })
    } },
  }).execute({}, { signal: new AbortController().signal, progress() {} })
  assert.equal(result.success, true)
  assert.equal((result.data.resumes as unknown[])[0], payload)
  assert.equal((result.data.resumes as unknown[]).length, 2)
  assert.equal(resumes, 3)
  assert.equal(observations, 1, '失败window分支不能成为正常网页的额外门禁')
})

for (const scale of [1.25, 1.5, 2]) test(`readonly card fallback reconnects and uses fresh same-card CSS coordinates at ${scale} scale`, async () => {
  let physical = 0, browsed = 0, sessions = 0, detail = false, reads = 0
  const originalRead = ResumeReader.prototype.readResume
  ResumeReader.prototype.readResume = async () => { reads++; return {
    segments: 1, bottomReached: true, width: 760, height: 2400, imageBuffer: Buffer.from('fixture'), seamMis: [], suspectSeams: [],
  } }
  const session: BossSession = {
    snapshot: async () => {
      if (detail) return detailSnap({ name: '测试甲', state: 'greet', canvasNameOnly: true })
      const snap = loadedSnapshot([{ name: '测试甲', y: physical >= 2 ? 190 : 160, baseInfo: '30岁本科' }])
      snap.strings.push('筛选')
      snap.documents[0]!.layout.clientRects = snap.documents[0]!.layout.bounds.map(b => b.map((v, i) => i < 2 ? 0 : v / scale))
      return snap
    },
    click: async () => { physical++; throw hidden() },
    clickBrowse: async point => { browsed++; assert.deepEqual(point, { x: 320 / scale, y: 265 / scale }); detail = true },
    pressEscape: async () => { detail = false },
    clickAndType: async () => { assert.fail('readonly fallback must never type') }, mouseWheel: async () => {},
    captureFullpage: async () => Buffer.from('unused'), getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
  }
  try {
    const operation = createBossResumeBatchOperation(async () => { sessions++; return session })
    const result = await withLocalRecovery(operation, { env: {}, sleep: async () => {} }).execute({ limit: 1 },
      { signal: new AbortController().signal, progress() {} })
    assert.equal(result.success, true, result.message)
    assert.equal((result.data.resumes as unknown[]).length, 1)
    assert.equal(result.data.attempted, 1)
    assert.deepEqual(result.data.failures, [])
    assert.equal(sessions, 3)
    assert.equal(physical, 2)
    assert.equal(browsed, 1)
    assert.equal(reads, 1)
  } finally { ResumeReader.prototype.readResume = originalRead }
})

test('cancellation reaches native discovery without allowing another physical input', async () => {
  const controller = new AbortController()
  const clicker = new WinMouseClicker({ signal: controller.signal, nativeOpener: async () => ({ perform: async (_request, signal) => {
    assert.equal(signal, controller.signal)
    controller.abort()
    throw Object.assign(new Error('STALE_OBSERVATION: cancelled before input'), { name: 'NativeInputError', exitCode: 2 })
  } }) })
  await assert.rejects(clicker.click({ x: 1, y: 1 }, { width: 10, height: 10 }), CancelledError)
})

test('window capture diagnostics preserve safe stage/code without copying arbitrary exception text', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-window-failure-'))
  const secret = 'Bearer must-not-be-in-result'
  const session = { getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', getTargetId: async () => 'fixture',
    activateTarget: async () => {}, close: async () => {} } as unknown as BossSession
  try {
    const op = createBossOverlayInspectOperation(async () => session, { root,
      captureWindow: async () => { throw Object.assign(new Error(secret), { code: 'ENOENT' }) } })
    const result = await op.execute({ request_id: '11111111-1111-4111-8111-111111111111', observation_mode: 'window',
      visual_context: { purpose: 'diagnose', available_actions: ['dismiss_click'],
        operation: { name: 'boss_resume_next', result_kind: 'page_state', goal: '读取', expected: '详情可读' },
        execution: { stage: 'input', input_status: 'not_started', attempts_used: 1 } } },
    { signal: new AbortController().signal, progress() {} })
    assert.equal(result.code, 'INTERNAL_ERROR')
    assert.deepEqual(result.data.visual_failure, { stage: 'window_capture', original_code: 'INTERNAL_ERROR', machine_code: 'ENOENT' })
    assert.equal(JSON.stringify(result).includes(secret), false)
  } finally { await rm(root, { recursive: true, force: true }) }
})
