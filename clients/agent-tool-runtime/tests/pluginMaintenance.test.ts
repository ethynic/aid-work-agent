import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { LegacyExecution } from '../src/runtimeHost.js'

test('import cleanup deletes only owned unneeded ZIPs and keeps unresolved snapshots and business evidence', async t => {
  const home = mkdtempSync(join(tmpdir(), 'runtime-import-cleanup-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  const directory = join(home, 'management-imports'); mkdirSync(directory)
  const done = '11111111-1111-4111-8111-111111111111'
  const pending = '22222222-2222-4222-8222-222222222222'
  for (const name of [`${done}.zip`, `${pending}.zip`, 'user-note.txt']) writeFileSync(join(directory, name), name)
  const fact = join(home, 'identity-reconciliation.bin'); writeFileSync(fact, 'original unknown send')
  new LegacyExecution(process.execPath, home).cleanupPluginImports([pending])
  assert.equal(existsSync(join(directory, `${done}.zip`)), false)
  assert.equal(existsSync(join(directory, `${pending}.zip`)), true)
  assert.equal(readFileSync(join(directory, 'user-note.txt'), 'utf8'), 'user-note.txt')
  assert.equal(readFileSync(fact, 'utf8'), 'original unknown send')
})

test('idle plugin maintenance preserves historical unknown facts without demanding business settlement', async t => {
  const home = mkdtempSync(join(tmpdir(), 'runtime-plugin-maintenance-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  const facts = ['identity-reconciliation.bin', 'journal/old.jsonl', 'session-tasks/old/meta.json', 'result-outbox/old.json']
  for (const fact of facts) {
    const path = join(home, fact)
    mkdirSync(dirname(path), { recursive: true })
    writeFileSync(path, `original unknown fact: ${fact}`)
  }
  const before = facts.map(fact => readFileSync(join(home, fact)))
  const execution = new LegacyExecution(process.execPath, home)
  for (const action of ['plugins.import', 'plugins.enable', 'plugins.disable', 'plugins.uninstall']) {
    assert.equal(await execution.canChangePlugins(action), true, `${action} must not require global historical task settlement`)
  }
  facts.forEach((fact, i) => assert.deepEqual(readFileSync(join(home, fact)), before[i], 'Maintenance eligibility never erases an unknown send or settles its receipt'))
})
