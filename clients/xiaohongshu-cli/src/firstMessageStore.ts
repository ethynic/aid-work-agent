import { createHash } from 'node:crypto'
import { mkdir, open, readFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join } from 'node:path'

export interface FirstMessageRecord {
  schema_version: 1
  record_id: string
  status: 'not_sent' | 'unknown' | 'sent'
  expected_sender_id: string
  sender_id: string | null
  target_id: string
  profile_url: string
  source_profile_url: string
  note_id: string
  comment_id: string
  reason: string
  message: string
  created_at: string
  checked_at?: string
  reserved_at?: string
  sent_at?: string
  target_nickname?: string
  target_red_id?: string
  message_id?: string
  store_id?: string
  local_receipt?: 'saved' | 'unconfirmed'
}

/** 一份首联占用永久保留。回执单独写入，回执不完整也不能重新发送。 */
export class FirstMessageStore {
  constructor(private readonly root = join(process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'),
    'AidWork', 'Xiaohongshu', 'FirstMessages')) {}

  recordId(profileKey: string, senderId: string, targetId: string): string {
    return createHash('sha256').update(JSON.stringify([1, profileKey, senderId, targetId])).digest('hex')
  }

  async find(recordId: string): Promise<FirstMessageRecord | undefined> {
    let reserved: string
    try { reserved = await readFile(join(this.root, recordId + '.json'), 'utf8') }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined
      throw error
    }
    const reservation = JSON.parse(reserved) as FirstMessageRecord
    if (reservation.record_id !== recordId || reservation.status !== 'unknown') throw new Error('Invalid reservation')
    try {
      const receipt = JSON.parse(await readFile(join(this.root, recordId + '.result.json'), 'utf8')) as FirstMessageRecord
      if (receipt.record_id !== recordId || !['unknown', 'sent'].includes(receipt.status)) throw new Error('Invalid receipt')
      return receipt
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return reservation
      throw error
    }
  }

  async reserve(record: FirstMessageRecord): Promise<boolean> {
    await mkdir(this.root, { recursive: true, mode: 0o700 })
    let file
    try { file = await open(join(this.root, record.record_id + '.json'), 'wx', 0o600) }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EEXIST') return false
      throw error
    }
    try {
      await file.writeFile(JSON.stringify(record) + '\n', 'utf8')
      await file.sync()
    } finally { await file.close() }
    return true
  }

  async finish(record: FirstMessageRecord): Promise<void> {
    const file = await open(join(this.root, record.record_id + '.result.json'), 'wx', 0o600)
    try {
      await file.writeFile(JSON.stringify(record) + '\n', 'utf8')
      await file.sync()
    } finally { await file.close() }
  }
}
