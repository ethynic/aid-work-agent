import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

for (const suite of [
  { file: 'input-locator-regression.ps1', marker: /PS_INPUT_LOCATOR_OK/ },
  { file: 'search-input-regression.ps1', marker: /PS_SEARCH_INPUT_OK/ },
  { file: 'clipboard-policy-regression.ps1', marker: /PS_CLIPBOARD_POLICY_OK/ },
]) test(`real PowerShell ${suite.file} preserves input and clipboard safety`, {
  skip: process.platform !== 'win32',
}, () => {
  const script = fileURLToPath(new URL(`../../tests/ps1/${suite.file}`, import.meta.url))
  const drivers = fileURLToPath(new URL('../../drivers/ps1/', import.meta.url))
  const result = spawnSync('powershell.exe', ['-STA', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', script, '-DriverDir', drivers], { encoding: 'utf8', timeout: 30_000 })
  assert.equal(result.error, undefined)
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`)
  assert.match(result.stdout, suite.marker)
})
