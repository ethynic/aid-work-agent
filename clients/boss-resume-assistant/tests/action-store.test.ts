import assert from 'node:assert/strict'
import test from 'node:test'
import os from 'node:os'
import path from 'node:path'
import fs from 'node:fs'
import BetterSqliteDatabase from 'better-sqlite3'
import { runMigrations } from '../db/migrations.js'
import { ActionStore } from '../src/main/actions/ActionStore.js'

function openTempDb(): BetterSqliteDatabase.Database {
  const dbPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'boss-action-')), 't.db')
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)
  return db
}

function plannedInput(overrides: Record<string, unknown> = {}) {
  return {
    candidateId: null,
    sessionId: 1,
    action: 'GREET' as const,
    reason: 'r',
    uniqueKey: 'fp-1|GREET',
    ...overrides,
  }
}

test('getOrInsertPlanned 插入 PLANNED 记录', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  const { record, inserted } = store.getOrInsertPlanned(plannedInput())
  assert.equal(inserted, true)
  assert.equal(record.status, 'PLANNED')
  assert.equal(record.uniqueKey, 'fp-1|GREET')
  db.close()
})

test('幂等：同 unique_key 二次插入返回已有记录，不产生新行', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  const first = store.getOrInsertPlanned(plannedInput())
  const second = store.getOrInsertPlanned(plannedInput())
  assert.equal(second.inserted, false)
  assert.equal(second.record.id, first.record.id)
  const count = db.prepare('SELECT COUNT(*) AS c FROM actions').get() as { c: number }
  assert.equal(count.c, 1)
  db.close()
})

test('不同 unique_key 各自插入', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  store.getOrInsertPlanned(plannedInput())
  const other = store.getOrInsertPlanned(plannedInput({ uniqueKey: 'fp-1|REJECT' }))
  assert.equal(other.inserted, true)
  const count = db.prepare('SELECT COUNT(*) AS c FROM actions').get() as { c: number }
  assert.equal(count.c, 2)
  db.close()
})

test('状态机流转：PLANNED→SENT→CONFIRMED，时间戳落库', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  const { record } = store.getOrInsertPlanned(plannedInput())
  store.markSent(record.id)
  let r = store.getById(record.id)!
  assert.equal(r.status, 'SENT')
  assert.ok(r.sentAt, 'sent_at should be set')
  store.markConfirmed(record.id, '/tmp/after.png')
  r = store.getById(record.id)!
  assert.equal(r.status, 'CONFIRMED')
  assert.ok(r.confirmedAt, 'confirmed_at should be set')
  assert.equal(r.afterScreenshotPath, '/tmp/after.png')
  db.close()
})

test('markUnknown / markFailed 记录 error', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  const a = store.getOrInsertPlanned(plannedInput())
  store.markUnknown(a.record.id, '结果无法确认')
  assert.equal(store.getById(a.record.id)!.status, 'UNKNOWN')
  assert.equal(store.getById(a.record.id)!.error, '结果无法确认')

  const b = store.getOrInsertPlanned(plannedInput({ uniqueKey: 'fp-2|GREET' }))
  store.markFailed(b.record.id, '按钮定位失败')
  assert.equal(store.getById(b.record.id)!.status, 'FAILED')
  db.close()
})

test('resetToPlanned 清除状态与错误，供重新执行', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  const { record } = store.getOrInsertPlanned(plannedInput())
  store.markFailed(record.id, 'boom')
  store.resetToPlanned(record.id)
  const r = store.getById(record.id)!
  assert.equal(r.status, 'PLANNED')
  assert.equal(r.error, null)
  assert.equal(r.sentAt, null)
  db.close()
})

test('会话计数只统计已发出动作（SENT/CONFIRMED/UNKNOWN），按 session 隔离', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  const mk = (key: string, sessionId: number) =>
    store.getOrInsertPlanned(plannedInput({ uniqueKey: key, sessionId }))
  const a = mk('a|GREET', 1)
  const b = mk('b|GREET', 1)
  const c = mk('c|GREET', 1)
  mk('d|GREET', 2) // 其它会话
  store.markSent(a.record.id)
  store.markConfirmed(b.record.id, null)
  // c 保持 PLANNED，不计数
  assert.equal(store.countAttemptsBySession(1), 2)
  assert.equal(store.countAttemptsBySession(2), 0)
  store.markUnknown(c.record.id, 'x')
  assert.equal(store.countAttemptsBySession(1), 3)
  db.close()
})

test('每日计数只统计当日已发出动作', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  const a = store.getOrInsertPlanned(plannedInput({ uniqueKey: 'a|GREET' }))
  store.markSent(a.record.id)
  store.getOrInsertPlanned(plannedInput({ uniqueKey: 'b|GREET' })) // PLANNED 不计
  assert.equal(store.countDailyAttempts(), 1)
  db.close()
})

test('listPlanned 只返回 PLANNED（重启恢复入口）', () => {
  const db = openTempDb()
  const store = new ActionStore(db)
  const a = store.getOrInsertPlanned(plannedInput({ uniqueKey: 'a|GREET' }))
  const b = store.getOrInsertPlanned(plannedInput({ uniqueKey: 'b|GREET' }))
  store.markSent(b.record.id)
  const planned = store.listPlanned()
  assert.equal(planned.length, 1)
  assert.equal(planned[0]!.id, a.record.id)
  db.close()
})
