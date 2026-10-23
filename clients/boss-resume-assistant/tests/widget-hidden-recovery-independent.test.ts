import assert from 'node:assert/strict'
import test from 'node:test'
import { nativeWindowInputFailure, WinClickError } from '../src/main/input/WinMouseClicker.js'
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import { failResult, okResult, type BossOperation, type OpContext, type OperationResult } from '../src/main/operations/types.js'
import { ResumeBatchReader, type ResumeBatchCheckpoint, type ResumeBatchDeps } from '../src/main/boss/ResumeBatchReader.js'
import { ResumeReader, type ResumeReadResult } from '../src/main/boss/ResumeReader.js'
import { loadedSnapshot } from './loadedCandidateFixture.js'
import { detailSnap } from './detailGreetFixture.js'
import { createBossResumeBatchOperation } from '../src/main/operations/bossResumeBatch.js'
import type { BossSession } from '../src/main/operations/bossContext.js'

const target = '34d76861-a573-4c24-87cf-f6d4db8d37e3'
const message = 'STALE_OBSERVATION: render widget hidden; input refused'

test('independent: native lifecycle retry evidence cannot be inferred from lookalike text or an unknown physical effect', () => {
  assert.equal(nativeWindowInputFailure(new WinClickError(message, 2)), 'render_widget_hidden')
  for (const error of [new Error(message), new WinClickError(message, 1),
    new WinClickError('[BOSS_INPUT_UNKNOWN] ' + message, 2),
    new WinClickError(message + '; token=synthetic-secret', 2),
    new WinClickError('STALE_OBSERVATION: Cursor or render-widget ownership changed; input refused', 2),
    new WinClickError('STALE_OBSERVATION: debug Chrome process is ambiguous; input refused (matched=2)', 2)]) {
    assert.equal(nativeWindowInputFailure(error), undefined)
  }
})

function failure(effect: 'none' | 'unknown' = 'none'): OperationResult {
  return failResult('synthetic-original', 'UI_CHANGED', message, effect, { browser_target_ref: target,
    operation_recovery: { schema_version: 1, operation_class: 'readonly', browser_target_ref: target,
      operation: { name: 'boss_resume_next', result_kind: 'page_state', goal: '读取原职位候选人', expected: '产生简历资料并关闭详情' },
      execution: { stage: 'input', input_status: effect === 'none' ? 'not_started' : 'unknown', attempts_used: 1, ordinary_retries_used: 0 },
      verification: { status: 'inconclusive', evidence: ['合成零输入窗口故障'] },
      error: { code: 'UI_CHANGED', source: 'input', summary: message },
      native_input_failure: 'render_widget_hidden' }, resumes: [] })
}

test('independent: native recovery is bounded, keeps original target and arguments, and a healthy page need not ask broken window diagnostics', async () => {
  const args = { limit: 2, exclude_names: ['合成已读人'], expected_job_name: '合成职位' }
  const abort = new AbortController(), delays: number[] = [], modes: unknown[] = []
  let executes = 0, resumes = 0
  const operation: BossOperation<typeof args> = { name: 'boss_resume_next', async execute(received, context) {
    executes++
    assert.deepEqual(received, args)
    context.recoveryContinuation!.resume = async fresh => {
      resumes++
      assert.equal(fresh.browserTargetRef, target)
      assert.equal(fresh.signal, abort.signal)
      assert.equal(fresh.recoverBrowserTarget, resumes <= 2 ? true : undefined)
      return resumes < 3 ? failure() : okResult('recovered', '原批次读取完成', 'none', { resumes: ['synthetic-payload'] })
    }
    return failure()
  } }
  const wrapped = withLocalRecovery(operation, {
    env: { AIDWORK_RUNTIME_SERVER: 'https://test.invalid', AIDWORK_RUNTIME_DEVICE_TOKEN: 'synthetic-only' },
    sleep: async ms => { delays.push(ms) },
    inspect: { name: 'boss_overlay_inspect', async execute(value) {
      modes.push(value.observation_mode)
      assert.equal(value.observation_mode, 'page', 'page diagnosis must not be vetoed by unrelated window capture')
      return okResult('diagnostic', '页面正常', 'none', { visual: { http_status: 200, state: 'completed',
        diagnosis: { obstruction: 'absent', goal_state: 'satisfied', proposed_action: null } } })
    } },
    dismiss: { name: 'boss_overlay_dismiss', async execute() { assert.fail('normal page has no dismiss input') } },
  })
  const ctx: OpContext = { signal: abort.signal, progress() {} }
  const result = await wrapped.execute(args, ctx)
  assert.equal(result.success, true)
  assert.equal(executes, 1, 'restart must use invocation-local continuation instead of rebuilding arguments')
  assert.equal(resumes, 3, 'two deterministic retries plus one domain continuation after visual page proof')
  assert.deepEqual(delays, [1000, 2000])
  assert.deepEqual(modes, ['page'])
  assert.deepEqual(result.data.resumes, ['synthetic-payload'])
})

test('independent: cancellation during native backoff never resumes business input or spends visual calls', async () => {
  const abort = new AbortController()
  let resumes = 0
  const operation: BossOperation<Record<string, never>> = { name: 'boss_resume_next', async execute(_args, ctx) {
    ctx.recoveryContinuation!.resume = async () => { resumes++; assert.fail('cancelled continuation executed') }
    return failure()
  } }
  const wrapped = withLocalRecovery(operation, { sleep: async () => { abort.abort() },
    inspect: { name: 'boss_overlay_inspect', async execute() { assert.fail('cancelled call cannot diagnose') } } })
  const result = await wrapped.execute({}, { signal: abort.signal, progress() {} })
  assert.equal(resumes, 0)
  assert.equal((result.data.visual_recovery as any).state, 'cancelled')
})

test('independent: even a lifecycle marker cannot cause deterministic replay of an unknown effect', async () => {
  let resumes = 0, sleeps = 0
  const operation: BossOperation<Record<string, never>> = { name: 'boss_resume_next', async execute(_args, ctx) {
    ctx.recoveryContinuation!.resume = async () => { resumes++; assert.fail('unknown effect automatically replayed') }
    return failure('unknown')
  } }
  const result = await withLocalRecovery(operation, { env: {}, sleep: async () => { sleeps++ } })
    .execute({}, { signal: new AbortController().signal, progress() {} })
  assert.equal(result.effect, 'unknown')
  assert.equal(resumes, 0)
  assert.equal(sleeps, 0)
})

test('independent: native no-progress allowance resets only after a newly retained resume payload', async () => {
  const delays: number[] = []
  let resumes = 0
  const payload = { image: 'retained-synthetic' }
  const operation: BossOperation<{}> = { name: 'boss_resume_next', async execute(_args, context) {
    context.recoveryContinuation!.resume = async () => {
      resumes++
      const result = failure()
      result.data.resumes = resumes >= 2 ? [payload] : []
      return result
    }
    return failure()
  } }
  const result = await withLocalRecovery(operation, { env: {}, sleep: async ms => { delays.push(ms) } })
    .execute({}, { signal: new AbortController().signal, progress() {} })
  assert.equal(resumes, 4)
  assert.deepEqual(delays, [1000, 2000, 1000, 2000])
  assert.equal((result.data.resumes as unknown[])[0], payload)
})

function readerWorld() {
  const checkpoint: ResumeBatchCheckpoint = {}
  const state = { detail: '', physical: 0, browsed: 0, wheels: 0, movedIdentity: false }
  const deps: ResumeBatchDeps = { checkpoint,
    snapshot: async () => state.detail ? detailSnap({ name: state.detail, state: 'greet', canvasNameOnly: true })
      : loadedSnapshot([{ name: '合成甲', y: 160, baseInfo: '30岁本科' },
        { name: '合成乙', y: 400, baseInfo: state.movedIdentity ? '40岁博士' : '31岁本科' }]),
    click: async point => { state.physical++; state.detail = point.y < 350 ? '合成甲' : '合成乙' },
    clickBrowse: async point => { state.browsed++; state.detail = point.y < 350 ? '合成甲' : '合成乙' },
    pressEscape: async () => { state.detail = '' },
    wheel: async () => { state.wheels++; throw new WinClickError(message, 2) },
    captureFullpage: async () => { assert.fail('synthetic reader must not capture real page') },
    sameView: async () => true,
    stitch: async () => { assert.fail('synthetic reader must not write images') },
    sleep: async () => {},
  }
  return { deps, state, checkpoint, reader: () => new ResumeBatchReader(deps) }
}

const image = (): ResumeReadResult => ({ segments: 1, bottomReached: true, width: 600, height: 1200,
  imageBuffer: Buffer.from('synthetic-image'), seamMis: [], suspectSeams: [] })

test('independent: inner read wheel refusal preserves prior payload, does not exclude unfinished card, and resumes only remaining work', async () => {
  const h = readerWorld(), prior = ResumeReader.prototype.readResume
  const names: string[] = []
  let failOnce = true
  ResumeReader.prototype.readResume = async function () {
    names.push(h.state.detail)
    if (h.state.detail === '合成乙' && failOnce) {
      failOnce = false
      await Reflect.get(this, 'deps').wheel(-120, 1)
    }
    return image()
  }
  try {
    const first = await h.reader().readBatch({ limit: 2 })
    assert.equal(first.resumes.length, 1)
    assert.deepEqual(first.processed_names, ['合成甲'])
    assert.equal(first.recovery?.nativeInputFailure, 'render_widget_hidden')
    assert.equal(first.recovery?.execution.inputStatus, 'attempted', 'earlier delivered open click remains factual history')
    assert.equal(h.checkpoint.current?.phase, 'read')
    const second = await h.reader().readBatch({ limit: 2 })
    assert.equal(second.resumes.length, 2)
    assert.equal(second.resumes[0], first.resumes[0])
    assert.equal(second.attempted, 2)
    assert.deepEqual(second.failures, [])
    assert.deepEqual(names, ['合成甲', '合成乙', '合成乙'])
    assert.equal(h.state.browsed, 0)
  } finally { ResumeReader.prototype.readResume = prior }
})

test('independent: same-name replacement cannot be clicked by readonly fallback after native refusal', async () => {
  const h = readerWorld()
  h.deps.click = async () => { throw new WinClickError(message, 2) }
  h.checkpoint.processedNames = new Set(['合成甲'])
  const first = await h.reader().readBatch({ limit: 1 })
  assert.equal(first.recovery?.nativeInputFailure, 'render_widget_hidden')
  h.checkpoint.current!.nativeWindowRefusals = 2
  h.state.movedIdentity = true
  const second = await h.reader().readBatch({ limit: 1 })
  assert.equal(second.resumes.length, 0)
  assert.equal(h.state.browsed, 0)
  assert.match(second.recovery?.summary ?? '', /卡片.*变化|同名替代/)
})

test('independent: closing a residual detail must retain the unfinished card reference before considering same-name replacement', async () => {
  const h = readerWorld(), prior = ResumeReader.prototype.readResume
  ResumeReader.prototype.readResume = async function () {
    if (h.state.detail === '合成乙') await Reflect.get(this, 'deps').wheel(-120, 1)
    return image()
  }
  try {
    const first = await h.reader().readBatch({ limit: 2 })
    assert.equal(first.resumes.length, 1)
    assert.equal(h.checkpoint.current?.phase, 'read')
    h.state.movedIdentity = true
    const second = await h.reader().readBatch({ limit: 2 })
    assert.equal(second.resumes.length, 1)
    assert.equal(second.resumes[0], first.resumes[0])
    assert.equal(h.state.physical, 2, 'do not click the replacement after closing old detail')
    assert.equal(h.state.browsed, 0)
    assert.match(second.recovery?.summary ?? '', /卡片.*变化|同名替代/)
  } finally { ResumeReader.prototype.readResume = prior }
})

test('independent: persistent native detail-wheel refusal does not silently replace the wheel with a CDP channel', async () => {
  const h = readerWorld(), prior = ResumeReader.prototype.readResume
  ResumeReader.prototype.readResume = async function () {
    await Reflect.get(this, 'deps').wheel(-120, 1)
    return image()
  }
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      const result = await h.reader().readBatch({ limit: 1 })
      assert.equal(result.resumes.length, 0)
      assert.equal(result.recovery?.nativeInputFailure, 'render_widget_hidden')
      assert.equal(result.stop_reason, 'page_operation_failed')
      assert.deepEqual(result.processed_names, [])
    }
    assert.equal(h.state.wheels, 2, 'both attempts use the existing real wheel boundary')
    assert.equal(h.state.browsed, 0)
  } finally { ResumeReader.prototype.readResume = prior }
})

for (const change of ['account', 'job'] as const) {
  test(`independent: native recovery cannot continue the original batch after ${change} scope changes`, async () => {
    let sessions = 0, clicks = 0
    const session: BossSession = {
      snapshot: async () => {
        const snap = loadedSnapshot([{ name: '合成甲', y: 160, baseInfo: '30岁本科' }])
        const root = snap.documents[0]!, index = (root.nodes.nodeValue as number[]).length
        const filter = snap.strings.push('筛选') - 1, text = snap.strings.push('#text') - 1
        ;(root.nodes.nodeValue as number[]).push(filter)
        ;(root.nodes.nodeName as number[]).push(text)
        ;(root.nodes.parentIndex as number[]).push(1)
        ;(root.nodes.attributes as number[][]).push([])
        root.layout.nodeIndex.push(index); root.layout.bounds.push([850, 65, 80, 20])
        root.layout.clientRects!.push([0, 0, 80, 20])
        root.layout.styles!.push([...root.layout.styles![1]!])
        if (sessions > 1) snap.strings = snap.strings.map(value => change === 'account'
          ? value.replace('https://example.com/test-account.png', 'https://example.com/other-account.png')
          : value.replace('PHP 开发工程师 _ 上海 15-30K', '其他合成职位 _ 上海 15-30K'))
        return snap
      },
      getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
      click: async () => { clicks++; throw new WinClickError(message, 2) },
      clickBrowse: async () => { assert.fail('scope changed; cannot browse replacement') },
      pressEscape: async () => { assert.fail('scope changed; cannot press keys') },
      clickAndType: async () => { assert.fail('readonly cannot type') },
      mouseWheel: async () => { assert.fail('scope changed; cannot scroll') },
      captureFullpage: async () => { assert.fail('scope changed; cannot capture another candidate') },
    }
    const operation = createBossResumeBatchOperation(async () => { sessions++; return session })
    const result = await withLocalRecovery(operation, { env: {}, sleep: async () => {} })
      .execute({ limit: 1 }, { signal: new AbortController().signal, progress() {} })
    assert.equal(result.success, false)
    assert.equal(result.code, change === 'account' ? 'UI_CHANGED' : 'JOB_CHANGED')
    assert.equal(sessions, 2)
    assert.equal(clicks, 1)
  })
}
