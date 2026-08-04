import assert from 'node:assert/strict'
import test from 'node:test'
import BetterSqliteDatabase from 'better-sqlite3'
import type { Database as BetterSqliteDatabaseType } from 'better-sqlite3'
import { runMigrations } from '../db/migrations.js'
import { ReviewStore } from '../src/main/storage/reviewStore.js'
import { SessionStore } from '../src/main/storage/sessionStore.js'
import { JobStore } from '../src/main/storage/jobStore.js'

function freshDb() {
  const db = new BetterSqliteDatabase(':memory:')
  runMigrations(db)
  return db
}

let seedCounter = 0

function seedEvaluation(db: BetterSqliteDatabaseType, conclusion: string): number {
  seedCounter += 1
  db.prepare("INSERT INTO jobs (name) VALUES ('岗位A')").run()
  db.prepare("INSERT INTO candidates (job_id, fingerprint, list_summary) VALUES (?, ?, '{\"name\":\"张三\"}')").run(
    seedCounter,
    `fp-${seedCounter}`,
  )
  const info = db
    .prepare("INSERT INTO evaluations (candidate_id, conclusion, reason) VALUES (?, ?, '测试理由')")
    .run(seedCounter, conclusion)
  return Number(info.lastInsertRowid)
}

test('复核队列：只列未改判的 UNCERTAIN，带候选人姓名', () => {
  const db = freshDb()
  const store = new ReviewStore(db)
  seedEvaluation(db, 'UNCERTAIN')
  seedEvaluation(db, 'QUALIFIED') // 不进队列

  const items = store.list()
  assert.equal(items.length, 1)
  assert.equal(items[0]!.candidateName, '张三')
  assert.equal(items[0]!.conclusion, 'UNCERTAIN')
  assert.equal(items[0]!.override, null)
  db.close()
})

test('改判：记录原结论+改判结论+理由，原评估不可变', () => {
  const db = freshDb()
  const store = new ReviewStore(db)
  const evalId = seedEvaluation(db, 'UNCERTAIN')

  store.override({ evaluationId: evalId, conclusion: 'QUALIFIED', reason: '人工复核合格' })

  // 原评估行结论不变
  const evaluation = db.prepare('SELECT conclusion FROM evaluations WHERE id = ?').get(evalId) as { conclusion: string }
  assert.equal(evaluation.conclusion, 'UNCERTAIN')

  // 改判记录完整
  const override = db.prepare('SELECT * FROM review_overrides WHERE evaluation_id = ?').get(evalId) as {
    original_conclusion: string
    override_conclusion: string
    override_reason: string
  }
  assert.equal(override.original_conclusion, 'UNCERTAIN')
  assert.equal(override.override_conclusion, 'QUALIFIED')
  assert.equal(override.override_reason, '人工复核合格')

  // 默认队列不再出现（已改判）；includeResolved 可见且带改判信息
  assert.equal(store.list().length, 0)
  const resolved = store.list({ includeResolved: true })
  assert.equal(resolved.length, 1)
  assert.equal(resolved[0]!.override?.overrideConclusion, 'QUALIFIED')
  db.close()
})

test('改判 fail-loud：评估不存在 / 理由为空 / 结论非法', () => {
  const db = freshDb()
  const store = new ReviewStore(db)
  const evalId = seedEvaluation(db, 'UNCERTAIN')
  assert.throws(() => store.override({ evaluationId: 999, conclusion: 'QUALIFIED', reason: 'x' }), /不存在/)
  assert.throws(() => store.override({ evaluationId: evalId, conclusion: 'QUALIFIED', reason: ' ' }), /理由不能为空/)
  assert.throws(
    () => store.override({ evaluationId: evalId, conclusion: 'MAYBE' as never, reason: 'x' }),
    /非法改判结论/,
  )
  db.close()
})

test('会话恢复清扫：残留 RUNNING/PAUSED → INTERRUPTED，终态不受影响', () => {
  const db = freshDb()
  const store = new SessionStore(db)
  const runningId = store.create({})
  const pausedId = store.create({})
  store.setStatus(pausedId, 'PAUSED')
  const stoppedId = store.create({})
  store.end(stoppedId, 'STOPPED')

  const swept = store.sweepInterrupted()
  assert.equal(swept, 2)
  const rows = db.prepare('SELECT id, status, ended_at FROM sessions ORDER BY id').all() as Array<{
    id: number
    status: string
    ended_at: string | null
  }>
  assert.equal(rows[0]!.status, 'INTERRUPTED')
  assert.ok(rows[0]!.ended_at)
  assert.equal(rows[1]!.status, 'INTERRUPTED')
  assert.equal(rows[2]!.status, 'STOPPED')

  // 幂等：再扫无影响
  assert.equal(store.sweepInterrupted(), 0)
  db.close()
})

test('JobStore CRUD + 入参校验', () => {
  const db = freshDb()
  const store = new JobStore(db)
  const job = store.create({
    name: '前端工程师',
    hardRules: JSON.stringify({ city: ['北京'], minYears: 3 }),
    actionLimitSession: 30,
  })
  assert.ok(job.id > 0)
  assert.equal(store.list().length, 1)

  const updated = store.update(job.id, { name: '高级前端', hardRules: null })
  assert.equal(updated.name, '高级前端')

  // 校验：空名称 / 未知硬规则键 / 非法上限
  assert.throws(() => store.create({ name: ' ' }), /不能为空/)
  assert.throws(() => store.create({ name: 'x', hardRules: '{"badKey":1}' }), /未知键/)
  assert.throws(() => store.create({ name: 'x', actionLimitSession: 0 }), /正整数/)

  store.remove(job.id)
  assert.equal(store.list().length, 0)
  assert.throws(() => store.remove(999), /不存在/)
  db.close()
})
