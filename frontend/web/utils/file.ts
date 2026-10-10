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

/** 文件类型图标种类：决定 FileTypeIcon 渲染哪种徽标 */
export type FileIconKind =
  | 'word'
  | 'excel'
  | 'ppt'
  | 'pdf'
  | 'image'
  | 'markdown'
  | 'text'
  | 'code'
  | 'html'
  | 'archive'
  | 'other'

/**
 * 按扩展名/MIME 识别文件类型图标种类。
 * 供 AttachmentChip（消息附件标签）与 DownloadFileCard（AI 产出文件卡片）
 * 共用，保证同一文件在两个入口的图标一致。
 */
export function detectFileIconKind(mime: string, name: string): FileIconKind {
  const ext = name.includes('.') ? name.split('.').pop()!.toLowerCase() : ''
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(ext)) return 'image'
  if (mime.includes('pdf') || ext === 'pdf') return 'pdf'
  if (mime.includes('wordprocessing') || ['doc', 'docx'].includes(ext)) return 'word'
  if (mime.includes('presentation') || ['ppt', 'pptx'].includes(ext)) return 'ppt'
  // CSV 按 Excel 呈现（表格语义，用户心智里用 Excel 打开）
  if (mime.includes('spreadsheet') || ['xls', 'xlsx', 'csv'].includes(ext)) return 'excel'
  if (['md', 'markdown'].includes(ext)) return 'markdown'
  if (mime === 'text/html' || ['html', 'htm'].includes(ext)) return 'html'
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return 'archive'
  if (mime.startsWith('text/plain') || ['txt', 'log'].includes(ext)) return 'text'
  if (mime.startsWith('text/') || ['json', 'js', 'ts', 'py', 'vue', 'css', 'xml', 'yaml', 'yml', 'sh', 'bat', 'sql'].includes(ext)) return 'code'
  return 'other'
}
