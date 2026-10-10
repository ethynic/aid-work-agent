/**
 * officePreview 工具单测
 *
 * 验证（设计 §5.2 硬门槛 + §4.1/§4.2 类型识别）：
 * - 三个阈值为具名常量且取值正确（Excel 10MB / Word、PPT 20MB / 单 sheet 1000 行）
 * - truncateSheetRange 纯函数：不超门槛不截断；超门槛重写结尾行；起始行非 0 时的相对截断
 * - detectOfficePreviewKind：OOXML 三格式识别；.doc/.ppt 旧格式返回 null；.xls 识别为 excel
 */
import { describe, it, expect } from 'vitest'
import {
  EXCEL_MAX_PREVIEW_BYTES,
  OFFICE_DOC_MAX_PREVIEW_BYTES,
  EXCEL_MAX_RENDER_ROWS,
  DOCX_DEFAULT_PAGE_WIDTH,
  PREVIEW_FIT_SCALE_FLOOR,
  detectOfficePreviewKind,
  officePreviewMaxBytes,
  truncateSheetRange,
  previewFitScale,
} from '@/utils/officePreview'

const MB = 1024 * 1024

describe('officePreview 常量', () => {
  it('Excel 大小门槛为 10MB', () => {
    expect(EXCEL_MAX_PREVIEW_BYTES).toBe(10 * MB)
  })

  it('Word/PPT 大小门槛为 20MB', () => {
    expect(OFFICE_DOC_MAX_PREVIEW_BYTES).toBe(20 * MB)
  })

  it('单 sheet 渲染行数门槛为 1000 行', () => {
    expect(EXCEL_MAX_RENDER_ROWS).toBe(1000)
  })

  it('docx 整页缩放常量：A4 兜底页宽 794 / 缩放下限 0.3', () => {
    expect(DOCX_DEFAULT_PAGE_WIDTH).toBe(794)
    expect(PREVIEW_FIT_SCALE_FLOOR).toBe(0.3)
  })
})

describe('officePreviewMaxBytes', () => {
  it('excel 用 10MB 门槛，docx/pptx 用 20MB 门槛', () => {
    expect(officePreviewMaxBytes('excel')).toBe(EXCEL_MAX_PREVIEW_BYTES)
    expect(officePreviewMaxBytes('docx')).toBe(OFFICE_DOC_MAX_PREVIEW_BYTES)
    expect(officePreviewMaxBytes('pptx')).toBe(OFFICE_DOC_MAX_PREVIEW_BYTES)
  })
})

describe('detectOfficePreviewKind', () => {
  it('按扩展名识别 OOXML 三格式', () => {
    expect(detectOfficePreviewKind('', 'docx')).toBe('docx')
    expect(detectOfficePreviewKind('', 'xlsx')).toBe('excel')
    expect(detectOfficePreviewKind('', 'pptx')).toBe('pptx')
  })

  it('按 MIME 识别（文件可能无扩展名）', () => {
    expect(detectOfficePreviewKind('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'bin')).toBe('docx')
    expect(detectOfficePreviewKind('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'bin')).toBe('excel')
    expect(detectOfficePreviewKind('application/vnd.openxmlformats-officedocument.presentationml.presentation', 'bin')).toBe('pptx')
  })

  it('.xls 旧格式识别为 excel（尝试解析，失败提示下载，设计 §2.4）', () => {
    expect(detectOfficePreviewKind('application/vnd.ms-excel', 'xls')).toBe('excel')
  })

  it('.doc/.ppt 旧格式返回 null（不支持预览，设计 §2.4）', () => {
    expect(detectOfficePreviewKind('application/msword', 'doc')).toBeNull()
    expect(detectOfficePreviewKind('application/vnd.ms-powerpoint', 'ppt')).toBeNull()
    expect(detectOfficePreviewKind('', 'doc')).toBeNull()
    expect(detectOfficePreviewKind('', 'ppt')).toBeNull()
  })

  it('非 Office 格式返回 null', () => {
    expect(detectOfficePreviewKind('application/pdf', 'pdf')).toBeNull()
    expect(detectOfficePreviewKind('text/plain', 'txt')).toBeNull()
    expect(detectOfficePreviewKind('', 'zip')).toBeNull()
  })
})

describe('truncateSheetRange（!ref 重写纯函数）', () => {
  // SheetJS decode_range 语义：s/e 为 0-based {c, r}
  const range = (startRow: number, endRow: number) => ({
    s: { c: 0, r: startRow },
    e: { c: 8, r: endRow },
  })

  it('行数未超门槛：原样返回且不截断', () => {
    const r = range(0, 999) // 1000 行
    const result = truncateSheetRange(r, 1000)
    expect(result.truncated).toBe(false)
    expect(result.totalRows).toBe(1000)
    expect(result.range).toBe(r) // 同一引用，未新建副本
  })

  it('恰好等于门槛不截断（边界）', () => {
    const result = truncateSheetRange(range(0, 999), 1000)
    expect(result.truncated).toBe(false)
  })

  it('超过门槛：结尾行重写为 起始行 + maxRows - 1', () => {
    // A1:I1251 → 1251 行，截断后应只保留前 1000 行
    const result = truncateSheetRange(range(0, 1250), 1000)
    expect(result.truncated).toBe(true)
    expect(result.totalRows).toBe(1251)
    expect(result.range.e.r).toBe(999) // encode_range 后即 X1000 行
    expect(result.range.s).toEqual({ c: 0, r: 0 }) // 起始不变
    expect(result.range.e.c).toBe(8) // 列不收窄
  })

  it('起始行非首行时按相对行数截断', () => {
    // 数据从第 5 行开始（s.r=4）到 5004 行（e.r=5003），共 5000 行
    const result = truncateSheetRange(range(4, 5003), 1000)
    expect(result.truncated).toBe(true)
    expect(result.totalRows).toBe(5000)
    expect(result.range.e.r).toBe(4 + 1000 - 1) // 1003
  })

  it('门槛为 1 时只保留起始行', () => {
    const result = truncateSheetRange(range(0, 99), 1)
    expect(result.truncated).toBe(true)
    expect(result.range.e.r).toBe(0)
  })

  it('不修改传入的原 range 对象', () => {
    const r = range(0, 5000)
    truncateSheetRange(r, 1000)
    expect(r.e.r).toBe(5000) // 原对象未被改写
  })
})

describe('previewFitScale（整页/整表缩放，仿 PPT 模式）', () => {
  it('容器比页窄时按比例缩放到整页可见', () => {
    expect(previewFitScale(458, 794)).toBe(0.577) // 约 490px 面板
    expect(previewFitScale(343, 794)).toBe(0.432) // 手机宽度面板
  })

  it('容器比页宽时不放大（上限 1）', () => {
    expect(previewFitScale(1068, 794)).toBe(1)
    expect(previewFitScale(794, 794)).toBe(1)
  })

  it('极窄容器不再继续缩小（下限 0.3，退回横向滚动）', () => {
    expect(previewFitScale(150, 794)).toBe(0.3)
  })

  it('页宽或容器宽度不可测（≤0）时不缩放', () => {
    expect(previewFitScale(458, 0)).toBe(1)
    expect(previewFitScale(0, 794)).toBe(1)
    expect(previewFitScale(-32, 794)).toBe(1) // jsdom 默认 clientWidth 0
  })
})
