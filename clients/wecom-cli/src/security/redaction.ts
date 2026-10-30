/**
 * 日志/输出脱敏（不把手机号等敏感信息写入普通日志）。
 *
 * stderr 日志本就不含参数值与正文（只记 tool/run_id/code/effect/耗时），
 * 此模块作为兜底防线：所有写 stderr 的日志行先过 redactSensitive。
 */

/** 中国大陆手机号（11 位，1[3-9] 开头） */
const PHONE_RE = /1[3-9]\d{9}/g

/**
 * 32 位 hex 串兜底脱敏（设计 §8：驱动输出与日志中的密钥样式串强制脱敏）。
 * wxaes128 主密钥 hex 形态恰为 32 hex 字符；协议载荷字段（如 keyscan 的 key_hex）由
 * 驱动侧受控放行，其余位置出现的一律按疑似密钥遮蔽（兜底防线，宁可误伤不漏放）。
 * 先于手机号规则执行：防止手机号规则改写 hex 串内部的数字段。
 */
const HEX32_RE = /[0-9a-fA-F]{32}/g

export function redactSensitive(text: string): string {
  return text.replace(HEX32_RE, '[REDACTED-HEX]').replace(PHONE_RE, '1**********')
}
