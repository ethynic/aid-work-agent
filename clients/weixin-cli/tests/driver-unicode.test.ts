import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

test('driver JSON survives a non-UTF8 Windows console without corrupting target identity', { skip: process.platform !== 'win32' }, () => {
  const dir = mkdtempSync(join(tmpdir(), 'wx-driver-unicode-'))
  try {
    const common = fileURLToPath(new URL('../../drivers/ps1/_common.ps1', import.meta.url)).replace(/'/g, "''")
    const script = join(dir, 'check.ps1')
    writeFileSync(script, `\ufeff. '${common}'\n[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(936)\nWrite-DriverJson @{ok=$true; data=@{label=([string][char]0x8983 + [char]0x59d7)}}\n`, 'utf8')
    const result = spawnSync('powershell.exe', ['-NoProfile', '-File', script], { windowsHide: true })
    assert.equal(result.status, 0)
    const out = result.stdout.toString('ascii').trim()
    assert.ok([...result.stdout].every(byte => byte < 128))
    assert.equal(JSON.parse(out.slice('DRIVER_JSON: '.length)).data.label, '\u8983\u59d7')
  } finally { rmSync(dir, { recursive: true, force: true }) }
})
