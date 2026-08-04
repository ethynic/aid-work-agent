import assert from 'node:assert/strict'
import test from 'node:test'
import { csvEscape, buildCsv, buildJson, queryExportRows, type ExportRow } from '../src/main/export/exporter.js'
import BetterSqliteDatabase from 'better-sqlite3'
import { runMigrations } from '../db/migrations.js'

function makeRow(overrides: Partial<ExportRow> = {}): ExportRow {
  return {
    candidateName: '张三',
    fingerprint: 'abc123',
    conclusion: 'QUALIFIED',
    overrideConclusion: null,
    overrideReason: null,
    reason: 'all hard rules passed',
    model: 'deepseek',
    evaluatedAt: '2026-08-04 10:00:00',
    lastAction: 'GREET',
    lastActionStatus: 'CONFIRMED',
    lastActionAt: '2026-08-04 10:01:00',
    ...overrides,
  }
}

test('csvEscape：普通值不包裹', () => {
  assert.equal(csvEscape('张三'), '张三')
  assert.equal(csvEscape(''), '')
  assert.equal(csvEscape(null), '')
})

test('csvEscape：含逗号/引号/换行必须包裹且引号双写', () => {
  assert.equal(csvEscape('a,b'), '"a,b"')
  assert.equal(csvEscape('说"你好"'), '"说""你好"""')
  assert.equal(csvEscape('第一行\n第二行'), '"第一行\n第二行"')
  assert.equal(csvEscape('a\rb'), '"a\rb"')
})

test('csvEscape：公式注入防护，=+-@ 开头的值前置单引号', () => {
  // OCR/LLM/用户输入文本若被 Excel 解析为公式会执行恶意内容，必须转义
  assert.equal(csvEscape('=1+1'), "'=1+1")
  assert.equal(csvEscape('+SUM(A1)'), "'+SUM(A1)")
  assert.equal(csvEscape('-2-1'), "'-2-1")
  assert.equal(csvEscape('@mention'), "'@mention")
  assert.equal(csvEscape('\t=x'), "'\t=x")
  // 单引号前缀与引号包裹可叠加
  assert.equal(csvEscape('=a,b'), `"'=a,b"`)
  // 非首字符的符号不触发
  assert.equal(csvEscape('a=b'), 'a=b')
})

test('buildCsv：带 BOM + 表头 + CRLF 行尾，字段转义正确', () => {
  const csv = buildCsv([makeRow({ reason: '含,逗号' }), makeRow({ candidateName: null })])
  assert.ok(csv.startsWith('﻿'), 'CSV 应以 BOM 开头')
  const lines = csv.slice(1).split('\r\n')
  assert.equal(lines.length, 3) // 表头 + 2 行
  assert.ok(lines[0]!.includes('候选人'))
  assert.ok(lines[1]!.includes('"含,逗号"'))
  // 姓名为空的行首字段为空
  assert.ok(lines[2]!.startsWith(','))
})

test('buildJson：输出可解析数组，字段完整', () => {
  const rows = [makeRow()]
  const parsed = JSON.parse(buildJson(rows)) as ExportRow[]
  assert.equal(parsed.length, 1)
  assert.equal(parsed[0]!.candidateName, '张三')
  assert.equal(parsed[0]!.lastActionStatus, 'CONFIRMED')
})

test('queryExportRows：evaluations 联查 candidates + 最新改判 + 最新动作', () => {
  const db = new BetterSqliteDatabase(':memory:')
  runMigrations(db)
  db.prepare("INSERT INTO jobs (name) VALUES ('岗位A')").run()
  db.prepare("INSERT INTO candidates (job_id, fingerprint, list_summary) VALUES (1, 'fp1', '{\"name\":\"张三\"}')").run()
  db.prepare(
    "INSERT INTO evaluations (candidate_id, conclusion, reason, model) VALUES (1, 'UNCERTAIN', '无法判断', 'deepseek')",
  ).run()
  db.prepare(
    "INSERT INTO review_overrides (evaluation_id, candidate_id, original_conclusion, override_conclusion, override_reason) VALUES (1, 1, 'UNCERTAIN', 'QUALIFIED', '人工看过后合格')",
  ).run()
  db.prepare(
    "INSERT INTO actions (candidate_id, action, status, unique_key) VALUES (1, 'GREET', 'CONFIRMED', 'fp1|GREET')",
  ).run()

  const rows = queryExportRows(db)
  assert.equal(rows.length, 1)
  const row = rows[0]!
  assert.equal(row.candidateName, '张三')
  assert.equal(row.conclusion, 'UNCERTAIN')
  assert.equal(row.overrideConclusion, 'QUALIFIED')
  assert.equal(row.overrideReason, '人工看过后合格')
  assert.equal(row.lastAction, 'GREET')
  assert.equal(row.lastActionStatus, 'CONFIRMED')

  // 结论筛选
  assert.equal(queryExportRows(db, { conclusion: 'QUALIFIED' }).length, 0)
  assert.equal(queryExportRows(db, { conclusion: 'UNCERTAIN' }).length, 1)
  // 岗位筛选
  assert.equal(queryExportRows(db, { jobId: 999 }).length, 0)
  // 日期格式非法 fail-loud（与 AuditStore 同一口径）
  assert.throws(() => queryExportRows(db, { dateFrom: '08/01/2026' }), /YYYY-MM-DD/)
  assert.throws(() => queryExportRows(db, { dateTo: '2026-8-4' }), /YYYY-MM-DD/)
  db.close()
})
