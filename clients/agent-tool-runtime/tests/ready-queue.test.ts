/**
 * ReadyQueue 契约测试（设计 §7）：到期过滤、优先级（control>send>observe）、
 * 上次服务 FIFO 公平、单动作单元让位。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ReadyQueue } from '../src/sessionTasks/readyQueue.js'

test('到期过滤：未到期不可服务', () => {
  const q = new ReadyQueue()
  q.set('A', 'observe', 1_000)
  assert.equal(q.peek(999), null)
  assert.notEqual(q.peek(1_000), null)
})

test('优先级：control > send > observe', () => {
  const q = new ReadyQueue()
  q.set('A', 'observe', 0)
  q.set('B', 'send', 0)
  q.set('C', 'control', 0)
  assert.equal(q.peek(0)?.taskId, 'C')
  q.remove('C')
  assert.equal(q.peek(0)?.taskId, 'B')
})

test('同优先级按上次服务时间 FIFO（防饥饿）', () => {
  const q = new ReadyQueue()
  q.set('A', 'observe', 0)
  q.set('B', 'observe', 0)
  q.markServed('A', 100)
  assert.equal(q.peek(0)?.taskId, 'B')
  q.markServed('B', 200)
  assert.equal(q.peek(0)?.taskId, 'A')
})

test('让位：刚服务完的任务在其他任务就绪时被跳过', () => {
  const q = new ReadyQueue()
  q.set('A', 'observe', 0)
  q.set('B', 'observe', 0)
  q.markServed('A', 100)
  assert.equal(q.peek(0, { excludeTaskId: 'A' })?.taskId, 'B')
  // 仅剩 A 到期时不得饿死：排除项是唯一就绪项 → 仍返回 A
  q.remove('B')
  assert.equal(q.peek(0, { excludeTaskId: 'A' })?.taskId, 'A')
})

test('set 保留上次服务时间（相位迁移不重置公平性）', () => {
  const q = new ReadyQueue()
  q.set('A', 'observe', 0)
  q.markServed('A', 500)
  q.set('A', 'send', 0) // 相位迁移
  assert.equal(q.get('A')?.lastServedAt, 500)
  assert.equal(q.get('A')?.kind, 'send')
})
