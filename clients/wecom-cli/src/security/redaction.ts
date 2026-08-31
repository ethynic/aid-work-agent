/**
 * 日志/输出脱敏（不把手机号等敏感信息写入普通日志）。
 *
 * stderr 日志本就不含参数值与正文（只记 tool/run_id/code/effect/耗时），
 * 此模块作为兜底防线：所有写 stderr 的日志行先过 redactSensitive。
 */

/** 中国大陆手机号（11 位，1[3-9] 开头） */
const PHONE_RE = /1[3-9]\d{9}/g

export function redactSensitive(text: string): string {
  return text.replace(PHONE_RE, '1**********')
}
