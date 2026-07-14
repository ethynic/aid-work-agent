/**
 * 日期工具函数。
 *
 * 后端 PostgreSQL TIMESTAMP 返回本地时间字符串（无 Z 后缀），
 * 直接 new Date(str) 即可，禁止拼接 'Z' 后缀（会多出 8 小时）。
 * 详见 .claude/rules/frontend_dev.md「时间显示规范」。
 */

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** 短日期时间：M/D HH:mm（月/日不补零，时分补零）。空值返 '-'。 */
export function formatShortDateTime(d: string | null | undefined): string {
  if (!d) return '-'
  const date = new Date(d)
  if (isNaN(date.getTime())) return '-'
  return `${date.getMonth() + 1}/${date.getDate()} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

/** 标准日期时间：YYYY-MM-DD HH:mm（无秒）。空值返 '-'。 */
export function formatDateTime(d: string | null | undefined): string {
  if (!d) return '-'
  const date = new Date(d)
  if (isNaN(date.getTime())) return '-'
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}

/** 带秒日期时间：YYYY-MM-DD HH:mm:ss。空值返 '-'。 */
export function formatDateTimeWithSeconds(d: string | null | undefined): string {
  if (!d) return '-'
  const date = new Date(d)
  if (isNaN(date.getTime())) return '-'
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
}

/**
 * 字符串截取到分钟：直接对 ISO 字符串 replace('T',' ').slice(0,16)。
 * 不经过 Date 解析，避免任何时区影响。空值返 '-'。
 */
export function formatTimestampToMinute(t: string | null | undefined): string {
  if (!t) return '-'
  return String(t).replace('T', ' ').slice(0, 16)
}
