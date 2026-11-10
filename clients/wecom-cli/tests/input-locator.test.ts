import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

for (const suite of [
  { file: 'input-locator-regression.ps1', marker: /PS_INPUT_LOCATOR_OK/ },
  { file: 'send-search-gate-regression.ps1', marker: /PS_SEND_SEARCH_GATE_OK/ },
  { file: 'title-copy-diagnostics-regression.ps1', marker: /PS_TITLE_COPY_DIAGNOSTICS_OK cases=3/ },
  { file: 'chat-select-clipboard-regression.ps1', marker: /PS_CHAT_SELECT_CLIPBOARD_OK cases=3/ },
]) test(`real PowerShell ${suite.file} enforces title and input safeguards`, {
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
