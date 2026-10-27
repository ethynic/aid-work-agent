import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

test('production PS bodies distinguish tray/logout and clean search without Escape', { skip: process.platform !== 'win32' }, () => {
  const script = fileURLToPath(new URL('../../tests/ps1/tray-escape-regression.ps1', import.meta.url))
  const drivers = fileURLToPath(new URL('../../drivers/ps1/', import.meta.url))
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script, '-DriverDir', drivers], {
    encoding: 'utf8', windowsHide: true, timeout: 30000,
  })
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
  assert.match(result.stdout, /PS_TRAY_ESCAPE_OK cases=7/)
})
