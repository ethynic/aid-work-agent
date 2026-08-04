/**
 * 动作持久化（设计文档 §10/§11/§12.1 actions 表）。
 * - 幂等唯一键：unique_key = candidate_fingerprint|action_type（migration v2 有 UNIQUE 索引）
 * - 状态机：PLANNED → SENT → CONFIRMED / UNKNOWN / FAILED
 * - 重启恢复：listPlanned() 取出崩溃残留的 PLANNED 记录，由调用方重新走完整前置校验
 *
 * 只负责 DB 读写，不做任何 CDP 调用。
 */
import type { Database as BetterSqliteDatabase } from 'better-sqlite3'

/** 可执行动作类型（NO_ACTION 不落 actions 表，无需执行） */
export type ExecutableActionType = 'GREET' | 'REJECT'

export type ActionStatus = 'PLANNED' | 'SENT' | 'CONFIRMED' | 'UNKNOWN' | 'FAILED'

/** 视为"已发出/可能已发出"的状态：这些状态的记录禁止再次执行（UNKNOWN 永不自动重试） */
export const NON_RETRYABLE_STATUSES: readonly ActionStatus[] = ['SENT', 'CONFIRMED', 'UNKNOWN']

export interface ActionRecord {
  id: number
  candidateId: number | null
  sessionId: number | null
  action: string
  reason: string | null
  status: ActionStatus
  uniqueKey: string | null
  beforeScreenshotPath: string | null
  afterScreenshotPath: string | null
  error: string | null
  createdAt: string
  sentAt: string | null
  confirmedAt: string | null
}

interface ActionRow {
  id: number
  candidate_id: number | null
  session_id: number | null
  action: string
  reason: string | null
  status: string
  unique_key: string | null
  before_screenshot_path: string | null
  after_screenshot_path: string | null
  error: string | null
  created_at: string
  sent_at: string | null
  confirmed_at: string | null
}

function toRecord(row: ActionRow): ActionRecord {
  return {
    id: row.id,
    candidateId: row.candidate_id,
    sessionId: row.session_id,
    action: row.action,
    reason: row.reason,
    status: row.status as ActionStatus,
    uniqueKey: row.unique_key,
    beforeScreenshotPath: row.before_screenshot_path,
    afterScreenshotPath: row.after_screenshot_path,
    error: row.error,
    createdAt: row.created_at,
    sentAt: row.sent_at,
    confirmedAt: row.confirmed_at,
  }
}

export class ActionStore {
  constructor(private readonly db: BetterSqliteDatabase) {}

  /** 幂等唯一键：候选人指纹 + 动作类型 */
  static makeUniqueKey(candidateFingerprint: string, action: ExecutableActionType): string {
    return `${candidateFingerprint}|${action}`
  }

  getById(id: number): ActionRecord | undefined {
    const row = this.db.prepare('SELECT * FROM actions WHERE id = ?').get(id) as ActionRow | undefined
    return row ? toRecord(row) : undefined
  }

  findByUniqueKey(uniqueKey: string): ActionRecord | undefined {
    const row = this.db.prepare('SELECT * FROM actions WHERE unique_key = ?').get(uniqueKey) as
      | ActionRow
      | undefined
    return row ? toRecord(row) : undefined
  }

  /**
   * 插入 PLANNED 记录；unique_key 冲突时返回已有记录（幂等）。
   * @returns record + inserted（false 表示命中已有记录，未重复插入）
   */
  getOrInsertPlanned(input: {
    candidateId: number | null
    sessionId: number | null
    action: ExecutableActionType
    reason: string | null
    uniqueKey: string
  }): { record: ActionRecord; inserted: boolean } {
    try {
      const info = this.db
        .prepare(
          `INSERT INTO actions (candidate_id, session_id, action, reason, status, unique_key)
           VALUES (?, ?, ?, ?, 'PLANNED', ?)`,
        )
        .run(input.candidateId, input.sessionId, input.action, input.reason, input.uniqueKey)
      const record = this.getById(Number(info.lastInsertRowid))
      if (!record) throw new Error('insert planned action failed: row not found after insert')
      return { record, inserted: true }
    } catch (e) {
      if (e instanceof Error && /UNIQUE/i.test(e.message)) {
        const existing = this.findByUniqueKey(input.uniqueKey)
        if (!existing) throw e // 冲突却查不到，fail-loud
        return { record: existing, inserted: false }
      }
      throw e
    }
  }

  /** FAILED / 崩溃残留 PLANNED 记录重新执行前重置回 PLANNED */
  resetToPlanned(id: number): void {
    this.db
      .prepare(`UPDATE actions SET status = 'PLANNED', error = NULL, sent_at = NULL, confirmed_at = NULL WHERE id = ?`)
      .run(id)
  }

  setBeforeScreenshot(id: number, path: string): void {
    this.db.prepare('UPDATE actions SET before_screenshot_path = ? WHERE id = ?').run(path, id)
  }

  setAfterScreenshot(id: number, path: string): void {
    this.db.prepare('UPDATE actions SET after_screenshot_path = ? WHERE id = ?').run(path, id)
  }

  markSent(id: number): void {
    this.db
      .prepare(`UPDATE actions SET status = 'SENT', sent_at = CURRENT_TIMESTAMP WHERE id = ?`)
      .run(id)
  }

  markConfirmed(id: number, afterScreenshotPath: string | null): void {
    this.db
      .prepare(
        `UPDATE actions SET status = 'CONFIRMED', confirmed_at = CURRENT_TIMESTAMP, after_screenshot_path = ? WHERE id = ?`,
      )
      .run(afterScreenshotPath, id)
  }

  /** UNKNOWN：结果无法确认，永不自动重试 */
  markUnknown(id: number, error: string): void {
    this.db.prepare(`UPDATE actions SET status = 'UNKNOWN', error = ? WHERE id = ?`).run(error, id)
  }

  markFailed(id: number, error: string): void {
    this.db.prepare(`UPDATE actions SET status = 'FAILED', error = ? WHERE id = ?`).run(error, id)
  }

  /**
   * 会话级动作尝试计数（仅计已发出的：SENT/CONFIRMED/UNKNOWN）。
   * PLANNED/FAILED 未向页面发出输入，不计入。
   */
  countAttemptsBySession(sessionId: number): number {
    const row = this.db
      .prepare(
        `SELECT COUNT(*) AS c FROM actions
         WHERE session_id = ? AND status IN ('SENT', 'CONFIRMED', 'UNKNOWN')`,
      )
      .get(sessionId) as { c: number }
    return row.c
  }

  /** 当日动作尝试计数（SQLite CURRENT_TIMESTAMP 为 UTC，start of day 同口径） */
  countDailyAttempts(): number {
    const row = this.db
      .prepare(
        `SELECT COUNT(*) AS c FROM actions
         WHERE status IN ('SENT', 'CONFIRMED', 'UNKNOWN') AND created_at >= datetime('now', 'start of day')`,
      )
      .get() as { c: number }
    return row.c
  }

  /** 重启恢复：崩溃残留的 PLANNED 记录（未发出，需重新走完整前置校验） */
  listPlanned(): ActionRecord[] {
    const rows = this.db
      .prepare(`SELECT * FROM actions WHERE status = 'PLANNED' ORDER BY id`)
      .all() as ActionRow[]
    return rows.map(toRecord)
  }
}
