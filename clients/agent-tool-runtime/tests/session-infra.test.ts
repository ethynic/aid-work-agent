/**
 * 单实例保护 + 保留清理测试（C2-d，设计 §7/§8）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { acquireSessionTasksSingleInstance } from '../src/sessionTasks/singleInstance.js'
import {
  enforceRetention,
  isSafeChild,
  sessionTasksRoot,
  type AssignmentRetentionState,
} from '../src/sessionTasks/retention.js'

test('单实例：第二个实例 acquire 返回 null；释放后可重新获取', async () => {
  const home = mkdtempSync(join(tmpdir(), 'st-single-'))
  const first = await acquireSessionTasksSingleInstance(home)
  assert.ok(first !== null, '第一实例应获得锁')
  const second = await acquireSessionTasksSingleInstance(home)
  assert.equal(second, null, '同 home 第二实例必须被拒')
  await first!.release()
  const third = await acquireSessionTasksSingleInstance(home)
  assert.ok(third !== null, '释放后可重新获取')
  await third!.release()
  rmSync(home, { recursive: true, force: true })
})

test('不同 runtime home 互不排斥', async () => {
  const homeA = mkdtempSync(join(tmpdir(), 'st-single-a-'))
  const homeB = mkdtempSync(join(tmpdir(), 'st-single-b-'))
  const a = await acquireSessionTasksSingleInstance(homeA)
  const b = await acquireSessionTasksSingleInstance(homeB)
  assert.ok(a !== null && b !== null)
  await a!.release()
  await b!.release()
  rmSync(homeA, { recursive: true, force: true })
  rmSync(homeB, { recursive: true, force: true })
})

function makeAssignment(home: string, assignmentId: string, bytes: number, ageDays = 0): AssignmentRetentionState {
  const dir = join(sessionTasksRoot(home), assignmentId)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'events.jsonl'), 'x'.repeat(bytes))
  return {
    assignmentId,
    terminal: true,
    fullyAcked: true,
    noPendingJournal: true,
    lastActivityAt: Date.now() - ageDays * 86_400_000,
  }
}

test('保留清理：终态+全 ACK+过期删除；运行中/未 ACK/新近终态不删', () => {
  const home = mkdtempSync(join(tmpdir(), 'st-ret-'))
  const old = makeAssignment(home, 'as-old', 100, 30)
  const fresh = makeAssignment(home, 'as-fresh', 100, 1)
  const running = { ...makeAssignment(home, 'as-run', 100, 30), terminal: false }
  const unacked = { ...makeAssignment(home, 'as-unacked', 100, 30), fullyAcked: false }
  const pending = { ...makeAssignment(home, 'as-pending', 100, 30), noPendingJournal: false }
  const result = enforceRetention(home, [old, fresh, running, unacked, pending], { keepDays: 7 })
  assert.deepEqual(result.deletedAssignmentIds, ['as-old'])
  assert.ok(!existsSync(join(sessionTasksRoot(home), 'as-old')))
  assert.ok(existsSync(join(sessionTasksRoot(home), 'as-fresh')))
  assert.ok(existsSync(join(sessionTasksRoot(home), 'as-run')))
  assert.ok(existsSync(join(sessionTasksRoot(home), 'as-unacked')))
  assert.ok(existsSync(join(sessionTasksRoot(home), 'as-pending')))
  rmSync(home, { recursive: true, force: true })
})

test('磁盘上限：80% 告警、100% 停新', () => {
  const home = mkdtempSync(join(tmpdir(), 'st-cap-'))
  // 1KB 上限：写 900B → warn80；写满 1KB+ → stopNew
  makeAssignment(home, 'as-1', 900)
  const r1 = enforceRetention(home, [], { maxBytes: 1000 })
  assert.equal(r1.warn80, true)
  assert.equal(r1.stopNew, false)
  makeAssignment(home, 'as-2', 200)
  const r2 = enforceRetention(home, [], { maxBytes: 1000 })
  assert.equal(r2.stopNew, true)
  rmSync(home, { recursive: true, force: true })
})

test('安全路径：拒绝目录穿越', () => {
  const home = mkdtempSync(join(tmpdir(), 'st-safe-'))
  const root = sessionTasksRoot(home)
  assert.ok(isSafeChild(root, join(root, 'as-1')))
  assert.ok(!isSafeChild(root, join(root, '..', 'escape')))
  assert.ok(!isSafeChild(root, join(root, 'a..b', '..', '..', 'x')))
  rmSync(home, { recursive: true, force: true })
})
