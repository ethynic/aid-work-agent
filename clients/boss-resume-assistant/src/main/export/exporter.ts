/**
 * 评估+动作导出（Phase 8，设计文档 §12.2/§15）。
 * - 数据源：evaluations join candidates，附最新改判与该候选人最新动作
 * - CSV：标准转义（含逗号/引号/换行的字段加引号，引号双写）；带 BOM 便于 Excel 打开中文
 * - JSON：UTF-8 数组
 * 导出由用户主动触发；写文件路径由主进程 dialog 选择，本模块只产出内容。
 */
import type { Database as BetterSqliteDatabase } from 'better-sqlite3'

export interface ExportFilters {
  jobId?: number
  dateFrom?: string
  dateTo?: string
  conclusion?: string
  limit?: number
}

export interface ExportRow {
  candidateName: string | null
  fingerprint: string | null
  conclusion: string
  overrideConclusion: string | null
  overrideReason: string | null
  reason: string | null
  model: string | null
  evaluatedAt: string
  lastAction: string | null
  lastActionStatus: string | null
  lastActionAt: string | null
}

const CSV_HEADER = [
  '候选人',
  '指纹',
  '结论',
  '改判结论',
  '改判理由',
  '评估理由',
  '模型',
  '评估时间',
  '最近动作',
  '动作状态',
  '动作时间',
]

/** 校验日期串格式（与 AuditStore 同一口径：参数化已防注入，格式非法 fail-loud） */
function assertDate(v: string, label: string): void {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) {
    throw new Error(`${label} 日期格式必须为 YYYY-MM-DD: ${v}`)
  }
}

/** 查询导出数据（evaluations 为主，附最新改判+最新动作） */
export function queryExportRows(db: BetterSqliteDatabase, filters: ExportFilters = {}): ExportRow[] {
  const where: string[] = []
  const params: unknown[] = []
  if (filters.jobId !== undefined) {
    where.push('c.job_id = ?')
    params.push(filters.jobId)
  }
  if (filters.dateFrom) {
    assertDate(filters.dateFrom, '起始')
    where.push('e.created_at >= ?')
    params.push(`${filters.dateFrom} 00:00:00`)
  }
  if (filters.dateTo) {
    assertDate(filters.dateTo, '截止')
    where.push('e.created_at <= ?')
    params.push(`${filters.dateTo} 23:59:59`)
  }
  if (filters.conclusion) {
    where.push('e.conclusion = ?')
    params.push(filters.conclusion)
  }
  const limit = filters.limit ?? 5000
  const sql = `
    SELECT c.list_summary, c.fingerprint,
           e.conclusion, e.reason, e.model, e.created_at AS evaluated_at,
           o.override_conclusion, o.override_reason,
           a.action AS last_action, a.status AS last_action_status, a.created_at AS last_action_at
    FROM evaluations e
    LEFT JOIN candidates c ON c.id = e.candidate_id
    LEFT JOIN review_overrides o ON o.id = (
      SELECT id FROM review_overrides WHERE evaluation_id = e.id ORDER BY id DESC LIMIT 1
    )
    LEFT JOIN actions a ON a.id = (
      SELECT id FROM actions WHERE candidate_id = e.candidate_id ORDER BY id DESC LIMIT 1
    )
    ${where.length ? `WHERE ${where.join(' AND ')}` : ''}
    ORDER BY e.id DESC
    LIMIT ?`
  const rows = db.prepare(sql).all(...params, limit) as Array<Record<string, unknown>>
  return rows.map((r) => ({
    candidateName: extractName((r['list_summary'] as string | null) ?? null),
    fingerprint: (r['fingerprint'] as string | null) ?? null,
    conclusion: r['conclusion'] as string,
    overrideConclusion: (r['override_conclusion'] as string | null) ?? null,
    overrideReason: (r['override_reason'] as string | null) ?? null,
    reason: (r['reason'] as string | null) ?? null,
    model: (r['model'] as string | null) ?? null,
    evaluatedAt: r['evaluated_at'] as string,
    lastAction: (r['last_action'] as string | null) ?? null,
    lastActionStatus: (r['last_action_status'] as string | null) ?? null,
    lastActionAt: (r['last_action_at'] as string | null) ?? null,
  }))
}

/**
 * CSV 单元格转义：
 * 1. 公式注入防护：以 = + - @ 制表符开头的值前置单引号（Excel 文本标记，不显示），
 *    防止导出的 OCR/LLM/用户输入文本被 Excel 解析为公式（CSV injection）
 * 2. 含逗号/引号/换行/回车 → 双引号包裹且内部引号双写
 */
export function csvEscape(value: string | null): string {
  let v = value ?? ''
  if (/^[=+\-@\t]/.test(v)) {
    v = `'${v}`
  }
  if (/[",\n\r]/.test(v)) {
    return `"${v.replace(/"/g, '""')}"`
  }
  return v
}

/** 生成 CSV 文本（带 BOM，Excel 直接打开中文不乱码） */
export function buildCsv(rows: ExportRow[]): string {
  const lines = [CSV_HEADER.map(csvEscape).join(',')]
  for (const r of rows) {
    lines.push(
      [
        r.candidateName,
        r.fingerprint,
        r.conclusion,
        r.overrideConclusion,
        r.overrideReason,
        r.reason,
        r.model,
        r.evaluatedAt,
        r.lastAction,
        r.lastActionStatus,
        r.lastActionAt,
      ]
        .map(csvEscape)
        .join(','),
    )
  }
  // BOM + CRLF：Excel 兼容性
  return '\uFEFF' + lines.join('\r\n')
}

/** 生成 JSON 文本 */
export function buildJson(rows: ExportRow[]): string {
  return JSON.stringify(rows, null, 2)
}

function extractName(listSummary: string | null): string | null {
  if (!listSummary) return null
  try {
    const obj = JSON.parse(listSummary) as { name?: unknown }
    return typeof obj.name === 'string' && obj.name.trim() ? obj.name.trim() : null
  } catch {
    return null
  }
}
