import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { recordOperationAck, hasJournalCompletionProof } from '../dist/legacy/completionProof.js'

for (const [name, prefix, effect, resolved] of [
  ['single BOM preserves original device identity in an acknowledged result', '\uFEFF', 'applied', true],
  ['a BOM never converts an unknown effect into completion', '\uFEFF', 'unknown', false],
  ['invalid double BOM does not create an identity completion proof', '\uFEFF\uFEFF', 'applied', false],
]) test(name, t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-ack-bom-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  const config = prefix + JSON.stringify({ server: 'https://isolated.invalid', device_id: 'original-device' })
  writeFileSync(join(home, 'config.json'), config)
  mkdirSync(join(home, 'journal'))
  const journal = JSON.stringify({ invocation_id: 'inv-one', request_id: 'req-one', permit_id: null, phase: 'may_have_started' }) + '\n'
  writeFileSync(join(home, 'journal/inv-one.jsonl'), journal)
  recordOperationAck(home, 'inv-one', { request_id: 'req-one', effect }, { effect })
  const proof = JSON.parse(readFileSync(join(home, 'completion-proofs/inv-one.json'), 'utf8'))
  assert.equal(proof.resolved, resolved)
  assert.equal(hasJournalCompletionProof(home, 'inv-one.jsonl'), resolved)
  if (prefix === '\uFEFF') assert.equal(proof.device_id, 'original-device')
  assert.equal(readFileSync(join(home, 'config.json'), 'utf8'), config)
  assert.equal(readFileSync(join(home, 'journal/inv-one.jsonl'), 'utf8'), journal)
})
