import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { execPowerShellScript, PowerShellScriptError } from '../src/main/input/powerShell.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { mapExecutorError } from '../src/main/operations/errorMapping.js'

function setup(t: { after: (fn: () => void) => void }, length = 500) {
  const root = mkdtempSync(path.join(tmpdir(), 'aid-ps-path-test-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const temporaryRoot = path.join(root, 'stage')
  mkdirSync(temporaryRoot)
  let source = path.join(root, '客户 scripts')
  while (source.length < length) source = path.join(source, 'source-segment')
  mkdirSync(source, { recursive: true })
  return { root, source, temporaryRoot }
}

const fixture = `\uFEFFparam([string]$Text='',[int]$ExitCode=0,[int]$Sleep=0)
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$acl=[IO.Directory]::GetAccessControl($PSScriptRoot)
$data=@{stage=$PSScriptRoot;text=$Text;dependency=[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'BossPhysicalInput.cs'));private=$acl.AreAccessRulesProtected;aceCount=$acl.Access.Count;hash=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash}
if($Sleep -gt 0){Start-Sleep -Milliseconds $Sleep}
if($ExitCode -ne 0){[Console]::Error.Write('[BOSS_INPUT_UNKNOWN] input result unknown');exit $ExitCode}
[Console]::Out.Write(($data | ConvertTo-Json -Compress))
`

test('long-path-unaware PowerShell rejects the original existing file and runs its private short copy', { skip: process.platform !== 'win32', timeout: 30_000 }, async t => {
  const f = setup(t)
  const system = path.join(process.env.SystemRoot!, 'System32/WindowsPowerShell/v1.0/powershell.exe')
  const executable = path.join(f.root, 'powershell.exe')
  const bytes = readFileSync(system)
  const index = bytes.indexOf(Buffer.from('true</longPathAware>'))
  assert.ok(index >= 0, 'Windows test host must expose the awareness manifest used by the regression')
  bytes.write('0   </longPathAware>', index, 'ascii')
  writeFileSync(executable, bytes)
  copyFileSync(system + '.config', executable + '.config')
  const script = path.join(f.source, 'win-click.ps1')
  writeFileSync(script, fixture)
  writeFileSync(path.join(f.source, 'BossPhysicalInput.cs'), 'current-version-one')
  const old = spawnSync(executable, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script], { windowsHide: true, timeout: 10_000 })
  assert.equal(old.status, 4294770688)
  assert.equal(existsSync(script), true)
  const text = `中文 '"; $(Write-Error NEVER_EXECUTE) space`
  const result = JSON.parse((await execPowerShellScript(script, ['-Text', text], { timeout: 10_000, temporaryRoot: f.temporaryRoot, executable })).stdout)
  assert.equal(result.text, text)
  assert.equal(result.dependency, 'current-version-one')
  assert.equal(result.private, true)
  assert.equal(result.aceCount, 2)
  assert.equal(result.hash.toLowerCase(), createHash('sha256').update(readFileSync(script)).digest('hex'))
  assert.ok(result.stage.length < 240)
  assert.equal(existsSync(result.stage), false)
  writeFileSync(path.join(f.source, 'BossPhysicalInput.cs'), 'current-version-two')
  const second = JSON.parse((await execPowerShellScript(script, [], { timeout: 10_000, temporaryRoot: f.temporaryRoot, executable })).stdout)
  assert.equal(second.dependency, 'current-version-two')
  assert.notEqual(second.stage, result.stage)
  assert.deepEqual(readdirSync(f.temporaryRoot), [])
})

test('preparation rejects missing dependencies, junction sources, unknown scripts and overlong temp paths', { skip: process.platform !== 'win32', timeout: 30_000 }, async t => {
  const f = setup(t)
  const script = path.join(f.source, 'win-click.ps1')
  writeFileSync(script, fixture)
  const options = { timeout: 5000, temporaryRoot: f.temporaryRoot }
  await assert.rejects(execPowerShellScript(script, [], options), /BOSS_POWERSHELL_PREPARE/)
  assert.deepEqual(readdirSync(f.temporaryRoot), [])
  writeFileSync(path.join(f.source, 'BossPhysicalInput.cs'), 'test')
  await assert.rejects(execPowerShellScript(path.join(f.source, 'unapproved.ps1'), [], options), /POWERSHELL_SCRIPT_NOT_ALLOWED/)
  const linked = path.join(f.root, 'linked')
  symlinkSync(f.source, linked, 'junction')
  await assert.rejects(execPowerShellScript(path.join(linked, 'win-click.ps1'), [], options), /POWERSHELL_UNSAFE_PATH/)
  await assert.rejects(execPowerShellScript(script, [], { ...options, temporaryRoot: f.source }), /POWERSHELL_TEMP_PATH_TOO_LONG/)
  assert.ok(!readdirSync(f.source).some(name => name.startsWith('aid-bps-')))
})

test('script failure/timeout and synchronous launch rejection clean only their own nonce and preserve unknown', { skip: process.platform !== 'win32', timeout: 30_000 }, async t => {
  const f = setup(t, 260)
  const script = path.join(f.source, 'win-click.ps1')
  writeFileSync(script, fixture)
  writeFileSync(path.join(f.source, 'BossPhysicalInput.cs'), 'current')
  const options = { timeout: 5000, temporaryRoot: f.temporaryRoot }
  await assert.rejects(execPowerShellScript(script, ['-ExitCode', '2'], options), (error: unknown) => {
    assert.ok(error instanceof PowerShellScriptError)
    assert.equal(error.exitCode, 2)
    assert.match(error.message, /BOSS_INPUT_UNKNOWN/)
    assert.equal(mapExecutorError(new WinClickError(error.message, error.exitCode)).code, 'EXECUTION_UNKNOWN')
    return true
  })
  await assert.rejects(execPowerShellScript(script, ['-Sleep', '5000'], { ...options, timeout: 300 }), PowerShellScriptError)
  const sensitive = 'SYNTHETIC_PRIVATE_ARGUMENT\0do-not-log'
  await assert.rejects(execPowerShellScript(script, ['-Text', sensitive], options), (error: unknown) => {
    assert.ok(error instanceof PowerShellScriptError)
    assert.ok(!error.message.includes('SYNTHETIC_PRIVATE_ARGUMENT'))
    assert.match(error.message, /BOSS_POWERSHELL_LAUNCH/)
    assert.equal(mapExecutorError(new WinClickError(error.message)).code, 'INTERNAL_ERROR')
    return true
  })
  assert.deepEqual(readdirSync(f.temporaryRoot), [])
})
