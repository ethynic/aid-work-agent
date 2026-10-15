/**
 * localdb-messages.test.ts — C9b 契约归一 + 游标编解码单测（任务 1.9b；设计 §4.1）。
 *
 * 覆盖：side 判定（严格相等不 trim / 非字符串防御）、时间格式化（显式 Date 构造做时区无关
 * 断言）、非文本占位映射（0/2 → text，其余占位 + kind 省略）、排除表（CONFIRMED_SYSTEM_TYPES
 * + isParticipating + system 标注）、parse_trusted 透传、展示排序（同秒 server_id 决胜 /
 * 迟到回退重排 / msg_id 稳定兜底）、防御丢弃、cursor 编解码 roundtrip（含保留字符与中文）、
 * malformed 全形态、ensureCursorMatches 三类失配。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  CONFIRMED_SYSTEM_TYPES,
  CURSOR_VERSION,
  NON_TEXT_PLACEHOLDER,
  decodeCursor,
  encodeCursor,
  ensureCursorMatches,
  formatTimestamp,
  isParticipating,
  normalizeMessages,
  type RawMessageRow,
} from '../src/platform/localdb/messages.js'

const SELF = '1688850000000001'
const PEER = '1688850000000002'

/** 与实现同源的本地时区期望值构造（测试断言显式 Date 构造，时区无关） */
function localStamp(seconds: number): string {
  const d = new Date(seconds * 1000)
  const p2 = (n: number): string => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}` +
    ` ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`
  )
}

function row(overrides: Partial<RawMessageRow>): RawMessageRow {
  return { message_id: 1, sender_id: PEER, content_type: 2, send_time: 1700000000, trusted: true, ...overrides }
}

test('side 判定：严格字符串相等（不 trim 不忽略大小写），非字符串/缺失防御为 peer', () => {
  const out = normalizeMessages(
    [
      row({ message_id: 1, sender_id: SELF }),
      row({ message_id: 2, sender_id: PEER }),
      row({ message_id: 3, sender_id: ` ${SELF}` }), // 前导空白 ≠ 本人（不 trim）
      row({ message_id: 4, sender_id: `${SELF} ` }), // 尾随空白 ≠ 本人（不 trim）
      row({ message_id: 5, sender_id: 12345 as unknown as string }), // 非字符串 → peer
      row({ message_id: 6, sender_id: null }),
      row({ message_id: 7 }), // 缺 sender_id → peer
    ],
    SELF,
  )
  assert.deepEqual(
    out.map((m) => m.side),
    ['self', 'peer', 'peer', 'peer', 'peer', 'peer', 'peer'],
  )
})

test('时间格式化：秒 → YYYY-MM-DD HH:MM:SS 本地时区；非法值显式留空', () => {
  assert.equal(formatTimestamp(1700000000), localStamp(1700000000))
  assert.match(formatTimestamp(1700000000), /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
  assert.equal(formatTimestamp(0), localStamp(0)) // 纪元（本地时区表示）
  assert.equal(formatTimestamp(1700000000.5 as unknown as number), '') // 非整数
  for (const bad of [-1, Number.NaN, Infinity, '1700000000', null, undefined]) {
    assert.equal(formatTimestamp(bad), '', String(bad))
  }
  // 归一路径同样生效
  const [m] = normalizeMessages([row({ send_time: 1700000000 })], SELF)
  assert.equal(m!.time, localStamp(1700000000))
  const [bad] = normalizeMessages([row({ send_time: null })], SELF)
  assert.equal(bad!.time, '')
})

test('kind 映射：0/2 → text + 正文；其余类型占位 + kind 省略（v1 不猜未确认语义）', () => {
  const out = normalizeMessages(
    [
      row({ message_id: 1, content_type: 2, text: '你好' }),
      row({ message_id: 2, content_type: 0, text: '顶层' }),
      row({ message_id: 3, content_type: 5, text: null }),
      row({ message_id: 4, content_type: 1011 }),
      row({ message_id: 5, content_type: undefined }), // content_type 缺失
      row({ message_id: 6, content_type: 2, text: null, trusted: false }), // 提取失败
    ],
    SELF,
  )
  assert.deepEqual(
    out.map((m) => ({ kind: m.kind, text: m.text })),
    [
      { kind: 'text', text: '你好' },
      { kind: 'text', text: '顶层' },
      { kind: undefined, text: NON_TEXT_PLACEHOLDER },
      { kind: undefined, text: NON_TEXT_PLACEHOLDER },
      { kind: undefined, text: NON_TEXT_PLACEHOLDER },
      { kind: 'text', text: '' }, // 文本类型提取失败：留空，不误占位
    ],
  )
  // 占位文案可注入覆写
  const [custom] = normalizeMessages([row({ content_type: 5 })], SELF, { nonTextPlaceholder: '[图片]' })
  assert.equal(custom!.text, '[图片]')
})

test('排除表：CONFIRMED_SYSTEM_TYPES 冻结值、isParticipating、system 标注', () => {
  assert.deepEqual([...CONFIRMED_SYSTEM_TYPES], [1017, 38, 31])
  assert.equal(CURSOR_VERSION, 'v1')
  for (const ct of [1017, 38, 31]) {
    assert.equal(isParticipating(ct), false, String(ct))
  }
  for (const ct of [0, 2, 999, 1011]) {
    assert.equal(isParticipating(ct), true, String(ct)) // 最小排除表：未确认类型按非系统对待
  }
  assert.equal(isParticipating(Number.NaN), false) // 非数值保守不参与
  assert.equal(isParticipating(undefined), false)
  assert.equal(isParticipating('38' as unknown as number), false)

  const out = normalizeMessages(
    [
      row({ message_id: 1, content_type: 1017 }),
      row({ message_id: 2, content_type: 38 }),
      row({ message_id: 3, content_type: 31 }),
      row({ message_id: 4, content_type: 2, text: 'x' }),
    ],
    SELF,
  )
  assert.deepEqual(out.map((m) => m.system ?? false), [true, true, true, false])
  // 系统类同样占位 + kind 省略
  assert.equal(out[0]!.text, NON_TEXT_PLACEHOLDER)
  assert.equal(out[0]!.kind, undefined)
})

test('parse_trusted 透传：py 标注原样保留，不因占位/文本改变', () => {
  const out = normalizeMessages(
    [
      row({ message_id: 1, trusted: true, text: '可信' }),
      row({ message_id: 2, trusted: false, text: '兜底' }),
      row({ message_id: 3, trusted: undefined }), // 缺省视为 false
      row({ message_id: 4, content_type: 1017, trusted: false }),
    ],
    SELF,
  )
  assert.deepEqual(out.map((m) => m.parse_trusted), [true, false, false, false])
})

test('展示排序：时间升序；同秒 server_id 决胜；迟到回退重排；msg_id 稳定兜底', () => {
  const out = normalizeMessages(
    [
      row({ message_id: 31, send_time: 100, server_id: 'zzz' }), // 迟到回退：入库在前、时间更晚
      row({ message_id: 32, send_time: 99, server_id: 'aaa' }),
      row({ message_id: 33, send_time: 100, server_id: 'mmm' }), // 同秒按 server_id
      row({ message_id: 34, send_time: 100 }), // 无 server_id → 排在有 server_id 之后
      row({ message_id: 35, send_time: 100 }),
    ],
    SELF,
  )
  assert.deepEqual(out.map((m) => m.msg_id), [32, 34, 35, 33, 31])
  // server_id 缺省省略字段
  assert.equal(out.find((m) => m.msg_id === 34)!.server_id, undefined)
  assert.equal(out.find((m) => m.msg_id === 31)!.server_id, 'zzz')
})

test('防御：非对象行 / 无有效 message_id 的行丢弃，不抛出', () => {
  const dirty = [
    null,
    42,
    'row',
    { message_id: Number.NaN } as unknown as RawMessageRow,
    { message_id: -1 } as unknown as RawMessageRow,
    { message_id: 1.5 } as unknown as RawMessageRow,
    row({ message_id: 9, text: '有效' }),
  ] as unknown as RawMessageRow[]
  const out = normalizeMessages(dirty, SELF)
  assert.deepEqual(out.map((m) => m.msg_id), [9])
  assert.equal(normalizeMessages([], SELF).length, 0)
})

test('cursor roundtrip：普通值 / 含 | 与中文的 account/conversationId / message_id=0', () => {
  const cases = [
    { account: SELF, conversationId: 'S:1_2', lastMessageId: 42 },
    { account: 'a|b', conversationId: 'R:wrTdK群|名', lastMessageId: 0 },
    { account: '陆伟账号', conversationId: 'R:项目群', lastMessageId: 197 },
  ]
  for (const c of cases) {
    const encoded = encodeCursor(c)
    assert.ok(encoded.startsWith(`${CURSOR_VERSION}|`), encoded)
    assert.equal(encoded.split('|').length, 4, '转义后不得引入分隔符')
    assert.deepEqual(decodeCursor(encoded), { ok: true, ...c })
  }
})

test('decodeCursor：malformed 全形态（版本/段数/空字段/非数字/负数/溢出/坏转义/垃圾串）', () => {
  const good = encodeCursor({ account: 'a', conversationId: 'c', lastMessageId: 1 })
  assert.equal(decodeCursor(good).ok, true)
  const malformed = [
    '',
    'garbage',
    'v2|a|c|1', // 版本不符
    'v1|a|c', // 段数不足
    'v1|a|c|1|extra', // 段数超出
    'v1||c|1', // 空 account
    'v1|a||1', // 空 conversationId
    'v1|a|c|x', // 非数字
    'v1|a|c|-1', // 负数
    'v1|a|c|1.5', // 小数
    'v1|a|c|9007199254740993', // 超出安全整数
    'v1|%zz|c|1', // 坏转义序列
    `${good} `, // 尾随空白（严格形态，不宽容）
  ]
  for (const s of malformed) {
    const r = decodeCursor(s)
    assert.deepEqual(r, { ok: false, reason: 'malformed' }, s)
  }
})

test('encodeCursor：非法入参抛 TypeError（编程错误），不影响 decode 侧容错', () => {
  assert.throws(() => encodeCursor({ account: '', conversationId: 'c', lastMessageId: 1 }), TypeError)
  assert.throws(() => encodeCursor({ account: 'a', conversationId: '', lastMessageId: 1 }), TypeError)
  assert.throws(() => encodeCursor({ account: 'a', conversationId: 'c', lastMessageId: -1 }), TypeError)
  assert.throws(
    () => encodeCursor({ account: 'a', conversationId: 'c', lastMessageId: 1.5 }),
    TypeError,
  )
})

test('ensureCursorMatches：format/account/conversation 三类失配 + 命中', () => {
  const bound = { account: SELF, conversationId: 'S:1_2' }
  assert.deepEqual(ensureCursorMatches(encodeCursor({ ...bound, lastMessageId: 7 }), bound), { ok: true })
  assert.deepEqual(
    ensureCursorMatches('garbage', bound),
    { ok: false, reason: 'format-mismatch' },
  )
  assert.deepEqual(
    ensureCursorMatches(encodeCursor({ account: '999', conversationId: 'S:1_2', lastMessageId: 7 }), bound),
    { ok: false, reason: 'account-mismatch' },
  )
  assert.deepEqual(
    ensureCursorMatches(encodeCursor({ account: SELF, conversationId: 'R:other', lastMessageId: 7 }), bound),
    { ok: false, reason: 'conversation-mismatch' },
  )
  // 账号与会话都不符：按判定顺序只报首个失配维度（账号）
  assert.deepEqual(
    ensureCursorMatches(encodeCursor({ account: '999', conversationId: 'R:other', lastMessageId: 7 }), bound),
    { ok: false, reason: 'account-mismatch' },
  )
})

test('端到端形态：py 行集 → 归一输出字段齐备（msg_id/server_id/side/kind/text/time/parse_trusted）', () => {
  const out = normalizeMessages(
    [
      row({ message_id: 11, server_id: 'srv-1', sequence: 1, sender_id: PEER, conversation_id: 'S:x_y', flag: 0, text: '你好' }),
      row({ message_id: 12, server_id: 'srv-2', sequence: 2, sender_id: SELF, content_type: 1017, text: null, trusted: false }),
    ],
    SELF,
  )
  // kind 省略 = 键不存在（非 undefined 键），单独断言
  assert.equal('kind' in out[1]!, false)
  assert.equal(out[1]!.kind, undefined)
  const { kind: _kind, ...rest1 } = out[0]!
  assert.deepEqual(rest1, {
    msg_id: 11,
    server_id: 'srv-1',
    side: 'peer',
    text: '你好',
    time: localStamp(1700000000),
    parse_trusted: true,
  })
  const { kind: _kind2, ...rest2 } = out[1]!
  assert.deepEqual(rest2, {
    msg_id: 12,
    server_id: 'srv-2',
    side: 'self',
    text: NON_TEXT_PLACEHOLDER,
    time: localStamp(1700000000),
    parse_trusted: false,
    system: true,
  })
})
