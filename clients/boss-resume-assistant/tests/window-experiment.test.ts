import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import test from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { captureWindow, clickWindow, loadWindowObservation, type WindowObservation, type WindowScriptRunner } from '../src/main/input/windowExperiment.js'
import { pngMetadata } from '../src/main/operations/bossScreenshot.js'
import { visualDiagnose } from '../src/main/boss/visualDiagnoseClient.js'
import { parseArgs } from '../src/cli/args.js'
import { validateCommandArgs } from '../src/cli/validate.js'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')
const requestId = '0c5a306b-8659-4000-8000-123456789012'

async function readObservationWithWindowsPowerShell(file: string): Promise<unknown> {
  const source = await readFile(fileURLToPath(new URL('../../scripts/window-experiment.ps1', import.meta.url)))
  assert.deepEqual(source.subarray(0, 3), Buffer.from([0xef, 0xbb, 0xbf]), 'the packaged PS5.1 script must retain its UTF-8 BOM')
  const read = source.toString('utf8').match(/^\s*\$meta = Get-Content[^\r\n]*\| ConvertFrom-Json\s*$/m)?.[0]
  assert.ok(read, 'execute the production observation read pipeline, not a test-side reimplementation')
  const command = `$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) { throw 'Expected Windows PowerShell 5.1' }
$Observation=$env:AID_BOSS_TEST_WINDOW_OBSERVATION
${read}
$meta | ConvertTo-Json -Depth 10 -Compress`
  const executable = join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  const { stdout } = await promisify(execFile)(executable, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(command, 'utf16le').toString('base64')],
    { windowsHide: true, timeout: 15_000, encoding: 'utf8', env: { ...process.env, AID_BOSS_TEST_WINDOW_OBSERVATION: file } })
  return JSON.parse(stdout)
}

test('native click distinguishes cursor movement from ownership changes before mouse down without logging page text', async () => {
  const script = await readFile(fileURLToPath(new URL('../../scripts/BossWindowExperiment.cs', import.meta.url)), 'utf8')
  const diagnostic = script.slice(script.indexOf('private static string WindowDiagnostic'), script.indexOf('public static void Click'))
  for (const field of ['hwnd=', 'class=', 'pid=', 'root=', 'rootowner=', 'expected_screen=', 'actual_cursor=', 'hit=', 'browser=', 'foreground=']) assert.ok(diagnostic.includes(field), field)
  assert.equal(diagnostic.includes('Title('), false)
  const cursorGuard = script.indexOf('throw ClickGuardFailure("cursor_changed"')
  const ownershipGuard = script.indexOf('throw ClickGuardFailure("ownership_changed"')
  const inputStarted = script.indexOf('scope.MarkInputStarted()')
  assert.ok(cursorGuard > 0 && ownershipGuard > cursorGuard && inputStarted > ownershipGuard)
  assert.match(script.slice(inputStarted), /finally \{ mouse_event\(4,/)
})

async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), 'boss-window-experiment-'))
  const output = join(dir, 'window.png')
  const file = `${output}.json`
  const observation: WindowObservation = { ...pngMetadata(png), space: 'window_image_px', output, cdp_port: 9222,
    captured_at: new Date().toISOString(), window: { hwnd: '12345', pid: 300, left: -1200, top: 0, width: 1, height: 1 }, owned_dialogs: [] }
  await writeFile(output, png)
  await writeFile(file, JSON.stringify(observation))
  return { dir, output, file, observation }
}

test('window capture invokes only packaged native script, preserves image and writes coordinate provenance sidecar', async () => {
  const item = await fixture()
  try {
    let calls = 0
    const data = await captureWindow(item.output, 9222, async (script, args) => {
      calls++
      assert.match(script, /window-experiment\.ps1$/)
      assert.deepEqual(args, ['-Action', 'capture', '-CdpPort', '9222', '-Output', item.output])
      return { stdout: JSON.stringify({ captured_at: item.observation.captured_at, window: item.observation.window, owned_dialogs: [] }), stderr: '' }
    })
    assert.equal(calls, 1)
    assert.equal(data.metadata_path, item.file)
    assert.equal(data.space, 'window_image_px')
    assert.equal(data.window.left, -1200)
    assert.equal(JSON.stringify(data).includes(png.toString('base64')), false)
    assert.equal((await loadWindowObservation(item.file)).sha256, pngMetadata(png).sha256)
    assert.deepEqual(await readFile(item.output), png)
  } finally { await rm(item.dir, { recursive: true, force: true }) }
})

test('Windows PowerShell 5.1 reads the Node-produced UTF-8 observation without corrupting Chinese window titles or paths', { skip: process.platform !== 'win32' }, async () => {
  const dir = await mkdtemp(join(tmpdir(), 'boss-窗口观察-'))
  const output = join(dir, '原窗口 截图.png')
  const captured = { captured_at: new Date().toISOString(),
    window: { hwnd: '12345', pid: 300, left: -1200, top: 42, width: 1, height: 1 },
    owned_dialogs: [{ hwnd: '67890', title: '要恢复页面吗？ "异常关闭" \\恢复', class: 'Chrome_WidgetWin_1' }] }
  try {
    const observation = await captureWindow(output, 9222, async () => {
      await writeFile(output, png)
      return { stdout: JSON.stringify(captured), stderr: '' }
    })
    const bytes = await readFile(observation.metadata_path)
    assert.equal(bytes.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf])), false, 'exercise the production no-BOM sidecar')
    assert.deepEqual(await readObservationWithWindowsPowerShell(observation.metadata_path), JSON.parse(bytes.toString('utf8')),
      'titles, escaped characters, source path, numeric coordinates and image hash must all survive the read unchanged')
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('Windows PowerShell observation read remains compatible with ASCII and UTF-8 BOM while rejecting invalid JSON', { skip: process.platform !== 'win32' }, async () => {
  const item = await fixture()
  try {
    assert.deepEqual(await readObservationWithWindowsPowerShell(item.file), item.observation)
    const bomObservation = { ...item.observation, owned_dialogs: [{ title: '恢复页面' }] }
    await writeFile(item.file, Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(JSON.stringify(bomObservation), 'utf8')]))
    assert.deepEqual(await readObservationWithWindowsPowerShell(item.file), bomObservation)
    await writeFile(item.file, '{"window":{"title":"恢复页面"', 'utf8')
    await assert.rejects(readObservationWithWindowsPowerShell(item.file), (error: unknown) => {
      const failure = error as { code?: unknown; stderr?: unknown }
      return failure.code === 1 && typeof failure.stderr === 'string' && failure.stderr.includes('ConvertFrom-Json')
    }, 'malformed metadata must fail at the real JSON parser')
  } finally { await rm(item.dir, { recursive: true, force: true }) }
})

test('window click rejects expired observations, changed source images, and outside coordinates before native input', async () => {
  const item = await fixture()
  let calls = 0
  const run: WindowScriptRunner = async () => { calls++; throw new Error('must not run native input') }
  try {
    for (const [x, y] of [[-1, 0], [1, 0], [0, 1], [0.5, 0]]) await assert.rejects(clickWindow(item.file, x!, y!, 120, run), /像素/)
    for (const age of [0, 601, 1.5]) await assert.rejects(clickWindow(item.file, 0, 0, age, run), /1-600/)
    item.observation.captured_at = new Date(Date.now() - 121_000).toISOString()
    await writeFile(item.file, JSON.stringify(item.observation))
    await assert.rejects(clickWindow(item.file, 0, 0, 120, run), /过期/)
    item.observation.captured_at = new Date().toISOString()
    await writeFile(item.file, JSON.stringify(item.observation))
    await writeFile(item.output, Buffer.concat([png, Buffer.from('modified')]))
    await assert.rejects(clickWindow(item.file, 0, 0, 120, run), /hash/)
    assert.equal(calls, 0)
  } finally { await rm(item.dir, { recursive: true, force: true }) }
})

test('a fresh window observation permits one constrained click attempt without retry on a native stale failure', async () => {
  const item = await fixture()
  try {
    let calls = 0
    await assert.rejects(clickWindow(item.file, 0, 0, 120, async (_script, args) => {
      calls++
      assert.deepEqual(args, ['-Action', 'click', '-CdpPort', '9222', '-Observation', item.file, '-X', '0', '-Y', '0', '-MaxAgeSeconds', '120'])
      throw new Error('STALE_OBSERVATION: click target pixels changed')
    }), /STALE_OBSERVATION/)
    assert.equal(calls, 1)
  } finally { await rm(item.dir, { recursive: true, force: true }) }
})

test('window diagnosis sends exact image and window coordinate space without local HWND or screen position', async () => {
  const item = await fixture()
  const env = { AIDWORK_RUNTIME_SERVER: 'http://127.0.0.1:9999', AIDWORK_RUNTIME_DEVICE_TOKEN: 'test-token' }
  try {
    let calls = 0
    await visualDiagnose({ requestId, image: item.output, observation: item.file }, { env, fetch: (async (_url, init) => {
      calls++
      const body = JSON.parse(String(init?.body))
      assert.equal(body.observation.space, 'window_image_px')
      assert.equal(body.observation.image_base64, png.toString('base64'))
      for (const key of ['hwnd', 'pid', 'left', 'output', 'captured_at']) assert.equal(Object.hasOwn(body.observation, key), false)
      return Response.json({ state: 'completed' })
    }) as typeof fetch })
    assert.equal(calls, 1)
    item.observation.sha256 = '0'.repeat(64)
    await writeFile(item.file, JSON.stringify(item.observation))
    await assert.rejects(visualDiagnose({ requestId, image: item.output, observation: item.file }, { env }), /hash/)
  } finally { await rm(item.dir, { recursive: true, force: true }) }
})

test('experimental native CLI requires explicit observation and integer image points; help cannot click', () => {
  const valid = (argv: string[]) => validateCommandArgs(argv[0], parseArgs(argv)).ok
  for (const argv of [
    ['screenshot', '--output', 'x.png', '--scope'], ['screenshot', '--output', 'x.png', '--scope', 'desktop'],
    ['window-click'], ['window-click', '--observation', 'x.json', '--x', '0'],
    ['window-click', '--observation', 'x.json', '--x', '-1', '--y', '2'],
    ['window-click', '--observation', 'x.json', '--x', '1.2', '--y', '2'],
    ['window-click', '--observation', 'x.json', '--x', '1', '--y', '2', '--max-age-seconds'],
    ['window-click', '--observation', 'x.json', '--x', '1', '--y', '2', '--hwnd', '1'],
    ['visual-diagnose', '--request-id', requestId, '--retrieve-only', '--observation', 'x.json'],
  ]) assert.equal(valid(argv), false, argv.join(' '))
  assert.equal(valid(['screenshot', '--output', 'x.png', '--scope', 'window', '--json']), true)
  assert.equal(valid(['window-click', '--observation', 'x.json', '--x', '1', '--y', '2', '--json']), true)
  assert.equal(valid(['window-click', '--help']), true)
})


// 2026-10-21：win-click.ps1 已删（native 输入替代）；绑定窗口唯一性不变量由共享包
// win32-native-input 的测试覆盖，这里保留随 window-experiment.ps1 出厂的 C++ 侧断言。
test('the experiment native helper resolves only the uniquely owned debug BOSS window, never foreground guessing', async () => {
  const native = await readFile(fileURLToPath(new URL('../../scripts/BossWindowExperiment.cs', import.meta.url)), 'utf8')
  const find = native.slice(native.indexOf('public static IntPtr Find('), native.indexOf('public static bool BelongsTo'))
  assert.ok(find.includes('BelongsTo(candidate, other)'), 'owned dialogs do not count as independent main windows')
  assert.ok(find.includes('mainWindows.Count != 1'), 'foreground is not evidence of the CDP target when several BOSS windows exist')
  assert.equal(find.includes('GetForegroundWindow()'), false)
})
