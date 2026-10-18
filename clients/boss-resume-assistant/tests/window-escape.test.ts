import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { escapeWindow, type WindowObservation, type WindowScriptRunner } from '../src/main/input/windowExperiment.js'
import { pngMetadata } from '../src/main/operations/bossScreenshot.js'

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9gAAAABJRU5ErkJggg==', 'base64')

async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), 'boss-window-escape-'))
  const output = join(dir, 'window.png')
  const file = `${output}.json`
  const observation: WindowObservation = { ...pngMetadata(png), space: 'window_image_px', output, cdp_port: 9222,
    captured_at: new Date().toISOString(), window: { hwnd: '12345', pid: 300, left: -1200, top: 0, width: 1, height: 1 }, owned_dialogs: [] }
  await writeFile(output, png)
  await writeFile(file, JSON.stringify(observation))
  return { dir, output, file, observation }
}

test('Escape delegates one fixed key to packaged CLI input and returns attempted rather than closed', async () => {
  const item = await fixture()
  try {
    let calls = 0
    const result = await escapeWindow(item.file, 120, async (script, args) => {
      calls++
      assert.match(script, /window-experiment\.ps1$/)
      assert.deepEqual(args, ['-Action', 'escape', '-CdpPort', '9222', '-Observation', item.file, '-MaxAgeSeconds', '120'])
      return { stdout: JSON.stringify({ key: 'Escape', sent: true, input_status: 'attempted', hwnd: '12345' }), stderr: '' }
    })
    assert.equal(calls, 1)
    assert.deepEqual(result, { key: 'Escape', sent: true, input_status: 'attempted', hwnd: '12345' })
    assert.equal(Object.hasOwn(result, 'closed'), false)
  } finally { await rm(item.dir, { recursive: true, force: true }) }
})

test('expired, future, changed-image and malformed-identity observations refuse Escape before native input', async () => {
  const item = await fixture()
  let calls = 0
  const run: WindowScriptRunner = async () => { calls++; throw new Error('must not input') }
  try {
    for (const age of [0, 601, 1.5]) await assert.rejects(escapeWindow(item.file, age, run), /1-600/)
    for (const offset of [-121_000, 60_000]) {
      item.observation.captured_at = new Date(Date.now() + offset).toISOString()
      await writeFile(item.file, JSON.stringify(item.observation))
      await assert.rejects(escapeWindow(item.file, 120, run), /过期/)
    }
    item.observation.captured_at = new Date().toISOString()
    item.observation.cdp_port = 0
    await writeFile(item.file, JSON.stringify(item.observation))
    await assert.rejects(escapeWindow(item.file, 120, run), /无效窗口观察/)
    item.observation.cdp_port = 9222
    await writeFile(item.file, JSON.stringify(item.observation))
    await writeFile(item.output, Buffer.concat([png, Buffer.from('changed')]))
    await assert.rejects(escapeWindow(item.file, 120, run), /hash/)
    assert.equal(calls, 0)
  } finally { await rm(item.dir, { recursive: true, force: true }) }
})

test('native refusal or uncertain input never retries Escape', async () => {
  const item = await fixture()
  try {
    for (const reason of ['Modifier key is pressed; Escape input refused', '[BOSS_INPUT_UNKNOWN] SendInput accepted=1']) {
      let calls = 0
      await assert.rejects(escapeWindow(item.file, 120, async () => { calls++; throw new Error(reason) }), (error: unknown) => error instanceof Error && error.message === reason)
      assert.equal(calls, 1)
    }
    for (const stdout of ['not-json', 'null', '{}', '{"key":"Escape","sent":true,"input_status":"attempted","hwnd":"wrong"}']) {
      let calls = 0
      await assert.rejects(escapeWindow(item.file, 120, async () => { calls++; return { stdout, stderr: '' } }), /BOSS_INPUT_UNKNOWN/)
      assert.equal(calls, 1)
    }
  } finally { await rm(item.dir, { recursive: true, force: true }) }
})

test('native Escape guards focus geometry and modifier keys before one SendInput pair; release-only cleanup cannot repeat down', async () => {
  const script = await readFile(fileURLToPath(new URL('../../scripts/BossWindowExperiment.cs', import.meta.url)), 'utf8')
  const escape = script.slice(script.indexOf('public static void Escape'))
  const mark = escape.indexOf('scope.MarkInputStarted()')
  assert.ok(escape.indexOf('RequireGeometry(') < mark)
  assert.ok(escape.indexOf('RequireForeground(') < mark)
  for (const key of ['0x10', '0x11', '0x12', '0x5B', '0x5C']) assert.ok(escape.slice(0, mark).includes(key), key)
  assert.ok(escape.indexOf('GetAsyncKeyState(') < mark)
  assert.equal((escape.match(/SendInput\(2,/g) ?? []).length, 1)
  assert.match(escape, /SendInput\(2, new Input\[\] \{ down, up \}/)
  assert.match(escape, /if \(accepted != 2\)/)
  assert.match(escape, /if \(accepted == 1\) SendInput\(1, new Input\[\] \{ up \}/)
  assert.match(escape, /BOSS_INPUT_UNKNOWN/)
  assert.equal(escape.includes('mouse_event('), false)
  assert.equal(escape.includes('Thread.Sleep'), false)
})

test('PowerShell Escape retains observation binding and refuses lost focus without activating another window', async () => {
  const bytes = await readFile(fileURLToPath(new URL('../../scripts/window-experiment.ps1', import.meta.url)))
  assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf])
  const script = bytes.toString('utf8')
  assert.match(script, /ValidateSet\('capture','click','escape'\)/)
  assert.match(script, /if \(\$Action -eq 'escape'\) \{\s*\[BossWindowExperiment\]::RequireForeground\(\$window\)\s*\} else \{/)
  const branch = script.slice(script.indexOf("} elseif ($Action -eq 'escape')"), script.indexOf('$screen ='))
  for (const required of ['meta.cdp_port', 'meta.width', 'meta.height', 'Get-FileHash', '$meta.sha256', '$original.Width', '$original.Height', 'TotalSeconds', '::Escape(']) assert.ok(branch.includes(required), required)
  assert.ok(branch.indexOf('TotalSeconds') < branch.indexOf('::Escape('))
  assert.equal(branch.includes('::Foreground('), false)
  assert.match(branch, /input_status = 'attempted'/)
})
