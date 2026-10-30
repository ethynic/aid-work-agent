import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { execFileSync, spawnSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
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
