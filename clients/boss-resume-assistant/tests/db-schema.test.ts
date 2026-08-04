import assert from 'node:assert/strict'
import test from 'node:test'
import os from 'node:os'
import path from 'node:path'
import fs from 'node:fs'
import BetterSqliteDatabase from 'better-sqlite3'
import { runMigrations, getCurrentVersion, getAppliedVersions } from '../db/migrations.js'
import { TARGET_SCHEMA_VERSION, MIGRATIONS } from '../db/schema.js'

function tempDbPath(): string {
  return path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), 'boss-resume-test-')),
    'test.db',
  )
}

test('迁移创建全部 9 张业务表 + schema_migrations 版本表', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)

  const rows = db
    .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    .all() as { name: string }[]
  const names = rows.map((r) => r.name)

  const expected = [
    'actions',
    'candidates',
    'captures',
    'cdp_audit',
    'evaluations',
    'jobs',
    'resume_views',
    'review_overrides',
    'schema_migrations',
    'sessions',
  ]
  for (const t of expected) {
    assert.ok(names.includes(t), `table ${t} should exist`)
  }
  db.close()
})

test('迁移后版本号等于 TARGET_SCHEMA_VERSION', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)
  assert.equal(getCurrentVersion(db), TARGET_SCHEMA_VERSION)
  db.close()
})

test('重复迁移幂等：第二次 runMigrations 不再应用', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  const first = runMigrations(db)
  assert.deepEqual(first.applied, [1, 2, 3, 4])

  const second = runMigrations(db)
  assert.deepEqual(second.applied, [], 'second migration run should apply nothing')
  assert.equal(getAppliedVersions(db).size, 4)
  db.close()
})

test('v2：actions 表补幂等字段（unique_key/session_id/sent_at/confirmed_at）+ 唯一索引', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)
  const cols = db.prepare('PRAGMA table_info(actions)').all() as { name: string }[]
  const names = cols.map((c) => c.name)
  for (const c of ['unique_key', 'session_id', 'sent_at', 'confirmed_at']) {
    assert.ok(names.includes(c), `actions should have ${c}`)
  }
  // unique_key 唯一约束生效
  db.prepare("INSERT INTO actions (action, status, unique_key) VALUES ('GREET', 'PLANNED', 'fp|GREET')").run()
  assert.throws(
    () =>
      db.prepare("INSERT INTO actions (action, status, unique_key) VALUES ('GREET', 'PLANNED', 'fp|GREET')").run(),
    /UNIQUE/i,
  )
  // 历史行为 NULL 的 unique_key 不冲突
  db.prepare("INSERT INTO actions (action, status) VALUES ('GREET', 'PLANNED')").run()
  db.prepare("INSERT INTO actions (action, status) VALUES ('GREET', 'PLANNED')").run()
  db.close()
})

test('schema_migrations 版本号唯一（PRIMARY KEY）', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)
  assert.throws(
    () => db.prepare('INSERT INTO schema_migrations (version) VALUES (1)').run(),
    /UNIQUE/i,
  )
  db.close()
})

test('含 created_at 的业务表确实存在该字段', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)

  // sessions 用 started_at，candidates 用 first/last_seen_at，resume_views 用 viewed_at，
  // cdp_audit 用 at，其余业务表用 created_at
  const tables = ['jobs', 'captures', 'evaluations', 'actions']
  for (const t of tables) {
    const cols = db.prepare(`PRAGMA table_info(${t})`).all() as { name: string }[]
    const names = cols.map((c) => c.name)
    assert.ok(names.includes('created_at'), `${t} should have created_at`)
  }
  // cdp_audit 用 at（审计时间语义）
  const auditCols = db.prepare('PRAGMA table_info(cdp_audit)').all() as { name: string }[]
  assert.ok(auditCols.map((c) => c.name).includes('at'), 'cdp_audit should have at')
  db.close()
})

test('sessions 表用 started_at / ended_at（会话语义）', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)
  const cols = db.prepare('PRAGMA table_info(sessions)').all() as { name: string }[]
  const names = cols.map((c) => c.name)
  assert.ok(names.includes('started_at'), 'sessions should have started_at')
  assert.ok(names.includes('ended_at'), 'sessions should have ended_at')
  db.close()
})

test('candidates.fingerprint 有 UNIQUE 约束', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)
  db.prepare('INSERT INTO candidates (fingerprint) VALUES (?)').run('fp-1')
  assert.throws(
    () => db.prepare('INSERT INTO candidates (fingerprint) VALUES (?)').run('fp-1'),
    /UNIQUE/i,
  )
  db.close()
})

test('MIGRATIONS 版本号严格递增且无重复', () => {
  const versions = MIGRATIONS.map((m) => m.version)
  const sorted = [...versions].sort((a, b) => a - b)
  assert.deepEqual(versions, sorted, 'versions must be ascending')
  assert.equal(new Set(versions).size, versions.length, 'versions must be unique')
})

test('v3：jobs 动作上限列 + resume_views/evaluations session_id + review_overrides 表', () => {
  const dbPath = tempDbPath()
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)

  const jobCols = (db.prepare('PRAGMA table_info(jobs)').all() as { name: string }[]).map((c) => c.name)
  for (const c of ['action_limit_session', 'action_limit_day']) {
    assert.ok(jobCols.includes(c), `jobs should have ${c}`)
  }
  const viewCols = (db.prepare('PRAGMA table_info(resume_views)').all() as { name: string }[]).map((c) => c.name)
  assert.ok(viewCols.includes('session_id'), 'resume_views should have session_id')
  const evalCols = (db.prepare('PRAGMA table_info(evaluations)').all() as { name: string }[]).map((c) => c.name)
  assert.ok(evalCols.includes('session_id'), 'evaluations should have session_id')

  const overrideCols = (db.prepare('PRAGMA table_info(review_overrides)').all() as { name: string }[]).map(
    (c) => c.name,
  )
  for (const c of ['evaluation_id', 'original_conclusion', 'override_conclusion', 'override_reason']) {
    assert.ok(overrideCols.includes(c), `review_overrides should have ${c}`)
  }
  db.close()
})
