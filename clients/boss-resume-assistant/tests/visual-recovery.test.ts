import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { VisualRecovery, validateVisualArgs, type VisualContext } from '../src/main/boss/visualRecovery.js'
import { VisualDiagnoseError, visualDiagnose } from '../src/main/boss/visualDiagnoseClient.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { execPowerShellScript } from '../src/main/input/powerShell.js'
import type { WindowObservation } from '../src/main/input/windowExperiment.js'
import { pngMetadata } from '../src/main/operations/bossScreenshot.js'
import { CodedOperationError } from '../src/main/operations/types.js'
import { createBossOverlayDismissOperation } from '../src/main/operations/bossOverlayDismiss.js'
import { fileURLToPath } from 'node:url'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
const png40 = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAYAAACM/rhtAAAAWElEQVR4nM3OQQEAIACEMKR/5zOCT1mCnW0jTOIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkzt+Blws+6gRMEVPP7QAAAABJRU5ErkJggg==', 'base64')
const id = '0c5a306b-8659-4000-8000-123456789012'
const context: VisualContext = { purpose: 'diagnose', operation: { name: 'boss_open_detail', goal: '恢复推荐页面并继续打开详情', expected: '右侧经历概览和打招呼可见', result_kind: 'page_state' },
  execution: { stage: 'input', input_status: 'not_started', attempts_used: 1, ordinary_retries_used: 0 }, error: { code: 'UI_CHANGED', source: 'cli', summary: '输入守卫拒绝' },
  verification: { status: 'inconclusive', evidence: [] }, available_actions: ['dismiss_click'] }
const controller = new AbortController()
const signal = controller.signal
function session() {
  let captures = 0, clicks = 0, currentUrl = 'https://www.zhipin.com/web/chat/recommend'
  return { session: { getUrl: async () => currentUrl, captureFullpage: async () => { captures++; return png }, clickBrowse: async () => { clicks++ } } as unknown as BossSession,
    get captures() { return captures }, get clicks() { return clicks }, changeUrl(url: string) { currentUrl = url } }
}
function response(extra: Record<string, unknown> = {}) {
  return { status: 200, body: { api_version: 2, request_id: id, state: 'completed', diagnosis: { page_summary: '弹窗挡住了推荐页面', goal_state: 'not_satisfied', obstruction: 'present', reason: '关闭可恢复', missing_evidence: [],
    proposed_action: { kind: 'dismiss', space: 'page_image_px', point: { x: 0.5, y: 0.5 }, target_box: { x: 0, y: 0, width: 1, height: 1 }, target_label: '关闭' } }, receipt: { usage_factor: 20 }, ...extra } }
}

test('visual recovery uses the saved backend suggestion once and leaves success verification to Runner', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = session(); let requests = 0, comparisons = 0
    const recovery = new VisualRecovery({ root, settleMs: 0, diagnose: async opts => { requests++; assert.deepEqual(opts.imageBytes, png); assert.equal(opts.context, context); return response() }, compare: async (_a, _b, region) => { comparisons++; assert.deepEqual(region, { x: 0, y: 0, width: 1, height: 1 }); return true } })
    const result = await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
    assert.equal(typeof result.recovery_ref, 'string')
    const cached = await recovery.inspect({ request_id: id, retrieve_only: true }, s.session, signal)
    assert.equal(cached.recovery_ref, result.recovery_ref)
    assert.equal(requests, 1); assert.equal(s.captures, 1)
    const dismissed = await recovery.dismiss(String(result.recovery_ref), s.session, signal)
    assert.equal(dismissed.input_status, 'attempted'); assert.equal('dismissed' in dismissed, false)
    assert.equal(s.clicks, 1); assert.equal(comparisons, 1)
    await assert.rejects(recovery.dismiss(String(result.recovery_ref), s.session, signal), /已消费/)
    assert.equal(s.clicks, 1)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('unknown receipts retrieve with the same request identity without another screenshot or model dispatch', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = session(); let calls = 0
    const recovery = new VisualRecovery({ root, diagnose: async opts => {
      calls++
      if (calls === 1) throw new VisualDiagnoseError('unknown', id, undefined, true)
      assert.equal(opts.retrieveOnly, true); assert.equal(opts.requestId, id); assert.equal(opts.imageBytes, undefined)
      return response()
    } })
    const first = await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
    assert.equal((first.visual as Record<string, unknown>).state, 'unknown')
    const second = await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
    assert.equal(typeof second.recovery_ref, 'string'); assert.equal(s.captures, 1); assert.equal(calls, 2)
    await assert.rejects(recovery.inspect({ request_id: id, visual_context: { ...context, error: null } }, s.session, signal), /上下文不能改变/)
    assert.equal(calls, 2)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('invalid actions preserve a useful page assessment and never create an executable reference', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = session()
    const recovery = new VisualRecovery({ root, diagnose: async () => response({ action_error: 'INVALID_MODEL_ACTION' }) })
    const result = await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
    assert.equal(result.recovery_ref, undefined)
    assert.equal(((result.visual as Record<string, unknown>).diagnosis as Record<string, unknown>).obstruction, 'present')
    assert.equal(s.clicks, 0)
    assert.equal(existsSync(join(root, id, 'page.png')), false)
    const cached = await recovery.inspect({ request_id: id, retrieve_only: true }, s.session, signal)
    assert.equal(((cached.visual as Record<string, unknown>).diagnosis as Record<string, unknown>).obstruction, 'present')
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('stale local target, changed page, expiry and cancellation all prevent a click', async () => {
  for (const failure of ['local', 'url', 'expired', 'cancelled', 'unknown-input']) {
    const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
    try {
      let now = Date.now(); const s = session()
      const recovery = new VisualRecovery({ root, now: () => now, diagnose: async () => response(), compare: async () => failure !== 'local' })
      const result = await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
      if (failure === 'url') s.changeUrl('https://www.zhipin.com/web/chat/recommend?other=1')
      if (failure === 'expired') now += 120_001
      if (failure === 'unknown-input') s.session.clickBrowse = async () => { throw new Error('CDP disconnected after press') }
      const abort = new AbortController(); if (failure === 'cancelled') abort.abort()
      await assert.rejects(recovery.dismiss(String(result.recovery_ref), s.session, abort.signal))
      if (failure === 'unknown-input') await assert.rejects(recovery.dismiss(String(result.recovery_ref), s.session, signal), /已消费/)
      assert.equal(s.clicks, 0)
    } finally { await rm(root, { recursive: true, force: true }) }
  }
})

test('shared visual primitives accept other BOSS operations without deciding their replay policy', () => {
  for (const name of ['boss_open_chat', 'boss_greet', 'boss_accept_resume']) {
    for (const result_kind of ['page_state', 'business_result']) {
      assert.equal(validateVisualArgs({ request_id: id, visual_context: { ...context, operation: { name, result_kind }, available_actions: ['dismiss_click', 'press_escape'] } }), null)
    }
  }
  for (const c of [{ ...context, purpose: 'verify' }, { ...context, available_actions: ['send_message'] }, { ...context, available_actions: ['press_escape', 'press_escape'] }, { ...context, operation: { name: 'boss_greet;rm', result_kind: 'page_state' } }, { ...context, operation: { name: 'boss_filter', result_kind: 'unknown' } }]) {
    assert.notEqual(validateVisualArgs({ request_id: id, visual_context: c }), null)
  }
  assert.equal(validateVisualArgs({ request_id: id, visual_context: { ...context, purpose: 'verify', available_actions: [] } }), null)
})

test('in-memory observations use the same backend-only v2 transport without leaking local context fields', async () => {
  const capturedAt = new Date().toISOString()
  await visualDiagnose({ requestId: id, apiVersion: 2, imageBytes: png, context, capturedAt }, { env: { AID_BOSS_SERVER_URL: 'http://127.0.0.1:9999', AID_BOSS_SERVER_TOKEN: 'secret' }, fetch: (async (_url, init) => {
    const body = JSON.parse(String(init?.body))
    assert.equal(body.observation.captured_at, capturedAt); assert.equal(body.observation.after_attempt, 1)
    assert.equal(body.observation.space, 'page_image_px'); assert.deepEqual(body.context, { mode: 'standalone' })
    assert.equal(body.billing_factor, undefined); return Response.json(response().body)
  }) as typeof fetch })
})

test('parallel consumers cannot issue the same visual close click twice', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = session(), recovery = new VisualRecovery({ root, settleMs: 0, diagnose: async () => response(), compare: async () => true })
    const result = await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
    const attempts = await Promise.allSettled([recovery.dismiss(String(result.recovery_ref), s.session, signal), recovery.dismiss(String(result.recovery_ref), s.session, signal)])
    assert.equal(attempts.filter(x => x.status === 'fulfilled').length, 1)
    assert.equal(s.clicks, 1)
    const receipt = await recovery.inspect({ request_id: id, retrieve_only: true }, s.session, signal)
    assert.equal((receipt.visual as Record<string, unknown>).state, 'completed')
    assert.equal(receipt.recovery_action_error, 'ALREADY_CONSUMED')
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('real image comparison ignores unrelated caret pixels and rejects changes at the close target', { skip: process.platform !== 'win32', timeout: 30_000 }, async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-region-'))
  try {
    const images = [
      'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAYAAACM/rhtAAAAWElEQVR4nM3OQQEAIACEMKR/5zOCT1mCnW0jTOIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkTuIkzt+Blws+6gRMEVPP7QAAAABJRU5ErkJggg==',
      'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAYAAACM/rhtAAAAW0lEQVR4nM3OQQHAQBCEMBb/nucc9FuiILdthEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxEmcxPl34Mvd8QA+8wRPiS9ACgAAAABJRU5ErkJggg==',
      'iVBORw0KGgoAAAANSUhEUgAAACgAAAAoCAYAAACM/rhtAAAAZElEQVR4nO3XsQ3AMAzEQJr77+wgMygFgegmeFCVzr33EiZxEidxEidxEidxEidxEidxEidxEidxEidxEidxEid/G3jOaQ+8H/9g/zvx13bg1Bac2oJTW3BqC05twaktOLUFGXjfhwcl4wdPtabGoQAAAABJRU5ErkJggg==',
    ]
    const files = images.map((_, i) => join(root, `${i}.png`))
    await Promise.all(files.map((file, i) => writeFile(file, Buffer.from(images[i]!, 'base64'))))
    const script = fileURLToPath(new URL('../../scripts/visual-region-compare.ps1', import.meta.url))
    for (const [index, expected] of [[1, true], [2, false]] as const) {
      const result = await execPowerShellScript(script, ['-Original', files[0]!, '-Fresh', files[index]!, '-X', '4', '-Y', '4', '-Width', '32', '-Height', '32'], { timeout: 10_000 })
      assert.equal(JSON.parse(result.stdout).equal, expected)
    }
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('a confirmed dead CLI owner cannot block receipt retrieval or permit a consumed input to replay', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = session(); let calls = 0
    const recovery = new VisualRecovery({ root, settleMs: 0, compare: async () => true, diagnose: async opts => {
      calls++; if (calls === 1) throw new VisualDiagnoseError('unknown', id, undefined, true)
      assert.equal(opts.retrieveOnly, true); return response()
    } })
    await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
    const child = spawnSync(process.execPath, ['-e', ''], { windowsHide: true })
    assert.equal(child.status, 0)
    await writeFile(join(root, id, 'lock'), JSON.stringify({ pid: child.pid, token: 'dead-owner' }))
    const result = await recovery.inspect({ request_id: id, retrieve_only: true }, s.session, signal)
    assert.equal(typeof result.recovery_ref, 'string'); assert.equal(calls, 2); assert.equal(s.captures, 1)
    await recovery.dismiss(String(result.recovery_ref), s.session, signal)
    await writeFile(join(root, id, 'lock'), JSON.stringify({ pid: child.pid, token: 'dead-owner' }))
    await assert.rejects(recovery.dismiss(String(result.recovery_ref), s.session, signal), /已消费/)
    assert.equal(s.clicks, 1)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('expired unknown screenshots are cleaned within the owned cache while immutable receipts remain retrievable', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = session(); let now = Date.now(), calls = 0
    const recovery = new VisualRecovery({ root, now: () => now, diagnose: async opts => {
      calls++
      if (calls === 1) return { status: 202, body: { api_version: 2, request_id: id, state: 'unknown', diagnosis: null } }
      return { status: 200, body: { ...response().body, request_id: opts.requestId, diagnosis: { ...(response().body.diagnosis), proposed_action: null } } }
    } })
    const sentinel = join(root, 'unrelated.png'); await writeFile(sentinel, png)
    await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
    assert.equal(existsSync(join(root, id, 'page.png')), true)
    now += 120_001
    await recovery.inspect({ request_id: '1c5a306b-8659-4000-8000-123456789012', visual_context: context }, s.session, signal)
    assert.equal(existsSync(join(root, id, 'page.png')), false); assert.equal(existsSync(sentinel), true)
    const retrieved = await recovery.inspect({ request_id: id, retrieve_only: true }, s.session, signal)
    assert.equal((retrieved.visual as Record<string, unknown>).state, 'completed')
    assert.equal(retrieved.recovery_action_error, 'STALE_OBSERVATION')
    assert.equal(((retrieved.visual as Record<string, unknown>).diagnosis as Record<string, unknown>).obstruction, 'present')
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('DPR 2 image coordinates map to CDP CSS coordinates and changed targets or CSS geometry prevent input', async () => {
  for (const change of ['none', 'target', 'metrics']) {
    const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
    try {
      const s = session(); let target = 'attached-boss-target', width = 20
      s.session.getTargetId = async () => target
      s.session.getPageMetrics = async () => ({ width, height: 20 })
      s.session.captureFullpage = async () => png40
      let input: unknown
      s.session.clickBrowse = async point => { input = point }
      const recovery = new VisualRecovery({ root, settleMs: 0, compare: async () => true, diagnose: async () => response({ diagnosis: { ...response().body.diagnosis, proposed_action: { kind: 'dismiss', space: 'page_image_px', point: { x: 20, y: 20 }, target_box: { x: 18, y: 18, width: 4, height: 4 }, target_label: '关闭' } } }) })
      const result = await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
      if (change === 'target') target = 'different-tab-with-the-same-url'
      if (change === 'metrics') width = 40
      if (change === 'none') {
        const dismissed = await recovery.dismiss(String(result.recovery_ref), s.session, signal)
        assert.deepEqual(input, { x: 10, y: 10 })
        assert.equal(dismissed.input_space, 'cdp_css_px')
      } else {
        await assert.rejects(recovery.dismiss(String(result.recovery_ref), s.session, signal), /标签页或 CSS 视口已改变/)
        assert.equal(input, undefined)
      }
    } finally { await rm(root, { recursive: true, force: true }) }
  }
})


test('page and window Escape suggestions preserve diagnosis but never create an executable reference', async () => {
  for (const mode of ['page', 'window'] as const) {
    const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
    try {
      const s = mode === 'window' ? windowSession() : session(); let escapes = 0, nativeClicks = 0
      s.session.pressEscape = async () => { escapes++ }
      const escapeContext = { ...context, available_actions: ['dismiss_click', 'press_escape'] }
      const recovery = new VisualRecovery({ root, settleMs: 0, captureWindow: windowCapture(),
        clickWindow: async () => { nativeClicks++; return {} },
        diagnose: async () => response({ diagnosis: { ...response().body.diagnosis, proposed_action: { kind: 'press_escape', target_label: '取消当前阻挡' } } }) })
      const result = await recovery.inspect({ request_id: id, observation_mode: mode, visual_context: escapeContext }, s.session, signal)
      assert.equal(result.recovery_ref, undefined)
      assert.equal(result.recovery_action_error, 'UNSUPPORTED_ACTION')
      assert.equal(((result.visual as Record<string, unknown>).diagnosis as Record<string, unknown>).obstruction, 'present')
      assert.equal(escapes, 0); assert.equal(nativeClicks, 0); assert.equal(s.clicks, 0)
    } finally { await rm(root, { recursive: true, force: true }) }
  }
})

function windowSession() {
  const s = session(); let activated = 0
  s.changeUrl('https://www.zhipin.com/web/chat/index')
  s.session.getTargetId = async () => 'bound-target'
  s.session.activateTarget = async () => { activated++ }
  s.session.cdpPort = 9333
  s.session.getPageMetrics = async () => { throw new Error('JS dialog blocks Page methods') }
  s.session.snapshot = async () => { throw new Error('JS dialog blocks DOM methods') }
  s.session.captureFullpage = async () => { throw new Error('JS dialog blocks page screenshots') }
  return { ...s, session: s.session, get activated() { return activated } }
}
function windowCapture(onCapture?: (observation: WindowObservation, count: number) => void) {
  let count = 0
  return async (output: string, cdpPort = 9222) => {
    const observation: WindowObservation = { ...pngMetadata(png), space: 'window_image_px', output, captured_at: new Date().toISOString(), cdp_port: cdpPort, window: { hwnd: '1234', pid: 100, process_started_at: '2026-10-15T00:00:00Z', left: 10, top: 20, width: 1, height: 1 }, owned_dialogs: [] }
    onCapture?.(observation, ++count)
    await writeFile(output, png)
    await writeFile(`${output}.json`, JSON.stringify(observation))
    return { ...observation, metadata_path: `${output}.json` }
  }
}

test('window fallback activates the bound target and uses the existing native click without blocked Page calls', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = windowSession(); let nativeClicks = 0
    const recovery = new VisualRecovery({ root, settleMs: 0, captureWindow: windowCapture(() => assert.ok(s.activated > 0)), diagnose: async opts => {
      assert.equal(opts.windowObservation?.space, 'window_image_px')
      assert.equal(opts.windowObservation?.cdp_port, 9333)
      assert.equal(opts.capturedAt, opts.windowObservation?.captured_at)
      return response({ diagnosis: { ...response().body.diagnosis, proposed_action: { ...response().body.diagnosis.proposed_action, space: 'window_image_px' } } })
    }, clickWindow: async (file, x, y) => {
      nativeClicks++; assert.equal(x, 0); assert.equal(y, 0)
      assert.equal(JSON.parse(await readFile(file, 'utf8')).window.hwnd, '1234')
      return { clicked: true }
    } })
    const result = await recovery.inspect({ request_id: id, observation_mode: 'window', visual_context: context }, s.session, signal)
    assert.deepEqual(result.observation, { mode: 'window', space: 'window_image_px', width: 1, height: 1 })
    await assert.rejects(recovery.inspect({ request_id: id, observation_mode: 'page', retrieve_only: true }, s.session, signal), /observation_mode 不能改变/)
    const cached = await recovery.inspect({ request_id: id, retrieve_only: true }, s.session, signal)
    assert.equal(cached.recovery_ref, result.recovery_ref)
    const result2 = await recovery.dismiss(String(result.recovery_ref), s.session, signal)
    assert.equal(result2.input_status, 'attempted'); assert.equal(result2.space, 'window_image_px')
    assert.equal(s.activated, 2); assert.equal(nativeClicks, 1)
    assert.equal(existsSync(join(root, id, 'page.png.json')), false)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('window sidecar metadata alone determines API image space and time; native handles stay local', async () => {
  const output = 'private-observation.png'
  const observation = { ...(await windowCapture()(join(await mkdtemp(join(tmpdir(), 'boss-api-window-')), output), 9222)) }
  try {
    await visualDiagnose({ requestId: id, apiVersion: 2, imageBytes: png, context, windowObservation: observation }, { env: { AID_BOSS_SERVER_URL: 'http://127.0.0.1:9999', AID_BOSS_SERVER_TOKEN: 'secret' }, fetch: (async (_url, init) => {
      const body = JSON.parse(String(init?.body))
      assert.equal(body.observation.space, 'window_image_px'); assert.equal(body.observation.captured_at, observation.captured_at)
      assert.equal(body.observation.image_base64, png.toString('base64'))
      assert.equal(body.observation.hwnd, undefined); assert.equal(body.observation.window, undefined); assert.equal(body.observation.output, undefined)
      return Response.json(response().body)
    }) as typeof fetch })
    await assert.rejects(visualDiagnose({ requestId: id, apiVersion: 2, imageBytes: png, context, windowObservation: { ...observation, sha256: '0'.repeat(64) } }, { env: { AID_BOSS_SERVER_URL: 'http://127.0.0.1:9999', AID_BOSS_SERVER_TOKEN: 'secret' } }), /不是同一张原图/)
  } finally { await rm(join(observation.output, '..'), { recursive: true, force: true }) }
})

test('window identity, creation time, geometry and native zero-input guards report machine-readable stale state', async () => {
  for (const change of ['hwnd', 'pid', 'process_started_at', 'left', 'width', 'native']) {
    const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
    try {
      const s = windowSession(); let nativeCalls = 0
      const recovery = new VisualRecovery({ root, settleMs: 0, captureWindow: windowCapture((o, count) => {
        if (count > 1 && change !== 'native') (o.window as unknown as Record<string, unknown>)[change] = change === 'process_started_at' ? '2026-10-16T00:00:00Z' : change === 'hwnd' ? '999' : 999
      }), diagnose: async () => response({ diagnosis: { ...response().body.diagnosis, proposed_action: { ...response().body.diagnosis.proposed_action, space: 'window_image_px' } } }), clickWindow: async () => { nativeCalls++; throw new Error('[BOSS_WINDOW_REFUSED] stale geometry; input refused') } })
      const result = await recovery.inspect({ request_id: id, observation_mode: 'window', visual_context: context }, s.session, signal)
      await assert.rejects(recovery.dismiss(String(result.recovery_ref), s.session, signal), (error: unknown) => error instanceof CodedOperationError && error.code === 'UI_CHANGED' && error.data?.recovery_action_error === 'STALE_OBSERVATION' && error.data.input_status === 'not_started')
      assert.equal(nativeCalls, change === 'native' ? 1 : 0)
    } finally { await rm(root, { recursive: true, force: true }) }
  }
})

test('unknown window input remains consumed and exposes unknown facts through the public operation result', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = windowSession(); let inputs = 0
    const deps = { root, settleMs: 0, captureWindow: windowCapture(), diagnose: async () => response({ diagnosis: { ...response().body.diagnosis, proposed_action: { ...response().body.diagnosis.proposed_action, space: 'window_image_px' } } }), clickWindow: async () => { inputs++; throw new Error('[BOSS_INPUT_UNKNOWN] partial click') } }
    const recovery = new VisualRecovery(deps)
    const inspected = await recovery.inspect({ request_id: id, observation_mode: 'window', visual_context: context }, s.session, signal)
    const op = createBossOverlayDismissOperation(async () => ({ ...s.session, close: async () => {} }), deps)
    const result = await op.execute({ recovery_ref: String(inspected.recovery_ref) }, { signal, progress: () => {} })
    assert.equal(result.success, false); assert.equal(result.code, 'EXECUTION_UNKNOWN')
    assert.equal(result.data.input_status, 'unknown'); assert.equal(result.data.recovery_action_error, 'INPUT_UNKNOWN')
    await assert.rejects(recovery.dismiss(String(inspected.recovery_ref), s.session, signal), /已消费/)
    assert.equal(inputs, 1)
  } finally { await rm(root, { recursive: true, force: true }) }
})


test('cached observation and action references cannot be mixed across opaque browser bindings', async () => {
  const root = await mkdtemp(join(tmpdir(), 'boss-recovery-'))
  try {
    const s = session(); s.session.browserTargetRef = '00000000-0000-4000-8000-000000000001'
    const recovery = new VisualRecovery({ root, settleMs: 0, diagnose: async () => response(), compare: async () => true })
    const result = await recovery.inspect({ request_id: id, visual_context: context }, s.session, signal)
    s.session.browserTargetRef = '00000000-0000-4000-8000-000000000002'
    const stale = (error: unknown) => error instanceof CodedOperationError && error.data?.recovery_action_error === 'STALE_OBSERVATION' && error.data.input_status === 'not_started'
    await assert.rejects(recovery.inspect({ request_id: id, retrieve_only: true }, s.session, signal), stale)
    await assert.rejects(recovery.dismiss(String(result.recovery_ref), s.session, signal), stale)
    assert.equal(s.clicks, 0)
    s.session.browserTargetRef = '00000000-0000-4000-8000-000000000001'
    const cached = await recovery.inspect({ request_id: id, retrieve_only: true }, s.session, signal)
    assert.equal(cached.recovery_ref, result.recovery_ref)
    await recovery.dismiss(String(result.recovery_ref), s.session, signal)
    assert.equal(s.clicks, 1)
  } finally { await rm(root, { recursive: true, force: true }) }
})
