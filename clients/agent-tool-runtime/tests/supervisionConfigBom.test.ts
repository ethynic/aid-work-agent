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
  assert.equal(marker.schemaVersion, 2)
  assert.equal(proof.generation, instance)
  assert.equal(proof.rebootOnly, false)
  assert.equal(readFileSync(join(home, 'config.json'), 'utf8'), config)
})
