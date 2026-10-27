import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

test('真实PS发送编排：明确微信身份不可被Jev yes覆盖，输入前及Enter前分别拒绝错目标', {
  skip: process.platform !== 'win32',
}, () => {
  const script = fileURLToPath(new URL('../../tests/ps1/input-locator-regression.ps1', import.meta.url))
  const drivers = fileURLToPath(new URL('../../drivers/ps1/', import.meta.url))
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', script, '-DriverDir', drivers], { encoding: 'utf8', windowsHide: true, timeout: 30_000 })
  assert.equal(result.error, undefined)
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
  assert.match(result.stdout, /PS_INPUT_LOCATOR_OK external_cases=6/)
})
