/**
 * 积分格式化工具函数
 * 统一格式化为固定 2 位小数 + 整数千分位逗号（如 1,234.50、0.05、0.00）
 *
 * 后端 NUMERIC(12,2) 经 psycopg3 加载为 Decimal，FastAPI 默认 JSON 编码器
 * 可能将其序列化为字符串。formatCredit 内部用 parseFloat 兜底，兼容数字与字符串两种入参。
 */

export function formatCredit(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '0.00'
  const num = typeof value === 'string' ? parseFloat(value) : Number(value)
  if (isNaN(num)) return '0.00'
  return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
