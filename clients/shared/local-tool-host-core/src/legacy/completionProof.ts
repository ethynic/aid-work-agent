import { createHash, randomUUID } from 'node:crypto'
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import type { OperationResultAck, OperationResultPayload } from './apiClient.js'

const digest = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex')
const known = (effect: string) => effect === 'none' || effect === 'applied'

/** New evidence only: a received original result ACK, never inferred from an empty outbox. */
export function recordOperationAck(home: string, invocationId: string, payload: OperationResultPayload, ack: OperationResultAck | undefined): void {
  if (!/^[A-Za-z0-9_-]+$/.test(invocationId)) throw new Error('Invalid completion identity')
  const journal = join(home, 'journal', `${invocationId}.jsonl`)
  if (!existsSync(journal)) return // Read-only/non-started work has no write journal to settle.
  let identity: { server: string; device_id: string } | undefined
  try {
    const config = JSON.parse(readFileSync(join(home, 'config.json'), 'utf8'))
    const url = new URL(config.server)
    if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && typeof config.device_id === 'string' && config.device_id) identity = { server: config.server, device_id: config.device_id }
  } catch { /* No provable original identity: retain an unresolved receipt. */ }
  const { claim_token: _claim, permit_token: _permit, ...result } = payload
  const proof = { schema_version: 1, invocation_id: invocationId, request_id: payload.request_id, permit_id: payload.permit_id ?? null,
    ...identity, result_digest: digest(JSON.stringify(result)),
    reported_effect: payload.effect, accepted_effect: ack?.effect ?? '', journal_digest: digest(readFileSync(journal)),
    resolved: Boolean(identity) && known(payload.effect) && ack?.effect === payload.effect }
  const directory = join(home, 'completion-proofs'); mkdirSync(directory, { recursive: true })
  const file = join(directory, `${invocationId}.json`); const temporary = `${file}.${randomUUID()}.tmp`
  writeFileSync(temporary, JSON.stringify(proof), { flag: 'wx', mode: 0o600 })
  const fd = openSync(temporary, 'r+'); try { fsyncSync(fd) } finally { closeSync(fd) }
  renameSync(temporary, file)
}

export function hasJournalCompletionProof(home: string, filename: string): boolean {
  if (!filename.endsWith('.jsonl')) return false
  try {
    const id = filename.slice(0, -6)
    const journal = readFileSync(join(home, 'journal', filename))
    const proof = JSON.parse(readFileSync(join(home, 'completion-proofs', `${id}.json`), 'utf8'))
    const entries = journal.toString('utf8').trim().split('\n').map(line => JSON.parse(line))
    return proof.schema_version === 1 && proof.invocation_id === id && proof.resolved === true && known(proof.reported_effect)
      && typeof proof.server === 'string' && typeof proof.device_id === 'string' && proof.device_id && /^[0-9a-f]{64}$/.test(proof.result_digest)
      && proof.accepted_effect === proof.reported_effect && proof.journal_digest === digest(journal) && entries.length > 0
      && entries.every(entry => entry.invocation_id === id && entry.request_id === proof.request_id && entry.permit_id === proof.permit_id && entry.phase === 'may_have_started')
  } catch { return false }
}
