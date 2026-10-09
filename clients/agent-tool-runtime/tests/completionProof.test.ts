import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { recordOperationAck, hasJournalCompletionProof } from '@aid/local-tool-host-core/legacy/completionProof'
import { appendJournalEntry } from '../src/journal.js'
import { recordSessionCompletion, hasSessionCompletion } from '../src/sessionTasks/completionProof.js'

test('identity settlement requires original ACK, known effect and unchanged journal; no secret is retained', async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-completion-')); t.after(() => rmSync(home, { recursive: true, force: true }))
  writeFileSync(join(home, 'config.json'), JSON.stringify({ server: 'https://isolated.test', device_id: 'original-device' }))
  appendJournalEntry(home, { ts: 'test', invocation_id: 'inv', request_id: 'request', permit_id: 'permit', phase: 'may_have_started' })
  assert.equal(hasJournalCompletionProof(home, 'inv.jsonl'), false, 'An empty outbox cannot prove completion')
  const result = { claim_token: 'DO_NOT_PERSIST_CLAIM', permit_token: 'DO_NOT_PERSIST_PERMIT', request_id: 'request', permit_id: 'permit', effect: 'unknown' }
  recordOperationAck(home, 'inv', result, { state: 'unknown', effect: 'unknown' })
  assert.equal(hasJournalCompletionProof(home, 'inv.jsonl'), false, 'ACK unknown still requires investigation')
  recordOperationAck(home, 'inv', { ...result, effect: 'applied' }, { state: 'succeeded', effect: 'applied' })
  assert.equal(hasJournalCompletionProof(home, 'inv.jsonl'), true)
  const proof = readFileSync(join(home, 'completion-proofs/inv.json'), 'utf8')
  assert.equal(proof.includes('DO_NOT_PERSIST'), false)
  assert.equal(JSON.parse(proof).device_id, 'original-device')
  appendJournalEntry(home, { ts: 'later', invocation_id: 'inv', request_id: 'request', permit_id: 'permit', phase: 'may_have_started' })
  assert.equal(hasJournalCompletionProof(home, 'inv.jsonl'), false, 'Later may-have-started evidence invalidates the earlier settlement')
})

test('session settlement needs terminal control, complete ACK and unchanged protected facts', async t => {
  const directory = mkdtempSync(join(tmpdir(), 'aid-session-completion-')); t.after(() => rmSync(directory, { recursive: true, force: true }))
  const crypto = { protect: async (text: string) => Buffer.from(text).toString('base64'), unprotect: async (text: string) => Buffer.from(text, 'base64').toString() }
  writeFileSync(join(directory, 'events.jsonl'), '{"local_seq":1}\n')
  writeFileSync(join(directory, 'meta.json'), JSON.stringify({ task_id: 'task', control_epoch: 2 }))
  const facts = { assignment_id: 'assignment', task_id: 'task', status: 'active', control_epoch: 2, local_seq: 1, acked_seq: 1 }
  await recordSessionCompletion(directory, facts, crypto)
  assert.equal(await hasSessionCompletion(directory, 'assignment', crypto), false)
  await recordSessionCompletion(directory, { ...facts, status: 'stopped', acked_seq: 0 }, crypto)
  assert.equal(await hasSessionCompletion(directory, 'assignment', crypto), false)
  await recordSessionCompletion(directory, { ...facts, status: 'stopped' }, crypto)
  assert.equal(await hasSessionCompletion(directory, 'assignment', crypto), true)
  assert.equal(await hasSessionCompletion(directory, 'other-assignment', crypto), false)
  writeFileSync(join(directory, 'meta.json'), JSON.stringify({ task_id: 'task', control_epoch: 3 }))
  assert.equal(await hasSessionCompletion(directory, 'assignment', crypto), false, 'Changed control facts invalidate an earlier completion proof')
})
