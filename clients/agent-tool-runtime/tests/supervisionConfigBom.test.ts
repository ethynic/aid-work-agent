import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { ProcessSupervision } from '../src/processSupervision.js'
import { dpapiUnprotect } from '../src/dpapi.js'

test('Windows supervision starts with the same single-BOM configuration accepted by Host', { skip: process.platform !== 'win32' }, async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-supervision-bom-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  const config = '\uFEFF' + JSON.stringify({ server: 'https://isolated.invalid', device_id: 'original-device' })
  writeFileSync(join(home, 'config.json'), config)
  const supervisor = new ProcessSupervision(home, process.execPath)
  assert.ok(supervisor.enabled, 'release supervision helper must be available')
  const instance = randomUUID()
  const marker = await supervisor.marker(instance)
  const proof = JSON.parse(await dpapiUnprotect(String(marker.supervision)))
  assert.equal(marker.schemaVersion, 3)
  assert.equal(proof.generation, instance)
  assert.equal(proof.externalPython, false)
  assert.equal(Object.hasOwn(proof, 'bootId'), false)
  const launch = await supervisor.prepareLaunch(process.execPath)
  const job = launch.args[2]!
  assert.match(job, /^Global\\AidWorkRuntime\.[a-f0-9-]+$/)
  const ledger = JSON.parse(await dpapiUnprotect(readFileSync(join(home, 'process-supervision', `global-${instance}.bin`), 'utf8')))
  assert.equal(ledger.schemaVersion, 2)
  assert.deepEqual(ledger.jobs, [job], 'The owned name is protected before the child can launch')
  assert.equal(launch.args.at(-1), join(home, 'process-supervision', `${job.slice('Global\\'.length)}.receipt.json`))
  assert.equal(readFileSync(join(home, 'config.json'), 'utf8'), config)
})
