/**
 * Token 格式化工具函数
 */

/**
 * 将原始 token 数转换为百万单位，保留2位小数
 * @param tokens 原始 token 数
 * @returns 格式化后的字符串，如 "1.23"
 */
export function formatTokensToMillions(tokens: number): string {
  if (tokens === undefined || tokens === null) return '0.00'
  const millions = tokens / 1_000_000
  return millions.toFixed(2)
}

/**
 * 将原始 token 数转换为百万单位，保留3位小数
 * @param tokens 原始 token 数
 * @returns 格式化后的字符串，如 "1.234"
 */
export function formatTokensToMillionsThreeDecimals(tokens: number): string {
  if (tokens === undefined || tokens === null) return '0.000'
  const millions = tokens / 1_000_000
  return millions.toFixed(3)
}

/**
 * 截取用户消息前10个字（中文字符）
 * @param message 用户消息
 * @returns 截取后的消息，如果消息为空则返回空字符串
 */
export function formatMessagePreview(message: string): string {
  if (!message) return ''
  // 去除换行和多余空格
  const trimmed = message.replace(/\s+/g, ' ').trim()
  // 截取前10个字符
  return trimmed.length > 10 ? trimmed.substring(0, 10) + '...' : trimmed
}

/**
 * 将 token 数格式化为易读的字符串（自动选择单位）
 * @param tokens 原始 token 数
 * @returns 格式化后的字符串，如 "1.23M", "456K", "789"
 */
export function formatTokensAuto(tokens: number): string {
  if (!tokens) return '0'
  if (tokens >= 1_000_000) return (tokens / 1_000_000).toFixed(2) + 'M'
  if (tokens >= 1_000) return (tokens / 1_000).toFixed(1) + 'K'
  return String(tokens)
}