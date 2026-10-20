import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { parseArgs } from '../src/cli/args.js'
import { validateCommandArgs } from '../src/cli/validate.js'

// Recovery Escape was removed after it affected the underlying BOSS page as well as the obstruction.
test('retired window-escape rejects before it can attach Chrome or read an observation', () => {
  const argv = ['window-escape', '--observation', 'must-not-be-read.png.json']
  const validated = validateCommandArgs(argv[0], parseArgs(argv))
  assert.equal(validated.ok, false)
  if (!validated.ok) assert.match(validated.message, /已移除/)
  const cli = fileURLToPath(new URL('../src/cli/index.js', import.meta.url))
  const result = spawnSync(process.execPath, [cli, ...argv], { encoding: 'utf8', windowsHide: true,
    env: { ...process.env, AID_BOSS_AUTO_CHROME: '0', AID_BOSS_CDP_PORT: '1' } })
  assert.equal(result.status, 2)
  assert.match(result.stderr, /故障恢复仅允许鼠标点击关闭/)
  assert.equal(result.stdout, '')
})

test('help and native recovery helpers expose mouse clicks without a keyboard bypass', async () => {
  const bytes = await readFile(fileURLToPath(new URL('../../scripts/window-experiment.ps1', import.meta.url)))
  assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf])
  const script = bytes.toString('utf8')
  assert.match(script, /ValidateSet\('capture','click'\)/)
  assert.equal(script.includes("$Action -eq 'escape'"), false)
  const native = await readFile(fileURLToPath(new URL('../../scripts/BossWindowExperiment.cs', import.meta.url)), 'utf8')
  for (const keyboard of ['void Escape(', 'SendInput(', 'GetAsyncKeyState(']) assert.equal(native.includes(keyboard), false, keyboard)
  const cli = fileURLToPath(new URL('../src/cli/index.js', import.meta.url))
  const help = spawnSync(process.execPath, [cli, '--help'], { encoding: 'utf8', windowsHide: true,
    env: { ...process.env, AID_BOSS_AUTO_CHROME: '0' } })
  assert.equal(help.status, 0)
  assert.equal(help.stdout.includes('window-escape'), false)
  assert.match(help.stdout, /window-click/)
})
