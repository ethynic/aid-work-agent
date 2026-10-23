import assert from 'node:assert/strict'
import test from 'node:test'
import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { captureWindow, WindowCaptureError, type WindowScriptRunner } from '../src/main/input/windowExperiment.js'
import { VisualRecovery, type VisualContext } from '../src/main/boss/visualRecovery.js'
import { pngMetadata } from '../src/main/operations/bossScreenshot.js'
import type { BossSession } from '../src/main/operations/bossContext.js'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
const win = { hwnd: '123', pid: 9, process_started_at: '2026-10-23T00:00:00Z', left: 0, top: 0, width: 1, height: 1 }
const geometry = { window: [-100, 0, 100, 100], screen: [0, 0, 1920, 1080], work: [0, 0, 1920, 1040], dpi: 144 }
const outside = () => new Error('[BOSS_WINDOW_DIAGNOSTIC]' + JSON.stringify({ reason: 'window_outside_screen', window: win, geometry, secret: 'must not escape' }))
async function fixture() { const root = await mkdtemp(join(tmpdir(), 'boss-containment-')); return { root, output: join(root, 'window.png') } }
function success() { return { stdout: JSON.stringify({ captured_at: new Date().toISOString(), window: win, owned_dialogs: [], repositioned: true, geometry }), stderr: '' } }

test('one original invocation budget permits one confirmed out-of-screen reposition across multiple captures', async () => {
  const f = await fixture(), budget = { repositionsUsed: 0 }; let calls = 0
  try {
    const run: WindowScriptRunner = async (_script, args) => {
      calls++
      if (!args.includes('-AllowReposition')) throw outside()
      assert.deepEqual(JSON.parse(args[args.indexOf('-ExpectedWindow') + 1]!), win)
      await writeFile(f.output, png); return success()
    }
    const observation = await captureWindow(f.output, 9222, run, { budget })
    assert.equal(observation.space, 'window_image_px'); assert.equal(observation.repositioned, true)
    assert.equal(budget.repositionsUsed, 1); assert.equal(calls, 2)
    await assert.rejects(captureWindow(f.output, 9222, run, { budget }), (error: unknown) => error instanceof WindowCaptureError && error.reason === 'window_outside_screen' && !error.message.includes('secret'))
    assert.equal(calls, 3, 'a later observation cannot reset the invocation budget')
  } finally { await rm(f.root, { recursive: true, force: true }) }
})

test('already-visible capture never authorizes a window move; malformed geometry never authorizes a move', async () => {
  const f = await fixture()
  try {
    const budget = { repositionsUsed: 0 }; let calls = 0
    await captureWindow(f.output, 9222, async (_s, args) => {
      calls++; assert.ok(!args.includes('-AllowReposition')); await writeFile(f.output, png); return success()
    }, { budget })
    assert.equal(calls, 1); assert.equal(budget.repositionsUsed, 0)
    await assert.rejects(captureWindow(f.output, 9222, async () => { calls++; throw new Error('[BOSS_WINDOW_DIAGNOSTIC]' + JSON.stringify({ reason: 'window_outside_screen', window: win, geometry: { ...geometry, dpi: '144' } })) }, { budget }), WindowCaptureError)
    assert.equal(calls, 2); assert.equal(budget.repositionsUsed, 0)
  } finally { await rm(f.root, { recursive: true, force: true }) }
})

test('identity change or failed reposition consumes the budget without a third native attempt', async () => {
  for (const reason of ['window_identity_changed', 'window_outside_screen', 'window_reposition_failed']) {
    const f = await fixture(), budget = { repositionsUsed: 0 }; let calls = 0
    try {
      await assert.rejects(captureWindow(f.output, 9222, async () => {
        calls++; if (calls === 1) throw outside()
        throw new Error('[BOSS_WINDOW_DIAGNOSTIC]' + JSON.stringify({ reason, geometry }))
      }, { budget }), (error: unknown) => error instanceof WindowCaptureError && error.reason === reason)
      assert.equal(calls, 2); assert.equal(budget.repositionsUsed, 1)
    } finally { await rm(f.root, { recursive: true, force: true }) }
  }
})

test('an existing action observation never moves a window during its freshness check', async () => {
  const f = await fixture(), budget = { repositionsUsed: 0 }; let calls = 0
  try {
    await assert.rejects(captureWindow(f.output, 9222, async (_s, args) => {
      calls++; assert.equal(args.includes('-AllowReposition'), false); throw outside()
    }, { budget, allowReposition: false }), WindowCaptureError)
    assert.equal(calls, 1); assert.equal(budget.repositionsUsed, 0)
  } finally { await rm(f.root, { recursive: true, force: true }) }
})

test('cancellation in the reposition invocation consumes the single allowance but cannot return an observation', async () => {
  const f = await fixture(), controller = new AbortController(), budget = { repositionsUsed: 0 }; let calls = 0, marker = ''
  try {
    await assert.rejects(captureWindow(f.output, 9222, async (_s, args) => {
      calls++; if (calls === 1) throw outside()
      assert.ok(args.includes('-AllowReposition')); assert.equal(budget.repositionsUsed, 1)
      marker = args[args.indexOf('-CancelFile') + 1]!
      controller.abort(); assert.ok(existsSync(marker)); return success()
    }, { budget, signal: controller.signal }), /取消/)
    assert.equal(calls, 2); assert.equal(budget.repositionsUsed, 1); assert.equal(existsSync(marker), false)
    assert.equal(existsSync(`${f.output}.json`), false)
  } finally { await rm(f.root, { recursive: true, force: true }) }
})

test('cancellation before capture or during native wait prevents another window action and cleans its marker', async () => {
  const f = await fixture(); let calls = 0
  try {
    const aborted = new AbortController(); aborted.abort()
    await assert.rejects(captureWindow(f.output, 9222, async () => { calls++; return success() }, { signal: aborted.signal }), /取消/)
    assert.equal(calls, 0)
    const controller = new AbortController(), budget = { repositionsUsed: 0 }; let marker = ''
    await assert.rejects(captureWindow(f.output, 9222, async (_script, args) => {
      calls++; marker = args[args.indexOf('-CancelFile') + 1]!
      controller.abort(); assert.equal(existsSync(marker), true)
      throw outside()
    }, { budget, signal: controller.signal }), /取消/)
    assert.equal(calls, 1); assert.equal(budget.repositionsUsed, 0); assert.equal(existsSync(marker), false)
  } finally { await rm(f.root, { recursive: true, force: true }) }
})

test('pure production C# geometry preserves negative monitor coordinates, translates before shrinking, and excludes monitor gaps', { skip: process.platform !== 'win32' }, async () => {
  const sources = await Promise.all(['BossPhysicalInput.cs', 'BossWindowExperiment.cs'].map(name => readFile(fileURLToPath(new URL(`../../scripts/${name}`, import.meta.url)), 'utf8')))
  const source = 'using System.Collections.Generic;\n' + sources[0] + '\n' + sources[1]!.replace(/^using [^;]+;\r?\n/gm, '')
  const command = `$ErrorActionPreference='Stop'
Add-Type -TypeDefinition (Get-Content -LiteralPath $env:AID_TEST_GEOMETRY_SOURCE -Raw -Encoding UTF8)
function New-TestRect($l,$t,$r,$b) { $v=New-Object BossPhysicalInput+Rect; $v.Left=$l; $v.Top=$t; $v.Right=$r; $v.Bottom=$b; return $v }
$work=New-TestRect -1920 0 0 1040
$translated=[BossWindowExperiment]::Contain((New-TestRect -2100 30 -1100 830),$work)
$shrunk=[BossWindowExperiment]::Contain((New-TestRect -2000 -100 100 1600),$work)
$screens=[BossPhysicalInput+Rect[]]@((New-TestRect -1920 0 0 1080),(New-TestRect 0 300 1920 1380))
$before=New-Object BossWindowExperiment+Geometry
$before.Window=New-TestRect -2100 400 900 1000
$before.Work=$work
$before.Screens=$screens
$wide=[BossWindowExperiment]::Placement($before)
@{translated=@($translated.Left,$translated.Top,$translated.Right,$translated.Bottom); shrunk=@($shrunk.Left,$shrunk.Top,$shrunk.Right,$shrunk.Bottom); negative=[BossWindowExperiment]::Covered((New-TestRect -1800 20 -100 1000),$screens); gap=[BossWindowExperiment]::Covered((New-TestRect -100 100 100 400),$screens); spanning=[BossWindowExperiment]::Covered((New-TestRect -100 400 100 800),$screens); wide=@($wide.Left,$wide.Top,$wide.Right,$wide.Bottom)} | ConvertTo-Json -Compress`
  const executable = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  const f = await fixture()
  try {
    const sourcePath = join(f.root, 'geometry.cs'); await writeFile(sourcePath, source)
    const { stdout } = await promisify(execFile)(executable, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(command, 'utf16le').toString('base64')], { windowsHide: true, timeout: 20_000, encoding: 'utf8', env: { ...process.env, AID_TEST_GEOMETRY_SOURCE: sourcePath } })
    assert.deepEqual(JSON.parse(stdout), { translated: [-1920, 30, -920, 830], shrunk: [-1920, 0, 0, 1040], negative: true, gap: false, spanning: true, wide: [-1920, 400, 1080, 1000] })
  } finally { await rm(f.root, { recursive: true, force: true }) }
})

const target = '11111111-1111-4111-8111-111111111111'
const id = '0c5a306b-8659-4000-8000-123456789012'
const context: VisualContext = { purpose: 'diagnose', operation: { name: 'boss_open_detail', goal: '恢复原页面', expected: '可重新定位', result_kind: 'page_state' }, execution: { stage: 'input', input_status: 'not_started', attempts_used: 1, ordinary_retries_used: 0 }, verification: { status: 'inconclusive', evidence: [] }, available_actions: ['dismiss_click'] }

test('a reposition invalidates earlier window references even if dimensions/pixels match and adds no extra diagnosis', async () => {
  const f = await fixture(), signal = new AbortController().signal, budget = { repositionsUsed: 0 }; let diagnostics = 0, clicks = 0, captures = 0
  const session = { browserTargetRef: target, cdpPort: 9222, getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', getTargetId: async () => 'target', activateTarget: async () => {} } as unknown as BossSession
  try {
    const recovery = new VisualRecovery({ root: f.root,
      captureWindow: async (output, _port, _run, options) => {
        captures++; if (captures === 2) { assert.equal(options!.budget, budget); options!.budget!.repositionsUsed++ }
        await writeFile(output, png)
        return { output, metadata_path: `${output}.json`, ...pngMetadata(png), space: 'window_image_px', captured_at: new Date().toISOString(), cdp_port: 9222, window: win, owned_dialogs: [] }
      }, diagnose: async opts => { diagnostics++; return { status: 200, body: { api_version: 2, request_id: opts.requestId, state: 'completed', diagnosis: { obstruction: 'present', goal_state: 'not_satisfied', proposed_action: { kind: 'dismiss', space: 'window_image_px', point: { x: 0, y: 0 }, target_box: { x: 0, y: 0, width: 1, height: 1 }, target_label: '关闭' } } } } },
      clickWindow: async () => { clicks++; return {} } })
    const first = await recovery.inspect({ request_id: id, observation_mode: 'window', visual_context: context }, session, signal, budget)
    assert.equal(typeof first.recovery_ref, 'string')
    await recovery.inspect({ request_id: '0c5a306b-8659-4000-8000-123456789013', observation_mode: 'window', visual_context: context }, session, signal, budget)
    await assert.rejects(recovery.dismiss(String(first.recovery_ref), session, signal, budget), /归位后观察已失效/)
    const receipt = await recovery.inspect({ request_id: id, retrieve_only: true }, session, signal, budget)
    assert.equal(receipt.recovery_action_error, 'STALE_OBSERVATION'); assert.equal(receipt.recovery_ref, undefined)
    assert.equal(diagnostics, 2, 'one diagnosis per requested observation, no automatic reposition diagnosis')
    assert.equal(captures, 2); assert.equal(clicks, 0)
  } finally { await rm(f.root, { recursive: true, force: true }) }
})
