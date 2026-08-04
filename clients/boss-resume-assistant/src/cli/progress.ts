/**
 * CLI 进度渲染（纯函数，可单测）。
 * 把 ScreeningSession 的事件流转成单行终端输出，不做任何 IO。
 */
import type { SessionEvent, SessionStats } from '../main/workflow/ScreeningSession.js'

/** 事件时间戳（ISO）→ HH:MM:SS */
function clockOf(at: string): string {
  const t = at.match(/T(\d{2}:\d{2}:\d{2})/)
  return t?.[1] ?? at
}

/** 状态机事件 → 单行进度文本 */
export function renderEventLine(event: SessionEvent): string {
  const clock = clockOf(event.at)
  if (event.type === 'state') {
    const suffix = event.message ? ` — ${event.message}` : ''
    return `[${clock}] 状态 → ${event.state ?? '?'}${suffix}`
  }
  if (event.type === 'candidate') {
    return `[${clock}] 候选人 ${event.candidateName ?? '?'} → ${event.conclusion ?? '?'}`
  }
  // log
  const level = event.level ?? 'info'
  return `[${clock}] ${level}: ${event.message ?? ''}`
}

/** 会话统计 → 单行汇总（状态切换 / 候选人结论后打印） */
export function renderStatsLine(stats: SessionStats): string {
  return (
    `[统计] 已看 ${stats.viewed}｜合格 ${stats.qualified}｜不合格 ${stats.rejected}` +
    `｜待复核 ${stats.uncertain}｜动作 ${stats.actionsAttempted}`
  )
}
