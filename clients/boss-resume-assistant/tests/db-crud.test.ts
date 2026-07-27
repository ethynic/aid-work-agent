import assert from 'node:assert/strict'
import test from 'node:test'
import os from 'node:os'
import path from 'node:path'
import fs from 'node:fs'
import BetterSqliteDatabase from 'better-sqlite3'
import { runMigrations } from '../db/migrations.js'
import { seedDevData } from '../db/seed.js'

function openTempDb(): BetterSqliteDatabase.Database {
  const dbPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'boss-crud-')), 't.db')
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)
  return db
}

test('jobs 插入并查询，created_at 自动填充', () => {
  const db = openTempDb()
  const info = db
    .prepare('INSERT INTO jobs (name, knowledge_version) VALUES (?, ?)')
    .run('岗位A', 'v1')
  assert.ok(info.lastInsertRowid)
  const row = db.prepare('SELECT * FROM jobs WHERE id = ?').get(info.lastInsertRowid) as {
    name: string
    created_at: string
  }
  assert.equal(row.name, '岗位A')
  assert.ok(row.created_at, 'created_at should be auto-filled')
  db.close()
})

test('candidates fingerprint 唯一去重', () => {
  const db = openTempDb()
  db.prepare('INSERT INTO candidates (fingerprint) VALUES (?)').run('fp-1')
  assert.throws(
    () => db.prepare('INSERT INTO candidates (fingerprint) VALUES (?)').run('fp-1'),
    /UNIQUE/i,
  )
  db.close()
})

test('resume_views 关联 candidate 与 job', () => {
  const db = openTempDb()
  const jobId = (db.prepare('INSERT INTO jobs (name) VALUES (?)').run('J') as { lastInsertRowid: number }).lastInsertRowid
  const candId = (db.prepare('INSERT INTO candidates (job_id, fingerprint) VALUES (?, ?)').run(Number(jobId), 'fp-2') as { lastInsertRowid: number }).lastInsertRowid
  db.prepare('INSERT INTO resume_views (job_id, candidate_id, source, completeness, summary_json) VALUES (?, ?, ?, ?, ?)')
    .run(Number(jobId), Number(candId), 'OCR_NORMALIZED', 'COMPLETE_SUMMARY', '{}')

  const row = db.prepare('SELECT * FROM resume_views WHERE candidate_id = ?').get(Number(candId)) as {
    source: string
    completeness: string
  }
  assert.equal(row.source, 'OCR_NORMALIZED')
  assert.equal(row.completeness, 'COMPLETE_SUMMARY')
  db.close()
})

test('cdp_audit 插入与按 method 查询', () => {
  const db = openTempDb()
  db.prepare('INSERT INTO cdp_audit (session_id_hash, method, params_summary, duration_ms, status) VALUES (?, ?, ?, ?, ?)')
    .run('sesshash', 'Page.captureScreenshot', '{}', 42, 'ok')
  const rows = db.prepare('SELECT * FROM cdp_audit WHERE method = ?').all('Page.captureScreenshot') as { method: string }[]
  assert.equal(rows.length, 1)
  db.close()
})

test('actions 三态状态字段可记录', () => {
  const db = openTempDb()
  const candId = (db.prepare('INSERT INTO candidates (fingerprint) VALUES (?)').run('fp-3') as { lastInsertRowid: number }).lastInsertRowid
  for (const status of ['PLANNED', 'SENT', 'CONFIRMED', 'UNKNOWN', 'FAILED']) {
    db.prepare('INSERT INTO actions (candidate_id, action, status) VALUES (?, ?, ?)').run(Number(candId), 'GREET', status)
  }
  const rows = db.prepare('SELECT status FROM actions WHERE candidate_id = ? ORDER BY id').all(Number(candId)) as { status: string }[]
  assert.deepEqual(rows.map((r) => r.status), ['PLANNED', 'SENT', 'CONFIRMED', 'UNKNOWN', 'FAILED'])
  db.close()
})

test('seedDevData 幂等：重复调用不重复插入', () => {
  const db = openTempDb()
  seedDevData(db)
  seedDevData(db)
  const row = db.prepare('SELECT COUNT(*) AS c FROM jobs').get() as { c: number }
  assert.equal(row.c, 1)
  db.close()
})
