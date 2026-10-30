import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { appendJournalEntry } from '../dist/legacy/journal.js'
import { recordOperationAck, pruneCompletedJournals } from '../dist/legacy/completionProof.js'
import { ResultOutbox } from '../dist/legacy/resultOutbox.js'

test('retention requires a matching known ACK and no pending receipt; age alone cannot discard unknown or newer write facts', t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-journal-retention-'))
  t.after(() => rmSync(home, { recursive: true, force: true }))
  writeFileSync(join(home, 'config.json'), JSON.stringify({ server: 'https://fixture.invalid', device_id: 'fixture' }))
  const now = Date.now(), old = new Date(now - 8 * 24 * 60 * 60 * 1000)
  const entry = id => ({ ts: old.toISOString(), invocation_id: id, request_id: 'request-' + id,
    permit_id: 'permit-' + id, payload_hash: 'fixture', operation: 'write', phase: 'may_have_started' })
  for (const id of ['confirmed', 'unknown', 'pending', 'changed', 'recent-ack']) {
    appendJournalEntry(home, entry(id))
    const effect = id === 'unknown' ? 'unknown' : 'applied'
    recordOperationAck(home, id, { request_id: 'request-' + id, permit_id: 'permit-' + id, effect }, { effect })
    utimesSync(join(home, 'journal', id + '.jsonl'), old, old)
    if (id !== 'recent-ack') utimesSync(join(home, 'completion-proofs', id + '.json'), old, old)
  }
  new ResultOutbox(join(home, 'result-outbox')).saveLegacy('pending', { claim_token: 'fixture', success: true, effect: 'applied' })
  appendJournalEntry(home, entry('changed'))
  const result = pruneCompletedJournals(home, now)
  assert.equal(result.removed, 1); assert.equal(result.failed, 0)
  assert.equal(existsSync(join(home, 'journal', 'confirmed.jsonl')), false)
  assert.equal(existsSync(join(home, 'completion-proofs', 'confirmed.json')), false)
  for (const id of ['unknown', 'pending', 'changed', 'recent-ack']) {
    assert.equal(existsSync(join(home, 'journal', id + '.jsonl')), true, id + ' evidence must remain')
  }
})
