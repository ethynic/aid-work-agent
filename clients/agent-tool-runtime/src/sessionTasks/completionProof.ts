import { createHash, randomUUID } from 'node:crypto'
import { closeSync, fsyncSync, lstatSync, openSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import type { SessionCrypto } from './sessionStore.js'

const digest = (file: string) => createHash('sha256').update(readFileSync(file)).digest('hex')
const terminal = (status: string) => status === 'completed' || status === 'stopped'

/** Written only after the engine drains and a terminal server control + full ACK are known. */
export async function recordSessionCompletion(directory: string, facts: { assignment_id: string; task_id: string; status: string; control_epoch: number; meta_control_epoch?: number; local_seq: number; acked_seq: number }, crypto: SessionCrypto): Promise<void> {
  if (!terminal(facts.status) || facts.local_seq !== facts.acked_seq) return
  const events = join(directory, 'events.jsonl'); const meta = join(directory, 'meta.json')
  const proof = { schema_version: 1, ...facts, events_digest: digest(events), meta_digest: digest(meta) }
  const protectedValue = await crypto.protect(JSON.stringify(proof))
  if (proof.events_digest !== digest(events) || proof.meta_digest !== digest(meta)) throw new Error('Session completion facts changed')
  const file = join(directory, 'completion-proof.bin'); const temporary = `${file}.${randomUUID()}.tmp`
  writeFileSync(temporary, protectedValue, { flag: 'wx', mode: 0o600 })
  const fd = openSync(temporary, 'r+'); try { fsyncSync(fd) } finally { closeSync(fd) }
  renameSync(temporary, file)
}

export async function hasSessionCompletion(directory: string, assignmentId: string, crypto: SessionCrypto): Promise<boolean> {
  try {
    if (lstatSync(directory).isSymbolicLink() || !lstatSync(directory).isDirectory()) return false
    for (const name of ['events.jsonl', 'meta.json', 'completion-proof.bin']) if (lstatSync(join(directory, name)).isSymbolicLink()) return false
    const proof = JSON.parse(await crypto.unprotect(readFileSync(join(directory, 'completion-proof.bin'), 'utf8')))
    const meta = JSON.parse(readFileSync(join(directory, 'meta.json'), 'utf8'))
    return proof.schema_version === 1 && proof.assignment_id === assignmentId && terminal(proof.status) && proof.task_id === meta.task_id
      && Number.isSafeInteger(proof.control_epoch) && proof.control_epoch >= meta.control_epoch && (proof.meta_control_epoch ?? proof.control_epoch) === meta.control_epoch
      && Number.isSafeInteger(proof.local_seq) && proof.local_seq >= 0 && proof.acked_seq === proof.local_seq
      && proof.events_digest === digest(join(directory, 'events.jsonl')) && proof.meta_digest === digest(join(directory, 'meta.json'))
  } catch { return false }
}
