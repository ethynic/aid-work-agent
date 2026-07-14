/**
 * 文件大小格式化：支持 B/KB/MB/GB，log 算法。
 * 空值或 0 返回 '0 B'。
 */
export function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  const idx = Math.min(i, units.length - 1)
  const size = (bytes / Math.pow(1024, idx)).toFixed(idx === 0 ? 0 : 1)
  return `${size} ${units[idx]}`
}
