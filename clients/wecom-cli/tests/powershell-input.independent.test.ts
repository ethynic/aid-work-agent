import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

test('independent PS 5.1 input primitives and guarded send/search bodies (no live UI)', {
  skip: process.platform !== 'win32',
}, () => {
  const script = fileURLToPath(new URL('../../tests/ps1/input-independent.ps1', import.meta.url))
  const drivers = fileURLToPath(new URL('../../drivers/ps1/', import.meta.url))
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', script, '-DriverDir', drivers], { encoding: 'utf8', timeout: 30_000 })
  assert.equal(result.error, undefined)
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
  assert.match(result.stdout, /PS_INPUT_INDEPENDENT_OK cases=20/)
})
