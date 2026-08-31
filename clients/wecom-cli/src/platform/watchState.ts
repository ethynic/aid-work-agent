/**
 * wecom_watch_poll 的会话水位状态文件（%LOCALAPPDATA%\AidWorkAgent\wecom-cli\watch-state.json）。
 *
 * 结构：{ sessions: { <会话名>: { last_seen_text_norm, last_unread, updated_at } } }
 * - last_seen_text_norm：上次成功读取时最后一行消息的归一化文本（与 chat_ocr.py
 *   normalize_text 同规则），下一轮据此取增量；
 * - last_unread：上一轮到该会话的未读角标数（diff 依据：增大或新出现 → 候选）；
 *   读取成功后归零（进会话已清角标，之后出现的任何角标都是新消息）；
 *   会话从快照消失时归零（否则「读完再来 1 条」会被 last_unread 旧值压住漏报）。
 *
 * 容错：文件缺失/损坏一律按空状态处理（首轮全量基线），绝不因状态文件崩溃；
 * 写入走 tmp + rename 原子替换，避免半截文件。
 */
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'

export interface WatchSessionState {
  last_seen_text_norm: string
  last_unread: number
  /** ISO 时间戳 */
  updated_at: string
}

export interface WatchState {
  sessions: Record<string, WatchSessionState>
}

/** watch-state.json 路径（与 target_ref 密钥同目录）；%LOCALAPPDATA% 缺失返回 null */
export function watchStatePath(localAppData: string | undefined = process.env.LOCALAPPDATA): string | null {
  if (!localAppData) return null
  return join(localAppData, 'AidWorkAgent', 'wecom-cli', 'watch-state.json')
}

function isValidSessionState(v: unknown): v is WatchSessionState {
  if (v === null || typeof v !== 'object') return false
  const s = v as Record<string, unknown>
  return (
    typeof s.last_seen_text_norm === 'string' &&
    typeof s.last_unread === 'number' &&
    Number.isFinite(s.last_unread) &&
    typeof s.updated_at === 'string'
  )
}

/** 读取状态；缺失/非法 JSON/结构不符 → 空状态（容错，首轮全量基线） */
export function loadWatchState(path: string): WatchState {
  try {
    if (!existsSync(path)) return { sessions: {} }
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as unknown
    if (parsed === null || typeof parsed !== 'object') return { sessions: {} }
    const rawSessions = (parsed as { sessions?: unknown }).sessions
    if (rawSessions === null || typeof rawSessions !== 'object') return { sessions: {} }
    const sessions: Record<string, WatchSessionState> = {}
    for (const [name, v] of Object.entries(rawSessions as Record<string, unknown>)) {
      // 逐条校验：单条损坏只丢该会话水位（退化为全量），不拖垮整个状态
      if (isValidSessionState(v)) sessions[name] = v
    }
    return { sessions }
  } catch {
    return { sessions: {} }
  }
}

/** 原子写入（tmp + rename）；目录自动创建 */
export function saveWatchState(path: string, state: WatchState): void {
  mkdirSync(dirname(path), { recursive: true })
  const tmp = `${path}.${process.pid}.tmp`
  writeFileSync(tmp, JSON.stringify(state, null, 2), 'utf8')
  renameSync(tmp, path)
}
