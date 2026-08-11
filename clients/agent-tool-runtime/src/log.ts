/**
 * stderr 日志（脱敏）。
 *
 * 硬约束：绝不输出 device_token / claim_token / 配对码 / 简历内容。
 * 调用方约定不把敏感值拼进消息；redact() 是兜底（长 hex/base64 串打码）。
 */

/** 兜底脱敏：32 位以上的 hex / base64 长串打码（token 形态）；UUID（含连字符）不算敏感，放行 */
export function redact(message: string): string {
  return message.replace(/[A-Za-z0-9+/=_-]{32,}/g, (m) => {
    if (/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(m)) return m
    return m.slice(0, 6) + '***'
  })
}

export function logInfo(message: string): void {
  process.stderr.write(`[runtime] ${redact(message)}\n`)
}

export function logError(message: string): void {
  process.stderr.write(`[runtime][ERROR] ${redact(message)}\n`)
}
