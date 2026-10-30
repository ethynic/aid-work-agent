/** Immutable intent checkpoint in the existing per-send artifact directory. DPAPI CurrentUser. */
import { closeSync, fsyncSync, lstatSync, openSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'
import { protectText, unprotectText } from '../security/dpapi.js'
import { CodedOperationError } from '../operations/types.js'
import type { SendReceipt } from './localdb/sendVerification.js'

export interface ReceiptCrypto {
  protect?: (plain: string) => Promise<string>
  unprotect?: (cipher: string) => Promise<string>
}

const FILE = 'send-receipt.dpapi'
const ID = /^send-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{3}Z(?:-[1-9]\d*)?$/
export const validReceiptId = (id: unknown): id is string => typeof id === 'string' && ID.test(id)

export async function saveSendReceipt(dir: string, receipt: SendReceipt, crypto: ReceiptCrypto = {}): Promise<string> {
  const id = basename(dir)
  if (!validReceiptId(id)) throw new Error('invalid receipt directory')
  const ciphertext = await (crypto.protect ?? protectText)(JSON.stringify({ ...receipt, receipt_id: id }))
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(ciphertext) || ciphertext.length > 65536) throw new Error('invalid receipt ciphertext')
  // No overwrite, and flush before the caller can dispatch a write. Partial files fail closed on load.
  const fd = openSync(join(dir, FILE), 'wx', 0o600)
  let failure: unknown
  try { writeFileSync(fd, ciphertext, 'utf8'); fsyncSync(fd) } catch (err) { failure = err }
  try { closeSync(fd) } catch (err) { failure ??= err }
  if (failure !== undefined) {
    // open(wx) succeeded: this function owns the file. An open collision never reaches cleanup.
    try { invalidateSendReceipt(dir) } catch {
      throw new CodedOperationError('INTERNAL_ERROR',
        '发送凭据保存失败且自建文件无法清理；未派发发送，勿用该凭据自动确认或重发', 'unknown',
        { receipt_id: id, receipt_invalidation_failed: true })
    }
    throw failure
  }
  return id
}

export function assertSendReceiptPresent(root: string, id: string): string {
  if (!validReceiptId(id)) throw new Error('invalid receipt id')
  const dir = join(root, id)
  const path = join(dir, FILE)
  if (!lstatSync(dir).isDirectory() || lstatSync(dir).isSymbolicLink()) throw new Error('invalid receipt directory')
  const stat = lstatSync(path)
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 1 || stat.size > 65536) throw new Error('invalid receipt file')
  return path
}

/** Only the writer which successfully created this stage's receipt may retire it. */
export function invalidateSendReceipt(dir: string): void {
  if (!validReceiptId(basename(dir))) throw new Error('invalid receipt directory')
  try { unlinkSync(join(dir, FILE)) } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err
  }
}

export async function loadSendReceipt(root: string, id: string, crypto: ReceiptCrypto = {}): Promise<SendReceipt> {
  const path = assertSendReceiptPresent(root, id)
  const r = JSON.parse(await (crypto.unprotect ?? unprotectText)(readFileSync(path, 'utf8'))) as SendReceipt & { receipt_id: string }
  if (!r || r.receipt_id !== id || r.version !== 1 || typeof r.account !== 'string' || !r.account ||
    typeof r.conversationId !== 'string' || !r.conversationId || typeof r.sourceId !== 'string' || !r.sourceId ||
    !Number.isSafeInteger(r.schema) || r.schema < 0 || !Number.isSafeInteger(r.baseline) || r.baseline < 0 ||
    typeof r.anchor !== 'string' || (r.baseline > 0 && !/^[a-f0-9]{64}$/.test(r.anchor)) ||
    typeof r.text !== 'string' || r.text.length < 1 || r.text.length > 2000 ||
    !Number.isSafeInteger(r.since) || !Number.isSafeInteger(r.until) || r.since < 0 ||
    r.until < r.since || r.until - r.since > 310) throw new Error('invalid receipt payload')
  return r
}
