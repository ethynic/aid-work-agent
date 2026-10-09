/**
 * v2 写动作本地 journal（§5.2 / R14）。
 *
 * - 输入开始前把 {ts, invocation_id, request_id, permit_id, payload_hash, operation,
 *   phase:'may_have_started'} 追加写入 <runtime home>/journal/<invocation_id>.jsonl 并
 *   fsync（Windows 上 fs.fsyncSync(fd) 映射 FlushFileBuffers，同样保证崩溃后落盘）。
 * - 写入/fsync 失败 → 抛 JournalWriteError，调用方禁止执行该操作（终态
 *   JOURNAL_WRITE_FAILED/effect none）——无 journal 即无证据链，宁可不做。
 * - journal 只追加不删除（崩溃恢复排查依据）；permit_token/claim_token 不写入。
 */
import { closeSync, fsyncSync, mkdirSync, openSync, writeSync } from 'node:fs'
import path from 'node:path'

export interface JournalEntry {
  ts: string
  invocation_id: string
  request_id: string
  permit_id: string
  payload_hash?: string
  operation?: string
  phase: 'may_have_started'
}

export class JournalWriteError extends Error {
  constructor(message: string, readonly cause?: unknown) {
    super(message)
    this.name = 'JournalWriteError'
  }
}

export function journalDir(runtimeHome: string): string {
  return path.join(runtimeHome, 'journal')
}

export function journalFilePath(runtimeHome: string, invocationId: string): string {
  return path.join(journalDir(runtimeHome), `${invocationId}.jsonl`)
}

/**
 * 追加一条 journal 记录并 fsync。目录不存在则创建（创建失败同样视为 journal 失败）。
 * 同步实现：写动作前一次性持久化，失败语义必须在执行前确定，异步反而引入窗口。
 */
export function appendJournalEntry(runtimeHome: string, entry: JournalEntry): void {
  let fd: number | undefined
  try {
    mkdirSync(journalDir(runtimeHome), { recursive: true })
    fd = openSync(journalFilePath(runtimeHome, entry.invocation_id), 'a')
    writeSync(fd, JSON.stringify(entry) + '\n')
    fsyncSync(fd)
  } catch (err) {
    throw new JournalWriteError(
      `journal 写入/fsync 失败（禁止执行该写操作）: ${err instanceof Error ? err.message : String(err)}`,
      err,
    )
  } finally {
    if (fd !== undefined) {
      try {
        closeSync(fd)
      } catch {
        // fd 已关闭/无效时忽略关闭错误（写入结果已由上方 try 决定）
      }
    }
  }
}
