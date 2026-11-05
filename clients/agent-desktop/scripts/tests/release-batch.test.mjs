import assert from 'node:assert/strict'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { execFileSync, spawnSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..')
const script = resolve(root, 'clients/agent-desktop/scripts/release-batch.ps1')
const runPlan = (...args) => JSON.parse(execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script, ...args, '-Plan'], { encoding: 'utf8', windowsHide: true }))

test('release plan selects components independently and never edits versions or publisher history', () => {
  const files = ['clients/agent-desktop/package.json', 'clients/agent-desktop/package-lock.json',
    'clients/boss-resume-assistant/package.json', 'clients/boss-resume-assistant/package-lock.json',
    'clients/runtime-plugin-packaging/trust/acceptance-roots.json']
  const before = files.map(file => readFileSync(resolve(root, file)))
  const boss = runPlan('-Component', 'Boss', '-Signer', 'C:/isolated-fixture/not-read.json', '-BossVersion', '1.2.3')
  assert.equal(boss.boss, true); assert.equal(boss.runtime, false)
  assert.equal(boss.versions.boss, '1.2.3'); assert.equal(boss.signing, 'external-stable')
  assert.ok(!boss.steps.includes('package-installer'), 'A plugin-only update must not require rebuilding the Runtime')
  const runtime = runPlan('-Component', 'Runtime', '-RuntimeVersion', '2.3.4')
  assert.equal(runtime.runtime, true); assert.equal(runtime.boss, false)
  assert.equal(runtime.versions.runtime, '2.3.4')
  assert.ok(!runtime.steps.includes('sign-plugin'), 'Runtime-only packaging must not replace the BOSS signer or package')
  const unsigned = runPlan('-Component', 'Runtime', '-Profile', 'production', '-TrustRoots', 'C:/isolated-fixture/public-roots.json', '-Unsigned')
  assert.equal(unsigned.runtimeSigning, 'unsigned', 'Windows signing may be omitted explicitly without changing the product profile')
  const both = runPlan('-Signer', 'C:/isolated-fixture/not-read.json', '-RuntimeVersion', '2.3.4', '-BossVersion', '1.2.3')
  assert.deepEqual(both.versions, { runtime: '2.3.4', boss: '1.2.3' }, 'Component versions do not have to move together')
  files.forEach((file, i) => assert.deepEqual(readFileSync(resolve(root, file)), before[i], 'Plan must return before package writes or trust registration'))
})

test('ordinary BOSS packaging requires a stable signer instead of silently inventing a new publisher', () => {
  const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script, '-Component', 'Boss', '-Plan'], { encoding: 'utf8', windowsHide: true })
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /Signer/)
  const production = runPlan('-Component', 'Runtime', '-Profile', 'production', '-TrustRoots', 'C:/isolated-fixture/public-roots.json')
  assert.equal(production.profile, 'production')
  assert.doesNotMatch(readFileSync(script, 'utf8'), /@\('run',\s*'runtime:verify'\)/, 'A production-only batch must not run the verifier whose default directory is acceptance; packaging already verifies its exact output')
  const packager = readFileSync(resolve(root, 'clients/agent-desktop/scripts/package-runtime-win.mjs'), 'utf8')
  assert.match(packager, /\['scripts\/verify-runtime-package\.mjs', output\]/, 'The selected profile is checked by the existing package command')
})

test('WeCom delivery is independent of BOSS and can include its required Runtime', () => {
  const args = ['-Signer', 'C:/isolated-fixture/not-read.json', '-WecomVersion', '3.4.5']
  const plugin = runPlan('-Component', 'Wecom', ...args)
  assert.equal(plugin.wecom, true); assert.equal(plugin.runtime, false); assert.equal(plugin.boss, false)
  assert.equal(plugin.wecomVersion, '3.4.5'); assert.equal(plugin.releaseId, 'wecom-3.4.5')
  assert.ok(!plugin.steps.includes('boss-build'))
  const pair = runPlan('-Component', 'RuntimeWecom', '-RuntimeVersion', '4.5.6', ...args)
  assert.equal(pair.runtime, true); assert.equal(pair.boss, false)
  assert.ok(pair.steps.includes('verify-target-runtime'))
  assert.equal(pair.isolated, true)
  assert.ok(!pair.outputDirectory.includes('clients/release'), 'Deliveries must not pollute the development workspace')
  const defaults = runPlan('-Component', 'RuntimeWecom', '-Signer', 'C:/isolated-fixture/not-read.json')
  assert.equal(defaults.versions.runtime, null)
  assert.equal(defaults.wecomVersion, null)
  assert.equal(defaults.releaseId, null, 'Plan must not mistake uncommitted workspace versions for isolated SourceRef input')
  assert.match(defaults.versionDefaults, /SourceRef/)
})

test('WeCom day preflight preserves Python timezone quotes through PowerShell -File', () => {
  const home = mkdtempSync(resolve(tmpdir(), 'aid-wecom-day-preflight-'))
  try {
    const batch = readFileSync(script, 'utf8')
    const line = batch.split(/\r?\n/).find(value => value.includes('$dayProbe = &'))
    assert.ok(line, 'The release entry must check the packaged Python timezone capability')
    // Use the build machine Python and replace only the timezone lookup: this checks
    // PowerShell argument transport without requiring the release OCR asset in CI.
    const command = line.replace('from zoneinfo import ZoneInfo', 'from builtins import str as ZoneInfo')
    assert.notEqual(command, line, 'The preflight must still call ZoneInfo')
    const fixture = resolve(home, 'preflight.ps1')
    writeFileSync(fixture, [
      '$OcrDirectory = Split-Path -Parent (Get-Command python.exe).Source',
      command,
      "if ($LASTEXITCODE -ne 0 -or $dayProbe -notmatch 'TZDATA_OK') { exit 1 }",
    ].join('\r\n') + '\r\n')
    const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', fixture], { encoding: 'utf8', windowsHide: true })
    assert.equal(result.status, 0, result.stderr)
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('isolated source extraction preserves real git archive Chinese filenames and bytes', () => {
  const home = mkdtempSync(resolve(tmpdir(), 'aid-release-unicode-'))
  try {
    const source = resolve(home, 'source'), target = resolve(home, 'target'), archive = resolve(home, 'source.tar')
    mkdirSync(resolve(source, '文档'), { recursive: true }); mkdirSync(target)
    const content = Buffer.from('机器人 Markdown 通知与附件')
    writeFileSync(resolve(source, '文档/读取说明.md'), content)
    const git = args => execFileSync('git', ['-C', source, ...args], { windowsHide: true, stdio: 'pipe' })
    git(['init']); git(['add', '.']); git(['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture'])
    git(['archive', '--format=tar', `--output=${archive}`, 'HEAD'])
    const extraction = readFileSync(script, 'utf8').match(/function Expand-SourceArchive \{[\s\S]*?\r?\n\}/)?.[0]
    assert.ok(extraction, 'Exercise the production extraction function, including its encoding choice')
    const literal = text => `'${text.replaceAll("'", "''")}'`
    execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', `${extraction}\nExpand-SourceArchive ${literal(archive)} ${literal(target)}`], { windowsHide: true, stdio: 'pipe' })
    assert.deepEqual(readFileSync(resolve(target, '文档/读取说明.md')), content)
    assert.match(readFileSync(script, 'utf8'), /\$isolatedRoot = Join-Path \$env:TEMP/, 'The production dependency build must use the tested Temp area; final deliveries use AidWorkBuild')
    const dependency = resolve(target, 'clients/shared/native-input'), link = resolve(target, 'clients/shared/node_modules/@aid/native-input')
    mkdirSync(dependency, { recursive: true }); mkdirSync(dirname(link), { recursive: true })
    writeFileSync(resolve(dependency, 'package.json'), '{}')
    symlinkSync(dependency, link, 'junction')
    assert.equal(existsSync(resolve(link, 'package.json')), true, 'A clean build must resolve npm workspace dependencies through real junctions')
  } finally { rmSync(home, { recursive: true, force: true }) }
})
