/**
 * Office 三格式（docx/xlsx/pptx）预览共享常量与纯函数
 *
 * 设计文档：docs/research/frontend-office-preview-design.md §4.1 / §4.4 / §5.2
 * - 大小与行数门槛在打开预览之前判断（attachment.size 已知，无需先下载文件）
 * - DownloadFileCard.isPreviewable 与 AttachmentPreviewPanel.previewType 两处共用，
 *   不得在组件内散落魔法数字
 *
 * 注意：本文件不得静态引入 xlsx-js-style / pptx-preview / docx-preview
 * （三库一律动态 import 懒加载，是性能验收项，见设计 §5.1）。
 */

/** Excel（xlsx/xls）预览大小上限：10MB */
export const EXCEL_MAX_PREVIEW_BYTES = 10 * 1024 * 1024

/** Word / PPT 预览大小上限：20MB */
export const OFFICE_DOC_MAX_PREVIEW_BYTES = 20 * 1024 * 1024

/** 单个 sheet 最多渲染行数；超出只渲染前 N 行并显示截断提示 */
export const EXCEL_MAX_RENDER_ROWS = 1000

/** docx 整页缩放兜底页宽：A4 纵向（约 794px），section 内联宽度缺失时使用 */
export const DOCX_DEFAULT_PAGE_WIDTH = 794

/** docx 整页缩放兜底页高：A4 纵向（约 1123px），section 声明页高与实测高度都缺失时使用 */
export const DOCX_DEFAULT_PAGE_HEIGHT = 1123

/**
 * 解析 docx-preview 写入 section 的内联尺寸（如 "612pt"）为像素：
 * pt 按 96dpi 换算（×4/3），无单位/px 视为像素，无法解析返回 0。
 * 直接 parseFloat 会把 612pt 当 612px 用（真实渲染宽 816px），缩放会偏大导致溢出。
 */
export function parseInlineSizeToPx(value: string): number {
  const m = /^\s*([\d.]+)\s*(pt|px)?\s*$/i.exec(value)
  if (!m) return 0
  const n = parseFloat(m[1])
  if (!Number.isFinite(n)) return 0
  return (m[2] ?? 'px').toLowerCase() === 'pt' ? (n * 4) / 3 : n
}

/** 预览整页/整表缩放下限：容器极窄时不再继续缩小（缩到不可读），退回横向滚动 */
export const PREVIEW_FIT_SCALE_FLOOR = 0.3

/** 缩放比收口：上限 1（容器比内容大时不放大）、下限 PREVIEW_FIT_SCALE_FLOOR，千分位取整去掉浮点尾巴 */
function clampFitScale(raw: number): number {
  return Math.round(Math.min(1, Math.max(PREVIEW_FIT_SCALE_FLOOR, raw)) * 1000) / 1000
}

/**
 * 预览内容整表缩放比（Excel 表格用，宽度单约束，仿 PPT 模式）：
 * 保留内容原始尺寸与版式，按容器内容宽度等比缩放到整表可见。
 * 内容或容器宽度不可测（≤0，如测试环境）时不缩放。
 */
export function previewFitScale(containerContentWidth: number, contentWidth: number): number {
  if (contentWidth <= 0 || containerContentWidth <= 0) return 1
  return clampFitScale(containerContentWidth / contentWidth)
}

/**
 * docx 整页适配缩放比：宽、高哪个约束更紧就按哪个缩放（两维比值取 min），
 * 保证一页在预览面板内完整可见——宽屏矮面板按高度缩，窄面板按宽度缩；
 * 多页文档由容器垂直滚动逐页查看。
 * 某一维不可测（≤0，如 jsdom 测不出 clientHeight）时忽略该约束退回单维，
 * 两维都不可测时不缩放。上限/下限与 previewFitScale 一致。
 */
export function previewFitPageScale(
  containerContentWidth: number,
  containerContentHeight: number,
  pageWidth: number,
  pageHeight: number,
): number {
  const widthRaw = pageWidth > 0 && containerContentWidth > 0 ? containerContentWidth / pageWidth : Number.POSITIVE_INFINITY
  const heightRaw = pageHeight > 0 && containerContentHeight > 0 ? containerContentHeight / pageHeight : Number.POSITIVE_INFINITY
  if (widthRaw === Number.POSITIVE_INFINITY && heightRaw === Number.POSITIVE_INFINITY) return 1
  return clampFitScale(Math.min(widthRaw, heightRaw))
}

/** Office 预览类型（与面板 previewType 的 office 分支取值一致） */
export type OfficePreviewKind = 'docx' | 'excel' | 'pptx'

/**
 * 按扩展名/MIME 识别 Office 预览类型。
 * 旧格式 .doc / .ppt 返回 null（设计 §2.4：不支持预览，走下载引导）；
 * .xls 识别为 excel（设计 §2.4：尝试解析，失败提示下载）。
 */
export function detectOfficePreviewKind(mime: string, ext: string): OfficePreviewKind | null {
  if (mime.includes('wordprocessing') || ext === 'docx') return 'docx'
  if (mime.includes('spreadsheet') || ['xlsx', 'xls'].includes(ext)) return 'excel'
  if (mime.includes('presentation') || ext === 'pptx') return 'pptx'
  return null
}

/** 各 Office 类型的大小门槛（设计 §5.2：Excel 10MB，Word/PPT 20MB） */
export function officePreviewMaxBytes(kind: OfficePreviewKind): number {
  return kind === 'excel' ? EXCEL_MAX_PREVIEW_BYTES : OFFICE_DOC_MAX_PREVIEW_BYTES
}

/** SheetJS XLSX.utils.decode_range 返回结构的最小子集（行号 0-based） */
export interface SheetRowRange {
  s: { r: number }
  e: { r: number }
}

export interface TruncatedSheetRange<T extends SheetRowRange> {
  /** 原范围或截断后的范围（结尾行重写为 s.r + maxRows - 1） */
  range: T
  /** 截断前的真实总行数 */
  totalRows: number
  /** 是否发生截断 */
  truncated: boolean
}

/**
 * 单 sheet 行数门槛纯函数（设计 §5.2）：超过 maxRows 时把范围结尾行重写为
 * 起始行 + maxRows - 1，只渲染前 N 行。
 * 调用方负责 decode_range（调用前）与 encode_range（写回 !ref 时），
 * 本函数不接触 xlsx 库，保持可独立单测。
 */
export function truncateSheetRange<T extends SheetRowRange>(range: T, maxRows: number): TruncatedSheetRange<T> {
  const totalRows = range.e.r - range.s.r + 1
  if (totalRows <= maxRows) {
    return { range, totalRows, truncated: false }
  }
  const truncatedRange = { ...range, e: { ...range.e, r: range.s.r + maxRows - 1 } } as T
  return { range: truncatedRange, totalRows, truncated: true }
}
