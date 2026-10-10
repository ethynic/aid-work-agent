/**
 * detectFileIconKind 单测：文件类型图标判定
 *
 * 验证 AttachmentChip 与 DownloadFileCard 共用的图标种类识别：
 * 常见类型（word/excel/ppt/pdf/图片/md/txt 等）按扩展名与 MIME 各自独立可辨。
 */
import { describe, it, expect } from 'vitest'
import { detectFileIconKind } from '@/utils/file'

describe('detectFileIconKind', () => {
  it('Office 三格式与旧格式归入各自类型', () => {
    expect(detectFileIconKind('', '报告.docx')).toBe('word')
    expect(detectFileIconKind('', '旧格式.doc')).toBe('word')
    expect(detectFileIconKind('', '数据.xlsx')).toBe('excel')
    expect(detectFileIconKind('', '明细表.xls')).toBe('excel')
    expect(detectFileIconKind('', '汇报.pptx')).toBe('ppt')
    expect(detectFileIconKind('', '老ppt.ppt')).toBe('ppt')
  })

  it('pdf / 图片 / markdown / 文本 / 压缩包 / 网页', () => {
    expect(detectFileIconKind('', '手册.pdf')).toBe('pdf')
    expect(detectFileIconKind('', '截图.png')).toBe('image')
    expect(detectFileIconKind('image/jpeg', '照片')).toBe('image')
    expect(detectFileIconKind('', '说明.md')).toBe('markdown')
    expect(detectFileIconKind('', '日志.txt')).toBe('text')
    expect(detectFileIconKind('', '归档.zip')).toBe('archive')
    expect(detectFileIconKind('', '页面.html')).toBe('html')
  })

  it('MIME 优先于扩展名判断（无扩展名文件）', () => {
    expect(detectFileIconKind('application/pdf', '无扩展名')).toBe('pdf')
    expect(detectFileIconKind(
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '无扩展名',
    )).toBe('word')
    expect(detectFileIconKind('text/plain', '无扩展名')).toBe('text')
  })

  it('csv 按表格语义归入 excel；代码类归入 code', () => {
    expect(detectFileIconKind('text/csv', '导出.csv')).toBe('excel')
    expect(detectFileIconKind('', '脚本.py')).toBe('code')
    expect(detectFileIconKind('', '配置.json')).toBe('code')
  })

  it('未知类型归入 other', () => {
    expect(detectFileIconKind('', '文件.xyz')).toBe('other')
    expect(detectFileIconKind('application/octet-stream', '')).toBe('other')
  })
})
