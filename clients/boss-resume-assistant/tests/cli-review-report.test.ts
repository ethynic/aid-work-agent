import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import BetterSqliteDatabase from 'better-sqlite3'
import type { Database as BetterSqliteDatabaseType } from 'better-sqlite3'
import { runMigrations } from '../db/migrations.js'
import {
  buildReviewHtml,
  collectReportEntries,
  escapeHtml,
  fileUrl,
  generateReviewReport,
} from '../src/cli/reviewReport.js'
import { ReviewStore } from '../src/main/storage/reviewStore.js'

function freshDb(): BetterSqliteDatabaseType {
  const db = new BetterSqliteDatabase(':memory:')
  runMigrations(db)
  return db
}

/** 种一条 UNCERTAIN 候选人全链路数据（候选人+查看+长图+评估+证据） */
function seedUncertain(db: BetterSqliteDatabaseType, name: string): { evaluationId: number; longPath: string } {
  db.prepare('INSERT INTO jobs (name) VALUES (?)').run('岗位A')
  const c = db
    .prepare('INSERT INTO candidates (job_id, fingerprint, list_summary) VALUES (1, ?, ?)')
    .run(`fp-${name}`, JSON.stringify({ name }))
  const candidateId = Number(c.lastInsertRowid)
  const rv = db
    .prepare(
      `INSERT INTO resume_views (job_id, candidate_id, source, completeness, summary_json, ocr_markdown)
       VALUES (1, ?, 'OCR_NORMALIZED', 'COMPLETE_SUMMARY', ?, ?)`,
    )
    .run(candidateId, JSON.stringify({ baseInfo: { name } }), `# 简历\n${name} 的 OCR markdown`)
  const viewId = Number(rv.lastInsertRowid)
  const longPath = path.join(os.tmpdir(), `long-${name}.png`)
  db.prepare("INSERT INTO captures (resume_view_id, kind, path, integrity_status) VALUES (?, 'long', ?, 'OK')").run(
    viewId,
    longPath,
  )
  const evidence = JSON.stringify([{ field: 'city', rule: '期望城市须为：杭州', result: 'UNDECIDED', detail: '无城市字段' }])
  const ev = db
    .prepare("INSERT INTO evaluations (candidate_id, conclusion, reason, evidence, model) VALUES (?, 'UNCERTAIN', '无法判定', ?, 'deepseek')")
    .run(candidateId, evidence)
  return { evaluationId: Number(ev.lastInsertRowid), longPath }
}

test('HTML 转义：防 OCR/LLM 内容注入', () => {
  assert.equal(escapeHtml('<script>alert("x")</script>'), '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;')
  assert.equal(escapeHtml("a&b'c"), 'a&amp;b&#39;c')
})

test('fileUrl：本地绝对路径转 file:// URL', () => {
  const url = fileUrl(path.resolve('data/captures/x.png'))
  assert.match(url, /^file:\/\/\//)
  assert.ok(!url.includes('\\'), 'Windows 反斜杠应转为正斜杠')
})

test('复核报告：包含候选人字段、图片引用、证据、OCR markdown', () => {
  const db = freshDb()
  const { evaluationId, longPath } = seedUncertain(db, '张三')
  const items = new ReviewStore(db).list()
  assert.equal(items.length, 1)

  const entries = collectReportEntries(db, items)
  assert.equal(entries.length, 1)
  assert.equal(entries[0]!.longImagePath, longPath)
  assert.ok(entries[0]!.ocrMarkdown?.includes('OCR markdown'))
  assert.equal(entries[0]!.evidence.length, 1)

  const html = buildReviewHtml(entries, new Date('2026-08-04T10:00:00'))
  // 编号 + 候选人名 + 结论
  assert.ok(html.includes(`#${evaluationId} 张三`))
  assert.ok(html.includes('UNCERTAIN'))
  // 长图 file:// 引用
  assert.ok(html.includes('file:///'), '应包含 file:// 图片引用')
  assert.ok(html.includes('long-%E5%BC%A0%E4%B8%89.png') || html.includes('long-张三.png'), '应引用长图文件名')
  // 结构化摘要 + OCR markdown + 列表摘要
  assert.ok(html.includes('baseInfo'), '应包含结构化摘要')
  assert.ok(html.includes('OCR markdown'), '应包含 OCR markdown')
  // 证据表
  assert.ok(html.includes('期望城市须为：杭州'))
  assert.ok(html.includes('UNDECIDED'))
  db.close()
})

test('复核报告：只含 UNCERTAIN 队列，QUALIFIED/REJECTED 不进报告', () => {
  const db = freshDb()
  seedUncertain(db, '李四')
  db.prepare('INSERT INTO candidates (job_id, fingerprint, list_summary) VALUES (1, ?, ?)').run(
    'fp-qualified',
    JSON.stringify({ name: '王五' }),
  )
  db.prepare("INSERT INTO evaluations (candidate_id, conclusion, reason) VALUES (2, 'QUALIFIED', '全部通过')").run()

  const items = new ReviewStore(db).list()
  assert.equal(items.length, 1)
  assert.equal(items[0]!.candidateName, '李四')

  const entries = collectReportEntries(db, items)
  const html = buildReviewHtml(entries, new Date())
  assert.ok(html.includes('李四'))
  assert.ok(!html.includes('王五'), 'QUALIFIED 不应进复核报告')
  db.close()
})

test('复核报告：generateReviewReport 写盘并返回路径与条数', () => {
  const db = freshDb()
  seedUncertain(db, '赵六')
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'review-report-'))
  try {
    const result = generateReviewReport(db, { outDir })
    assert.equal(result.count, 1)
    assert.ok(fs.existsSync(result.reportPath), '报告文件应已写入')
    const content = fs.readFileSync(result.reportPath, 'utf8')
    assert.ok(content.includes('赵六'))
    assert.ok(content.includes('<!DOCTYPE html>'))
  } finally {
    fs.rmSync(outDir, { recursive: true, force: true })
    db.close()
  }
})

test('复核报告：改判后默认队列排除，includeResolved 含改判信息', () => {
  const db = freshDb()
  const { evaluationId } = seedUncertain(db, '孙七')
  const store = new ReviewStore(db)
  store.override({ evaluationId, conclusion: 'QUALIFIED', reason: '人工确认合格' })

  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), 'review-report-'))
  try {
    const pending = generateReviewReport(db, { outDir })
    assert.equal(pending.count, 0, '已改判项默认不进报告')

    const all = generateReviewReport(db, { outDir, includeResolved: true })
    assert.equal(all.count, 1)
    const content = fs.readFileSync(all.reportPath, 'utf8')
    assert.ok(content.includes('已改判：QUALIFIED'))
    assert.ok(content.includes('人工确认合格'))
  } finally {
    fs.rmSync(outDir, { recursive: true, force: true })
    db.close()
  }
})
