/**
 * CLI 人工复核静态 HTML 报告（Phase 9，设计文档 §16 决策 6）。
 * - 数据源：reviewStore 队列（UNCERTAIN）+ resume_views（结构化摘要/OCR markdown）+ captures（长图路径）
 * - 长图以 file:// 绝对路径 <img> 引用（本地文件，报告与截图同机使用）
 * - 纯字符串拼装，无模板依赖；所有文本经 HTML 转义，防 OCR/LLM 内容注入
 */
import fs from 'node:fs'
import path from 'node:path'
import type { Database as BetterSqliteDatabase } from 'better-sqlite3'
import { ReviewStore, type ReviewItem } from '../main/storage/reviewStore.js'
import type { Evidence } from '../main/screening/ScreeningEngine.js'

/** 单个候选人的报告视图模型 */
export interface ReviewReportEntry {
  item: ReviewItem
  /** candidates.list_summary 原始 JSON 文本 */
  listSummary: string | null
  /** resume_views.summary_json（结构化摘要） */
  structuredSummary: string | null
  /** resume_views.ocr_markdown（OCR 归一化 markdown） */
  ocrMarkdown: string | null
  /** 长图本地绝对路径（captures kind='long'，取最新一次查看） */
  longImagePath: string | null
  /** 解析后的证据列表（解析失败为空数组） */
  evidence: Evidence[]
}

/** HTML 转义（报告内容是 OCR/LLM 输出，必须转义） */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 本地绝对路径 → file:// URL（Windows 反斜杠转正斜杠） */
export function fileUrl(absPath: string): string {
  return 'file:///' + path.resolve(absPath).replace(/\\/g, '/')
}

interface ResumeViewRow {
  summary_json: string | null
  ocr_markdown: string | null
  id: number
}

/** 汇总一个复核条目所需的全部素材（该候选人最新一次 resume_view + 长图） */
export function collectReportEntries(db: BetterSqliteDatabase, items: ReviewItem[]): ReviewReportEntry[] {
  return items.map((item) => {
    let view: ResumeViewRow | undefined
    if (item.candidateId !== null) {
      view = db
        .prepare(
          `SELECT id, summary_json, ocr_markdown FROM resume_views
           WHERE candidate_id = ? ORDER BY id DESC LIMIT 1`,
        )
        .get(item.candidateId) as ResumeViewRow | undefined
    }
    let longImagePath: string | null = null
    if (view) {
      const cap = db
        .prepare(
          `SELECT path FROM captures WHERE resume_view_id = ? AND kind = 'long' ORDER BY id DESC LIMIT 1`,
        )
        .get(view.id) as { path: string | null } | undefined
      longImagePath = cap?.path ?? null
    }
    let listSummary: string | null = null
    if (item.candidateId !== null) {
      const c = db.prepare('SELECT list_summary FROM candidates WHERE id = ?').get(item.candidateId) as
        | { list_summary: string | null }
        | undefined
      listSummary = c?.list_summary ?? null
    }
    let evidence: Evidence[] = []
    if (item.evidence) {
      try {
        const parsed = JSON.parse(item.evidence) as unknown
        if (Array.isArray(parsed)) evidence = parsed as Evidence[]
      } catch {
        // 证据 JSON 损坏：报告里展示原始文本，不 fail（报告生成不应被单条脏数据阻断）
      }
    }
    return {
      item,
      listSummary,
      structuredSummary: view?.summary_json ?? null,
      ocrMarkdown: view?.ocr_markdown ?? null,
      longImagePath,
      evidence,
    }
  })
}

function renderEvidenceTable(evidence: Evidence[]): string {
  if (evidence.length === 0) return '<p class="muted">无证据记录</p>'
  const rows = evidence
    .map(
      (ev) =>
        `<tr><td>${escapeHtml(ev.field)}</td><td>${escapeHtml(ev.rule)}</td>` +
        `<td class="result-${escapeHtml(ev.result)}">${escapeHtml(ev.result)}</td>` +
        `<td>${escapeHtml(ev.detail ?? '')}</td></tr>`,
    )
    .join('\n')
  return (
    '<table><thead><tr><th>字段</th><th>规则</th><th>结果</th><th>详情</th></tr></thead>' +
    `<tbody>${rows}</tbody></table>`
  )
}

function prettyJson(raw: string | null): string {
  if (!raw) return ''
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function renderEntry(entry: ReviewReportEntry): string {
  const { item } = entry
  const name = item.candidateName ?? `候选人#${item.candidateId ?? '?'}`
  const image = entry.longImagePath
    ? `<img class="long-img" src="${escapeHtml(fileUrl(entry.longImagePath))}" alt="简历长图" loading="lazy" />`
    : '<p class="muted">无长图</p>'
  const overrideBadge = item.override
    ? `<p class="override">已改判：${escapeHtml(item.override.overrideConclusion)}（${escapeHtml(item.override.overrideReason)}）</p>`
    : ''
  return `
<section class="card">
  <h2>#${item.evaluationId} ${escapeHtml(name)} <span class="badge">${escapeHtml(item.conclusion)}</span></h2>
  <p class="muted">评估时间：${escapeHtml(item.createdAt)}｜模型：${escapeHtml(item.model ?? '无')}</p>
  <p><strong>结论理由：</strong>${escapeHtml(item.reason ?? '无')}</p>
  ${overrideBadge}
  <h3>证据</h3>
  ${renderEvidenceTable(entry.evidence)}
  <h3>列表摘要</h3>
  <pre>${escapeHtml(prettyJson(entry.listSummary) || '无')}</pre>
  <h3>结构化摘要</h3>
  <pre>${escapeHtml(prettyJson(entry.structuredSummary) || '无')}</pre>
  <h3>OCR 归一化 Markdown</h3>
  <pre class="ocr">${escapeHtml(entry.ocrMarkdown ?? '无（该简历未完成 OCR 归一化或历史数据未落库）')}</pre>
  <h3>简历长图</h3>
  ${image}
</section>`
}

/** 生成完整 HTML 文档 */
export function buildReviewHtml(entries: ReviewReportEntry[], generatedAt: Date): string {
  const body = entries.length
    ? entries.map(renderEntry).join('\n')
    : '<p class="muted">复核队列为空，没有 UNCERTAIN 待处理项。</p>'
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>BOSS 简历人工复核报告</title>
<style>
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; margin: 24px; background: #f5f6f8; color: #24292f; }
  h1 { font-size: 20px; }
  .card { background: #fff; border: 1px solid #d8dbe0; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; }
  .card h2 { font-size: 16px; margin-top: 0; }
  .card h3 { font-size: 13px; color: #57606a; margin-bottom: 6px; }
  .badge { background: #fff3cd; color: #7a5b00; border-radius: 4px; padding: 2px 8px; font-size: 12px; }
  .override { background: #e7f5e9; border: 1px solid #b7e2c0; border-radius: 4px; padding: 6px 10px; }
  .muted { color: #8a9199; }
  pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 6px; padding: 10px; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
  pre.ocr { max-height: 400px; overflow-y: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; }
  th, td { border: 1px solid #d8dbe0; padding: 6px 8px; text-align: left; }
  th { background: #f0f2f5; }
  .result-PASS { color: #1a7f37; }
  .result-FAIL { color: #cf222e; }
  .result-UNDECIDED { color: #9a6700; }
  .long-img { max-width: 480px; border: 1px solid #d8dbe0; border-radius: 6px; display: block; }
</style>
</head>
<body>
<h1>BOSS 简历人工复核报告</h1>
<p class="muted">生成时间：${escapeHtml(generatedAt.toLocaleString('zh-CN'))}｜待复核条目：${entries.length}</p>
${body}
</body>
</html>
`
}

export interface GenerateReportResult {
  /** 报告文件绝对路径 */
  reportPath: string
  /** 条目数 */
  count: number
}

/**
 * 生成复核报告并写盘。
 * @param outDir 输出目录（默认 ./exports）
 * @param includeResolved true 时连同已改判的一起导出
 */
export function generateReviewReport(
  db: BetterSqliteDatabase,
  opts: { outDir: string; includeResolved?: boolean },
): GenerateReportResult {
  const store = new ReviewStore(db)
  const items = store.list({ includeResolved: opts.includeResolved ?? false, limit: 500 })
  const entries = collectReportEntries(db, items)
  const html = buildReviewHtml(entries, new Date())
  fs.mkdirSync(opts.outDir, { recursive: true })
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const reportPath = path.join(opts.outDir, `review-report-${stamp}.html`)
  fs.writeFileSync(reportPath, html, 'utf8')
  return { reportPath: path.resolve(reportPath), count: entries.length }
}
