/**
 * 会话窗口对齐器测试（C2-e，设计 §6；对齐 C0 重放语料语义）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ConversationAligner, boxesToBubbles, type AlignBox } from '../src/platform/aligner.js'

const B = (text: string, x0: number, y0: number, x1: number, y1: number): AlignBox => ({ text, x0, y0, x1, y1 })
const REGION = { minX: 0, maxX: 800, selfStartX: 500 }

test('基线分配稳定 ID：重复基线观察复用同一批 ID', () => {
  const a = new ConversationAligner()
  const bubbles = [
    { sender: 'peer' as const, text: '你好' },
    { sender: 'self' as const, text: '在的' },
  ]
  const first = a.align(bubbles, null)
  const second = a.align(bubbles, null)
  assert.ok(first.kind === 'ok' && second.kind === 'ok')
  assert.deepEqual(
    (first as { messages: Array<{ local_message_id: string }> }).messages.map((m) => m.local_message_id),
    (second as { messages: Array<{ local_message_id: string }> }).messages.map((m) => m.local_message_id),
  )
})

test('水位锚定：新后缀分配新 ID，前缀 ID 不变', () => {
  const a = new ConversationAligner()
  const base = a.align([{ sender: 'peer', text: '第一条' }], null)
  assert.ok(base.kind === 'ok')
  const anchor = a.lastKnownId
  const next = a.align(
    [
      { sender: 'peer', text: '第一条' },
      { sender: 'peer', text: '第二条' },
    ],
    anchor,
  )
  assert.ok(next.kind === 'ok')
  const msgs = (next as { messages: Array<{ local_message_id: string; text: string }> }).messages
  assert.equal(msgs.length, 2)
  assert.equal(msgs[0]!.local_message_id, anchor)
  assert.notEqual(msgs[1]!.local_message_id, anchor)
})

test('10 条连续相同文本：10 个不同 ID，重复观察不折叠', () => {
  const a = new ConversationAligner()
  const ten = Array.from({ length: 10 }, () => ({ sender: 'peer' as const, text: '收到' }))
  const base = a.align(ten, null)
  assert.ok(base.kind === 'ok')
  const ids = (base as { messages: Array<{ local_message_id: string }> }).messages.map((m) => m.local_message_id)
  assert.equal(new Set(ids).size, 10)
  // 追加第 11 条：前 10 条 ID 不变
  const anchor = a.lastKnownId
  const next = a.align([...ten, { sender: 'peer', text: '收到' }], anchor)
  assert.ok(next.kind === 'ok')
  const nextIds = (next as { messages: Array<{ local_message_id: string }> }).messages.map((m) => m.local_message_id)
  assert.deepEqual(nextIds.slice(0, 10), ids)
})

test('锚点不在记忆窗口 → gap（alignment_broken，不猜测）', () => {
  const a = new ConversationAligner()
  a.align([{ sender: 'peer', text: 'x' }], null)
  const result = a.align([{ sender: 'peer', text: '无关窗口' }], 'm-not-in-window')
  assert.deepEqual(result, { kind: 'gap', reason: 'alignment_broken' })
})

test('sender 不明 → 整体 gap（sender_ambiguous）', () => {
  const a = new ConversationAligner()
  const result = a.align([{ sender: 'unknown', text: '谁说的' }], null)
  assert.deepEqual(result, { kind: 'gap', reason: 'sender_ambiguous' })
})

test('拆行重建：同侧相邻行合并为一条消息', () => {
  const boxes = [
    B('这件事需要', 60, 100, 300, 120),
    B('再对齐一下', 60, 118, 280, 138), // y 相邻 → 同一气泡
    B('另一条消息', 520, 200, 700, 220), // self 区
  ]
  const bubbles = boxesToBubbles(boxes, REGION)
  assert.equal(bubbles.length, 2)
  assert.equal(bubbles[0]!.text, '这件事需要再对齐一下')
  assert.equal(bubbles[0]!.sender, 'peer')
  assert.equal(bubbles[1]!.sender, 'self')
})

test('居中短行识别为 system；时间轴不参与对齐消息', () => {
  const boxes = [B('14:02', 380, 50, 420, 68), B('正文', 60, 100, 200, 120)]
  const bubbles = boxesToBubbles(boxes, REGION)
  assert.equal(bubbles[0]!.sender, 'system')
  assert.equal(bubbles[1]!.sender, 'peer')
})
