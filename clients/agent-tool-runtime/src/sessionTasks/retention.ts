/**
 * 会话任务本地保留与磁盘上限（C2，设计 §8）。
 *
 * - 本地存储上限默认 256 MiB（session-tasks 目录，不含全局 journal）：
 *   ≥80% 告警；≥100% 停止新观察持久化/新发送（引擎据此暂停），不删除未 ACK 数据。
 * - 终态且全部 ACK、无未决 journal 的 assignment 日志保留 7 天后可删；
 *   运行中 / unknown 不按年龄清理。
 * - 删除路径必须解析后仍在 session-tasks 目录内（安全路径检查，防穿越）。
 */
import { mkdirSync, readdirSync, rmSync, statSync, existsSync } from 'node:fs'
import { join, resolve, sep } from 'node:path'
import { SESSION_TASKS_DIR } from './sessionStore.js'

export const RETENTION_DEFAULT_MAX_BYTES = 256 * 1024 * 1024
export const RETENTION_WARN_RATIO = 0.8
export const RETENTION_DEFAULT_KEEP_DAYS = 7

export interface AssignmentRetentionState {
  assignmentId: string
  /** 云端终态（completed/stopped） */
  terminal: boolean
  /** 本地日志全部 ACK（lastLocalSeq == ackedLocalSeq） */
  fullyAcked: boolean
  /** 无未决 may_have_started 写 journal（引擎/调用方判定） */
  noPendingJournal: boolean
  /** 最后活动时间（ms epoch；终态判定起点） */
  lastActivityAt: number
}

export interface RetentionResult {
  usedBytes: number
  warn80: boolean
  /** true = 已达 100%：调用方（引擎）必须停新观察持久化与新发送 */
  stopNew: boolean
  deletedAssignmentIds: string[]
}

export function sessionTasksRoot(runtimeHome: string): string {
  return resolve(runtimeHome, SESSION_TASKS_DIR)
}

/** 目录磁盘占用（字节；目录不存在为 0） */
export function sessionTasksDiskUsage(runtimeHome: string): number {
  const root = sessionTasksRoot(runtimeHome)
  if (!existsSync(root)) return 0
  let total = 0
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else total += statSync(full).size
    }
  }
  walk(root)
  return total
}

/** 安全路径检查：child 解析后必须位于 parent 目录内（拒绝 .. 与绝对路径穿越） */
export function isSafeChild(parent: string, child: string): boolean {
  const p = resolve(parent)
  const c = resolve(child)
  return c === p || c.startsWith(p + sep)
}

/**
 * 执行保留策略：先删过期终态 assignment 目录，再统计占用并产出告警/停止标志。
 * 不删除任何未 ACK / 运行中 / unknown 数据。
 */
export function enforceRetention(
  runtimeHome: string,
  states: AssignmentRetentionState[],
  opts: { maxBytes?: number; keepDays?: number; now?: number } = {},
): RetentionResult {
  const maxBytes = opts.maxBytes ?? RETENTION_DEFAULT_MAX_BYTES
  const keepDays = opts.keepDays ?? RETENTION_DEFAULT_KEEP_DAYS
  const now = opts.now ?? Date.now()
  const root = sessionTasksRoot(runtimeHome)
  const deleted: string[] = []
  for (const s of states) {
    if (!s.terminal || !s.fullyAcked || !s.noPendingJournal) continue
    if (now - s.lastActivityAt < keepDays * 86_400_000) continue
    const dir = join(root, s.assignmentId)
    if (!isSafeChild(root, dir)) continue
    if (existsSync(dir)) {
      rmSync(dir, { recursive: true, force: true })
      deleted.push(s.assignmentId)
    }
  }
  mkdirSync(root, { recursive: true })
  const usedBytes = sessionTasksDiskUsage(runtimeHome)
  return {
    usedBytes,
    warn80: usedBytes >= maxBytes * RETENTION_WARN_RATIO,
    stopNew: usedBytes >= maxBytes,
    deletedAssignmentIds: deleted,
  }
}
