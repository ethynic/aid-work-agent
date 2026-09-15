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
  const boundary = { sender: 'system' as const, text: '14:02' }
  const base = a.align([boundary, ...ten], null)
  assert.ok(base.kind === 'ok')
  const ids = (base as { messages: Array<{ local_message_id: string }> }).messages.map((m) => m.local_message_id)
  assert.equal(new Set(ids).size, 11)
  // 追加第 11 条：前 10 条 ID 不变
  const anchor = a.lastKnownId
  const next = a.align([boundary, ...ten, { sender: 'peer', text: '收到' }], anchor)
  assert.ok(next.kind === 'ok')
  const nextIds = (next as { messages: Array<{ local_message_id: string }> }).messages.map((m) => m.local_message_id)
  assert.deepEqual(nextIds.slice(0, 11), ids)
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

test('滑窗：首条滚出，连续锚点和已观察后缀保留ID，新增人工self不丢失', () => {
  const a = new ConversationAligner()
  const peer = (text: string) => ({ sender: 'peer' as const, text })
  const base = a.align([peer('旧首条'), peer('锚点'), peer('已观察未ACK')], null)
  assert.ok(base.kind === 'ok')
  const anchor = base.messages[1]!.local_message_id
  const next = a.align([peer('锚点'), peer('已观察未ACK'), { sender: 'self', text: '人工回复' }], anchor)
  assert.ok(next.kind === 'ok')
  assert.deepEqual(next.messages.slice(0, 2), base.messages.slice(1))
  assert.equal(next.messages[2]!.sender, 'self')
  const repeat = a.align([peer('锚点'), peer('已观察未ACK'), { sender: 'self', text: '人工回复' }], anchor)
  assert.deepEqual(repeat, next)
  const third = a.align([peer('已观察未ACK'), { sender: 'self', text: '人工回复' }, peer('新答复')], next.messages[2]!.local_message_id)
  assert.ok(third.kind === 'ok')
  assert.deepEqual(third.messages.slice(0, 2), next.messages.slice(1))
})

test('无边界的重复窗口不能以最长匹配猜测；gap不破坏旧状态', () => {
  const a = new ConversationAligner()
  const same = { sender: 'peer' as const, text: '收到' }
  const ten = Array.from({ length: 10 }, () => same)
  const base = a.align(ten, null)
  assert.ok(base.kind === 'ok')
  const anchor = base.messages[9]!.local_message_id
  assert.deepEqual(a.align([...ten, same], anchor), { kind: 'gap', reason: 'duplicate_unalignable' })
  assert.deepEqual(a.align(ten, anchor), { kind: 'gap', reason: 'duplicate_unalignable' })
  assert.equal(a.lastKnownId, anchor)
})

test('锚点滚出、已观察后缀被改写不得推进', () => {
  const a = new ConversationAligner()
  const p = (text: string) => ({ sender: 'peer' as const, text })
  const base = a.align([p('a'), p('b'), p('c')], null)
  assert.ok(base.kind === 'ok')
  for (const [window, anchor] of [
    [[p('b'), p('c'), p('d')], base.messages[0]!.local_message_id],
    [[p('a'), p('b'), p('changed')], base.messages[1]!.local_message_id],
  ] as const) {
    assert.deepEqual(a.align([...window], anchor), { kind: 'gap', reason: 'alignment_broken' })
  }
  assert.equal(a.lastKnownId, base.messages[2]!.local_message_id)
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

test('damaged acknowledged prefix is ignored but pending messages and media are not lost', () => {
  const a = new ConversationAligner()
  const p = (text: string) => ({sender:'peer' as const,text})
  const base=a.align([p('old'),p('anchor')],null)
  assert.ok(base.kind==='ok')
  const anchor=base.messages[1]!.local_message_id
  const next=a.align([{sender:'system',text:'',unsupported:true},p('anchor'),p('pending')],anchor)
  assert.ok(next.kind==='ok')
  assert.equal(next.messages.at(-1)!.text,'pending')
  assert.equal(a.align([p('anchor'),p('new')],anchor).kind,'gap')
  assert.equal(a.align([p('anchor'),p('pending'),{sender:'system',text:'',unsupported:true}],anchor).kind,'gap')
  const repeat=a.align([p('damaged'),p('anchor'),p('pending')],anchor)
  assert.ok(repeat.kind==='ok')
  assert.deepEqual(repeat.messages,next.messages)
})

test('empty baseline continuation never discards newly observed media', () => {
  const a=new ConversationAligner()
  a.align([],null)
  const media={sender:'system' as const,text:'',unsupported:true}
  assert.equal(a.align([media],null,true).kind,'gap')
  const seen=a.align([{sender:'peer',text:'pending'}],null,true)
  assert.ok(seen.kind==='ok')
  assert.equal(a.align([{sender:'peer',text:'pending'},media],null,true).kind,'gap')
  assert.deepEqual(a.align([{sender:'peer',text:'pending'}],null,true),seen)
})
