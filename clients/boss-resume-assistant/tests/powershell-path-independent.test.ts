import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { copyFileSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmdirSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { after, test, type TestContext } from 'node:test'

const here = dirname(fileURLToPath(import.meta.url))
const boss = resolve(here, basename(dirname(here)) === 'dist' ? '../..' : '..')
const repo = resolve(boss, '../..')
const load = (name: string) => import(pathToFileURL(join(boss, 'dist/src/main', name + '.js')).href)
const { execPowerShellScript, PowerShellScriptError } = await load('input/powerShell')
const { WinMouseClicker, WinClickError } = await load('input/WinMouseClicker')
const { mapExecutorError } = await load('operations/errorMapping')
const { sameViewAt, stitchParts } = await load('operations/bossResumeDetail')
const output = join(repo, 'clients/agent-desktop/build/boss-powershell-independent')
mkdirSync(output, { recursive: true })
const results: Record<string, unknown>[] = []
const keepAlive = setInterval(() => {}, 500)
const sha = (file: string) => createHash('sha256').update(readFileSync(file)).digest('hex')
const productFiles = ['src/main/input/powerShell.ts', 'src/main/input/WinMouseClicker.ts', 'src/main/operations/bossResumeDetail.ts', 'src/main/operations/errorMapping.ts', 'dist/src/main/input/powerShell.js']
const bindings = Object.fromEntries(productFiles.map(name => [name, sha(join(boss, name))]))
const systemPs = join(process.env.SystemRoot!, 'System32/WindowsPowerShell/v1.0/powershell.exe')
const systemHash = sha(systemPs)

function removeOwned(root: string) {
  assert.ok(resolve(root).startsWith(resolve(tmpdir()) + '\\aid-bps-independent-'))
  function remove(directory: string) {
    for (const item of readdirSync(directory, { withFileTypes: true })) {
      const child = join(directory, item.name)
      if (item.isDirectory() && !item.isSymbolicLink()) remove(child)
      else unlinkSync(child)
    }
    rmdirSync(directory)
  }
  remove('\\\\?\\' + resolve(root))
}
function fixture(t: TestContext) {
  const root = mkdtempSync(join(tmpdir(), 'aid-bps-independent-'))
  const stageRoot = join(root, 'private-stage-base')
  mkdirSync(stageRoot)
  const bytes = readFileSync(systemPs)
  const marker = Buffer.from('true</longPathAware>', 'ascii')
  const index = bytes.indexOf(marker)
  assert.ok(index >= 0, 'need the actual awareness manifest for the controlled old-host reproduction')
  assert.equal(bytes.indexOf(marker, index + 1), -1)
  bytes.write('0   </longPathAware>', index, 'ascii')
  const unaware = join(root, 'powershell.exe')
  writeFileSync(unaware, bytes)
  copyFileSync(systemPs + '.config', unaware + '.config')
  t.after(() => removeOwned(root))
  return { root, stageRoot, unaware, options: { executable: unaware, temporaryRoot: stageRoot, timeout: 15000 } }
}
function scriptAt(root: string, name: string, length: number) {
  let directory = join(root, randomUUID())
  while (join(directory, name).length < length) {
    const remaining = length - join(directory, name).length
    if (remaining === 1) directory += '中'
    else directory = join(directory, '中文 空格 路径验证'.repeat(5).slice(0, Math.min(43, remaining - 1)))
  }
  const file = join(directory, name)
  assert.equal(file.length, length)
  mkdirSync(directory, { recursive: true })
  return file
}
function ps(script: string) {
  const encoded = Buffer.from(script, 'utf16le').toString('base64')
  const run = spawnSync(systemPs, ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], { encoding: 'utf8', windowsHide: true, timeout: 20000 })
  assert.equal(run.status, 0, 'owned image/probe PowerShell should succeed')
  return run.stdout.trim()
}
const synthetic = (version: string) => '\uFEFF' + `param([string]$Text='', [int]$Hold=0, [string]$Mode='', [int]$X=0,[int]$Y=0,[int]$CssW=0,[int]$CssH=0,[switch]$OldProbe)
if($OldProbe){[Console]::Out.Write('DIRECT_OK');exit 0}
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
if($Mode -eq 'timeout'){Start-Sleep -Seconds 8}
if($Mode -eq 'unknown'){[Console]::Error.Write('[BOSS_INPUT_UNKNOWN] harmless fixture uncertainty');exit 3}
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$acl=[IO.Directory]::GetAccessControl($PSScriptRoot)
$aces=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object { @{sid=$_.IdentityReference.Value;rights=[int]$_.FileSystemRights;type=[string]$_.AccessControlType} })
$data=@{version='${version}';path=$PSCommandPath;stage=$PSScriptRoot;text=$Text;sid=$sid;private=$acl.AreAccessRulesProtected;aces=$aces;fileLink=(([int][IO.File]::GetAttributes($PSCommandPath) -band 1024) -ne 0);folderLink=(([int][IO.File]::GetAttributes($PSScriptRoot) -band 1024) -ne 0);scriptHash=(Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash;dependencyHash=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'BossPhysicalInput.cs') -Algorithm SHA256).Hash}
if($Hold -gt 0){Start-Sleep -Milliseconds $Hold}
[Console]::Out.Write(($data | ConvertTo-Json -Compress -Depth 5))
`
function fake(file: string, version = 'one') {
  writeFileSync(file, synthetic(version))
  const dependency = join(dirname(file), 'BossPhysicalInput.cs')
  writeFileSync(dependency, '\uFEFF// byte-preserved dependency 中文 ' + version)
  return dependency
}
function validateStage(data: Record<string, any>, source: string, dependency: string) {
  assert.ok(data.path.length < 240)
  assert.equal(data.private, true)
  assert.deepEqual(data.aces.map((ace: { sid: string }) => ace.sid).sort(), ['S-1-5-18', data.sid].sort())
  assert.ok(data.aces.every((ace: { rights: number; type: string }) => ace.rights === 2032127 && ace.type === 'Allow'))
  assert.equal(data.fileLink, false)
  assert.equal(data.folderLink, false)
  assert.equal(data.scriptHash.toLowerCase(), sha(source))
  assert.equal(data.dependencyHash.toLowerCase(), sha(dependency))
  assert.equal(existsSync(data.stage), false)
}

test('independent unaware PowerShell 240/260/500 boundary reproduces old failure and new private staging succeeds', { skip: process.platform !== 'win32', timeout: 60000 }, async t => {
  const f = fixture(t)
  const outcomes = []
  for (const length of [240, 260, 500]) {
    const file = scriptAt(f.root, 'win-click.ps1', length)
    const dep = fake(file)
    const old = spawnSync(f.unaware, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', file, '-OldProbe'], { windowsHide: true, timeout: 15000 })
    assert.equal(old.status, length < 260 ? 0 : 4294770688)
    assert.equal(existsSync(file), true)
    const literal = `中文 空格 '" ; $(Write-Error NEVER_EXECUTE) & | < > trailing\\`
    const reply = JSON.parse((await execPowerShellScript(file, ['-Text', literal], f.options)).stdout)
    assert.equal(reply.text, literal)
    validateStage(reply, file, dep)
    assert.deepEqual(readdirSync(f.stageRoot), [])
    outcomes.push({ source_length: length, old_exit: old.status, new_success: true, staged_path_length: reply.path.length, exact_bytes: true, literal_argument: true, private_acl: true })
  }
  assert.equal(sha(systemPs), systemHash)
  results.push({ name: 'real_unaware_boundary', outcomes, system_exe_untouched: true })
})

test('independent concurrent calls and source-version change never share or reuse staging', { skip: process.platform !== 'win32', timeout: 60000 }, async t => {
  const f = fixture(t)
  const sourceA = scriptAt(f.root, 'win-click.ps1', 505)
  const sourceB = scriptAt(f.root, 'win-click.ps1', 507)
  const depA = fake(sourceA, 'first')
  const depB = fake(sourceB, 'second')
  const [a, b] = await Promise.all([sourceA, sourceB].map(source => execPowerShellScript(source, ['-Hold', '1000'], f.options).then((result: { stdout: string }) => JSON.parse(result.stdout))))
  assert.equal(a.version, 'first'); assert.equal(b.version, 'second')
  assert.notEqual(a.stage, b.stage)
  validateStage(a, sourceA, depA); validateStage(b, sourceB, depB)
  fake(sourceA, 'updated')
  const c = JSON.parse((await execPowerShellScript(sourceA, [], f.options)).stdout)
  validateStage(c, sourceA, depA)
  assert.equal(c.version, 'updated')
  assert.notEqual(c.stage, a.stage)
  assert.deepEqual(readdirSync(f.stageRoot), [])
  results.push({ name: 'concurrent_version_isolation', simultaneous_calls: 2, updated_source_used: true, three_distinct_nonces: true, residual_stage_count: 0 })
})

test('independent missing dependency, unapproved source, UNC, junction and long-temp inputs fail without stale fallback', { skip: process.platform !== 'win32', timeout: 45000 }, async t => {
  const f = fixture(t)
  const source = scriptAt(f.root, 'win-click.ps1', 500)
  writeFileSync(source, synthetic('missing'))
  await assert.rejects(execPowerShellScript(source, [], f.options), /BOSS_POWERSHELL_PREPARE/)
  fake(source)
  await assert.rejects(execPowerShellScript('\\\\localhost\\C$\\win-click.ps1', [], f.options), /BOSS_POWERSHELL_PREPARE/)
  const unknown = join(dirname(source), 'other.ps1')
  writeFileSync(unknown, 'exit 99')
  await assert.rejects(execPowerShellScript(unknown, [], f.options), /BOSS_POWERSHELL_PREPARE/)
  const linkedSource = join(f.root, 'linked-source')
  symlinkSync(dirname(source), linkedSource, 'junction')
  await assert.rejects(execPowerShellScript(join(linkedSource, 'win-click.ps1'), [], f.options), /POWERSHELL_UNSAFE_PATH/)
  const linkedTemp = join(f.root, 'linked-stage')
  symlinkSync(f.stageRoot, linkedTemp, 'junction')
  await assert.rejects(execPowerShellScript(source, [], { ...f.options, temporaryRoot: linkedTemp }), /POWERSHELL_UNSAFE_PATH/)
  await assert.rejects(execPowerShellScript(source, [], { ...f.options, temporaryRoot: dirname(source) }), /POWERSHELL_TEMP_PATH_TOO_LONG/)
  assert.ok(!readdirSync(dirname(source)).some(name => name.startsWith('aid-bps-')))
  assert.deepEqual(readdirSync(f.stageRoot), [])
  results.push({ name: 'unsafe_sources_refused', cases: 6, fallback: false, residual_stage_count: 0 })
})

test('independent failure timeout launch errors clean nonce and preserve unknown priority through actual clicker wrapper', { skip: process.platform !== 'win32', timeout: 60000 }, async t => {
  const f = fixture(t)
  const source = scriptAt(f.root, 'win-click.ps1', 500)
  fake(source)
  await assert.rejects(execPowerShellScript(source, ['-Mode', 'timeout'], { ...f.options, timeout: 800 }), (error: unknown) => error instanceof PowerShellScriptError)
  assert.deepEqual(readdirSync(f.stageRoot), [])
  const privateArgument = 'PRIVATE_TEST_ARGUMENT_DO_NOT_LOG\0invalid'
  await assert.rejects(execPowerShellScript(source, ['-Text', privateArgument], f.options), (error: any) => {
    assert.ok(error instanceof PowerShellScriptError)
    assert.match(error.message, /BOSS_POWERSHELL_LAUNCH/)
    assert.ok(!error.message.includes('PRIVATE_TEST_ARGUMENT'))
    assert.equal(mapExecutorError(new WinClickError(error.message)).code, 'INTERNAL_ERROR')
    return true
  })
  writeFileSync(source, '\uFEFFparam([int]$X,[int]$Y,[int]$CssW,[int]$CssH)\n[Console]::Error.Write("[BOSS_INPUT_UNKNOWN] harmless independent fixture");exit 3')
  const clicker = new WinMouseClicker({ scriptPath: source })
  await assert.rejects(clicker.click({ x: 1, y: 1 }, { width: 10, height: 10 }), (error: any) => {
    assert.ok(error instanceof WinClickError)
    assert.equal(mapExecutorError(error).code, 'EXECUTION_UNKNOWN')
    assert.equal(mapExecutorError(new WinClickError(error.message + '[BOSS_POWERSHELL_LAUNCH]')).code, 'EXECUTION_UNKNOWN')
    return true
  })
  unlinkSync(join(dirname(source), 'BossPhysicalInput.cs'))
  await assert.rejects(clicker.click({ x: 1, y: 1 }, { width: 10, height: 10 }), (error: any) => {
    assert.equal(mapExecutorError(error).code, 'INTERNAL_ERROR'); return true
  })
  assert.deepEqual(readdirSync(f.stageRoot), [])
  results.push({ name: 'failure_and_unknown', timeout_rejected: true, synchronous_argument_error_redacted: true, actual_default_clicker_wrapper: true, unknown_priority_preserved: true, preparation_internal_error: true })
})

test('independent four genuine scripts compile and run harmless guards or synthetic-image operations from >500 paths', { skip: process.platform !== 'win32', timeout: 90000 }, async t => {
  const f = fixture(t)
  const script = scriptAt(f.root, 'win-click.ps1', 510)
  const source = dirname(script)
  for (const name of ['win-click.ps1', 'cv-wheel.ps1', 'cv-segdiff.ps1', 'cv-stitch.ps1', 'BossPhysicalInput.cs']) copyFileSync(join(boss, 'scripts', name), '\\\\?\\' + join(source, name))
  const uniqueTitle = 'NO_SUCH_AID_INDEPENDENT_WINDOW_' + randomUUID()
  for (const name of ['win-click.ps1', 'cv-wheel.ps1']) {
    await assert.rejects(execPowerShellScript(join(source, name), ['-X', '0', '-Y', '0', '-TitleKeyword', uniqueTitle], f.options), (error: any) => {
      assert.equal(error.exitCode, 1)
      assert.ok(!error.message.includes('[BOSS_POWERSHELL_PREPARE]'))
      assert.ok(!error.message.includes('[BOSS_POWERSHELL_LAUNCH]'))
      assert.match(error.message, /Chrome|chrome|window|窗口/)
      return true
    })
  }
  const imageA = join(f.root, 'image-a.png'), imageB = join(f.root, 'image-b.png'), imageOut = join(f.root, 'stitched.png')
  ps(`$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Drawing; $b=New-Object Drawing.Bitmap(8,8);$g=[Drawing.Graphics]::FromImage($b);$g.Clear([Drawing.Color]::White);$b.Save('${imageA}',[Drawing.Imaging.ImageFormat]::Png);$b.Save('${imageB}',[Drawing.Imaging.ImageFormat]::Png);$g.Dispose();$b.Dispose()`)
  const geometry = ['-X', '0', '-Y', '0', '-W', '8', '-H', '8']
  const diff = await execPowerShellScript(join(source, 'cv-segdiff.ps1'), ['-A', imageA, '-B', imageB, ...geometry], f.options)
  assert.match(diff.stdout, /misrate=0\.0000/)
  const stitch = await execPowerShellScript(join(source, 'cv-stitch.ps1'), ['-Parts', imageA, ...geometry, '-Out', imageOut], f.options)
  assert.match(stitch.stdout, /stitched=8x8/)
  assert.ok(existsSync(imageOut))
  const imageViaOperation = join(f.root, 'operation-stitch.png')
  assert.equal(await sameViewAt(imageA, imageB, { x: 0, y: 0, w: 8, h: 8 }), true)
  assert.deepEqual(await stitchParts([imageA], { x: 0, y: 0, w: 8, h: 8 }, imageViaOperation), { width: 8, height: 8, overlaps: [], seamMis: [] })
  assert.deepEqual(readdirSync(f.stageRoot), [])
  results.push({ name: 'four_actual_scripts', source_script_lengths: ['win-click.ps1', 'cv-wheel.ps1', 'cv-segdiff.ps1', 'cv-stitch.ps1'].map(name => join(source, name).length), win_click_and_wheel: 'compiled and reached unique nonexistent-window guard; no input sent', segdiff: 'misrate=0.0000', stitch: 'stitched=8x8', actual_runPs_exports: ['sameViewAt', 'stitchParts'], recruitment_actions: 0 })
})

after(() => {
  clearInterval(keepAlive)
  assert.equal(sha(systemPs), systemHash, 'system PowerShell must remain byte-identical')
  for (const [name, hash] of Object.entries(bindings)) assert.equal(sha(join(boss, name)), hash, 'tested product bytes changed: ' + name)
  writeFileSync(join(output, 'boss-powershell-independent-raw.json'), JSON.stringify({ date: '2026-10-15', source_bindings: bindings, test_source_sha256: sha(fileURLToPath(import.meta.url)), passed_results: results, system_powershell_unchanged: true, scope: 'Only owned nonce dirs and copied PowerShell manifest changed. No system executable/registry mutation, customer/server access, recruiting actions or deployment.' }, null, 2) + '\n')
})
