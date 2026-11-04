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

test('普通文本保留正文；未确认类型保留占位且省略kind', () => {
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
  assert.deepEqual([...CONFIRMED_SYSTEM_TYPES], [1017, 38, 31, 1001, 1002, 1006, 1022])
  assert.equal(CURSOR_VERSION, 'v2')
  for (const ct of [1017, 38, 31, 1001, 1002, 1006, 1022]) {
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
  assert.equal(out[0]!.kind, 'system')
  assert.equal(out[0]!.side, 'timeline')
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

test('机器人Markdown保留段落和引用；系统事件与同文普通发言按类型区分', () => {
  const markdown = '【面试通知】\n工程师岗位\n\n> 候选人甲 · 明天14:00\n> 请安排面试'
  const event = '群名变更：客户服务群'
  const out = normalizeMessages([
    row({ message_id: 1, content_type: 80, text: markdown, trusted: true }),
    row({ message_id: 2, sender_id: SELF, content_type: 1001, text: event }),
    row({ message_id: 3, sender_id: SELF, content_type: 2, text: event }),
    row({ message_id: 4, content_type: 80, text: '已知段落', trusted: false }),
    row({ message_id: 5, content_type: 80, text: null, trusted: false }),
  ], SELF)
  assert.equal(out[0]!.text, markdown)
  assert.equal(out[0]!.kind, 'text')
  assert.equal(out[0]!.text_format, 'markdown')
  assert.equal(out[1]!.side, 'timeline', '本人改群名也不是本人发言')
  assert.equal(out[1]!.kind, 'system')
  assert.equal(out[1]!.system, true)
  assert.equal(out[1]!.sender_id, SELF, '仍保留事件发起人')
  assert.equal(out[2]!.side, 'self', '不能按正文关键词把正常聊天误判系统事件')
  assert.equal(out[2]!.kind, 'text')
  assert.equal(out[2]!.system, undefined)
  assert.equal(out[3]!.text, '已知段落')
  assert.equal(out[3]!.parse_trusted, false)
  assert.equal(out[4]!.text, NON_TEXT_PLACEHOLDER)
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

const CURSOR_FIELDS = { generation: 'a'.repeat(64), anchor: 'b'.repeat(64) }

test('cursor roundtrip：普通值 / 含 | 与中文的 account/conversationId / message_id=0', () => {
  const cases = [
    { account: SELF, conversationId: 'S:1_2', lastMessageId: 42 },
    { account: 'a|b', conversationId: 'R:wrTdK群|名', lastMessageId: 0 },
    { account: '陆伟账号', conversationId: 'R:项目群', lastMessageId: 197 },
  ].map(c => ({ ...CURSOR_FIELDS, ...c, anchor: c.lastMessageId === 0 ? '' : CURSOR_FIELDS.anchor }))
  for (const c of cases) {
    const encoded = encodeCursor(c)
    assert.ok(encoded.startsWith(`${CURSOR_VERSION}|`), encoded)
    assert.equal(encoded.split('|').length, 6, '转义后不得引入分隔符')
    assert.deepEqual(decodeCursor(encoded), { ok: true, ...c })
  }
})

test('decodeCursor：malformed 全形态（版本/段数/空字段/非数字/负数/溢出/坏转义/垃圾串）', () => {
  const good = encodeCursor({ ...CURSOR_FIELDS, account: 'a', conversationId: 'c', lastMessageId: 1 })
  assert.equal(decodeCursor(good).ok, true)
  const parts = good.split('|')
  const changed = (index: number, value: string) => parts.map((part, i) => i === index ? value : part).join('|')
  const malformed = [
    '', 'garbage', 'v1|a|c|1', parts.slice(1).join('|'), `${good}|extra`,
    changed(1, ''), changed(2, ''), changed(1, '%zz'), changed(3, 'no-generation'),
    changed(4, ''), changed(5, '-1'), changed(5, '1.5'), changed(5, '9007199254740993'), `${good} `,
  ]
  for (const s of malformed) {
    const r = decodeCursor(s)
    assert.deepEqual(r, { ok: false, reason: 'malformed' }, s)
  }
})

test('encodeCursor：非法入参抛 TypeError（编程错误），不影响 decode 侧容错', () => {
  assert.throws(() => encodeCursor({ ...CURSOR_FIELDS, account: '', conversationId: 'c', lastMessageId: 1 }), TypeError)
  assert.throws(() => encodeCursor({ ...CURSOR_FIELDS, account: 'a', conversationId: '', lastMessageId: 1 }), TypeError)
  assert.throws(() => encodeCursor({ ...CURSOR_FIELDS, account: 'a', conversationId: 'c', lastMessageId: -1 }), TypeError)
  assert.throws(
    () => encodeCursor({ ...CURSOR_FIELDS, account: 'a', conversationId: 'c', lastMessageId: 1.5 }),
    TypeError,
  )
})

test('ensureCursorMatches：format/account/conversation 三类失配 + 命中', () => {
  const bound = { account: SELF, conversationId: 'S:1_2' }
  assert.deepEqual(ensureCursorMatches(encodeCursor({ ...CURSOR_FIELDS, ...bound, lastMessageId: 7 }), bound), { ok: true })
  assert.deepEqual(
    ensureCursorMatches('garbage', bound),
    { ok: false, reason: 'format-mismatch' },
  )
  assert.deepEqual(
    ensureCursorMatches(encodeCursor({ ...CURSOR_FIELDS, account: '999', conversationId: 'S:1_2', lastMessageId: 7 }), bound),
    { ok: false, reason: 'account-mismatch' },
  )
  assert.deepEqual(
    ensureCursorMatches(encodeCursor({ ...CURSOR_FIELDS, account: SELF, conversationId: 'R:other', lastMessageId: 7 }), bound),
    { ok: false, reason: 'conversation-mismatch' },
  )
  // 账号与会话都不符：按判定顺序只报首个失配维度（账号）
  assert.deepEqual(
    ensureCursorMatches(encodeCursor({ ...CURSOR_FIELDS, account: '999', conversationId: 'R:other', lastMessageId: 7 }), bound),
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
  assert.equal(out[1]!.kind, 'system')
  const { kind: _kind, ...rest1 } = out[0]!
  assert.deepEqual(rest1, {
    msg_id: 11,
    sender_id: PEER,
    sender_name: null,
    sender_platform: 'unknown',
    server_id: 'srv-1',
    side: 'peer',
    text: '你好',
    time: localStamp(1700000000),
    parse_trusted: true,
    content_type: 2,
    send_time: 1700000000,
    sequence: 1,
    flag: 0,
  })
  const { kind: _kind2, ...rest2 } = out[1]!
  assert.deepEqual(rest2, {
    msg_id: 12,
    sender_id: SELF,
    sender_name: null,
    sender_platform: 'unknown',
    server_id: 'srv-2',
    side: 'timeline',
    text: '[系统通知]',
    time: localStamp(1700000000),
    parse_trusted: false,
    system: true,
    content_type: 1017,
    send_time: 1700000000,
    sequence: 2,
  })
})

// ============ observed_chat 片段与日期模式校验（wecom-conversation 设计 §3.1.1/§4.1） ============

import { buildObservedChat } from '../src/operations/messageSend.js'
import { dayPlaceholderMessage, validateSessionRead } from '../src/platform/localdb/sessions.js'

test('buildObservedChat：优先确认命中并按预算装邻近记录，装不下标 truncated', () => {
  const rows = [
    { message_id: 1, sender_id: 'acc', content_type: 2, send_time: 100, conversation_id: 'R:g' },
    { message_id: 2, sender_id: 'peer', content_type: 2, send_time: 101, conversation_id: 'R:g' },
    { message_id: 3, sender_id: 'acc', content_type: 2, send_time: 102, conversation_id: 'R:g' },
  ]
  const fragment = buildObservedChat(rows as never, 'acc', 'R:g', 'gen1', 3, false)
  assert.ok(fragment)
  assert.equal(fragment.session, 'ws1.' + Buffer.from(JSON.stringify({ account: 'acc', conversation_id: 'R:g' })).toString('base64url'))
  assert.equal(fragment.source_generation, 'gen1')
  assert.equal(fragment.confirmed_message_id, 3)
  assert.equal((fragment.messages as unknown[]).length, 3)
  assert.equal(fragment.truncated, undefined)
  // 超预算：P0 冻结包装正文（0a..08 00 12..0a..utf8）保证真实字节大小
  const varint = (n: number): number[] => {
    const out: number[] = []
    for (;;) {
      const b7 = n & 0x7f
      n >>>= 7
      if (n) out.push(b7 | 0x80)
      else { out.push(b7); return out }
    }
  }
  const wrapText = (text: string): Buffer => {
    const raw = Buffer.from(text, 'utf8')
    const inner = Buffer.from([0x0a, ...varint(raw.length), ...raw])
    const mid = Buffer.from([0x08, 0x00, 0x12, ...varint(inner.length), ...inner])
    return Buffer.from([0x0a, ...varint(mid.length), ...mid])
  }
  const big = [
    { message_id: 1, sender_id: 'acc', content_type: 2, send_time: 100, conversation_id: 'R:g', content: wrapText('确认命中的正文') },
    ...Array.from({ length: 40 }, (_, index) => ({
      message_id: 2 + index, sender_id: 'peer', content_type: 2, send_time: 101 + index,
      conversation_id: 'R:g', content: wrapText('邻近记录'.repeat(60)),
    })),
  ]
  const truncated = buildObservedChat(big as never, 'acc', 'R:g', 'gen1', 1, false)
  assert.ok(truncated)
  assert.equal(truncated.truncated, true)
  assert.ok((truncated.messages as { msg_id: number }[]).length < big.length)
  assert.equal((truncated.messages as { msg_id: number }[])[0]!.msg_id, 1) // 确认命中优先保留
})

test('buildObservedChat：空窗口/组装失败整体省略，不影响原发送结论', () => {
  assert.equal(buildObservedChat([], 'acc', 'R:g', 'gen1', null, false), null)
})

// 评审三 P2-6：正文降级保留身份——占位由归一消息展开，仅替换正文/引用字段。
// readSessionDay 内联使用同构（spread placeholder + 文案/unreadable_reason），
// 这里用与实现一致的字段集合断言防护回归。
test('正文降级占位保留发言人/平台/原类型/ID/时间', () => {
  const normalized = {
    msg_id: 7, sender_id: 'acc', sender_name: 'Known Customer', sender_platform: 'wechat',
    server_id: 'srv-7', side: 'self', kind: 'text', text: '巨'.repeat(20000),
    time: '2026-11-04 10:00:00', parse_trusted: true, content_type: 2, send_time: 100,
    mentions: [], mentions_complete: false, sender_name_source: 'user_table',
  } as never
  const downgraded = dayPlaceholderMessage(normalized) as Record<string, unknown>
  for (const key of ['msg_id', 'sender_id', 'sender_name', 'sender_platform', 'server_id',
    'side', 'kind', 'content_type', 'send_time', 'time']) {
    assert.equal(downgraded[key], (normalized as Record<string, unknown>)[key], key)
  }
  // 合成占位正文不可信（评审四 P2-2）：原消息可信，占位必须置 false
  assert.equal((normalized as Record<string, unknown>).parse_trusted, true)
  assert.equal(downgraded.parse_trusted, false)
  assert.match(String(downgraded.text), /超出单页大小限制/)
})

test('validateSessionRead 日期模式：单会话+稳定标识才可用；续页不能用增量游标', () => {
  assert.equal(validateSessionRead({ sessions: ['ws1.x'], single: true, date: '2026-11-03' }), null)
  assert.equal(validateSessionRead({ sessions: ['ws1.x'], single: true, date: '2026-11-03', include_context: true, timezone: 'Asia/Shanghai', limit: 100 }), null)
  assert.match(validateSessionRead({ sessions: ['群名'], single: true, date: '2026-11-03' }) ?? '', /稳定会话标识/)
  assert.match(validateSessionRead({ sessions: ['ws1.x'], single: true, date: '2026/11/03' }) ?? '', /YYYY-MM-DD/)
  assert.match(validateSessionRead({ sessions: ['ws1.x'], single: true, date: '2026-11-03', attachment: { message_id: 1 } }) ?? '', /分开使用/)
  assert.match(validateSessionRead({ sessions: ['ws1.x'], single: true, date: '2026-11-03', cursor: 'wb1.abc' }) ?? '', /日期读取返回的游标/)
  assert.match(validateSessionRead({ sessions: ['ws1.x', 'ws1.y'], date: '2026-11-03' }) ?? '', /稳定会话标识/)
  assert.match(validateSessionRead({ sessions: ['ws1.x'], single: true, include_context: true }) ?? '', /日期模式可用/)
})

test('validateSessionRead：仅 wd1 游标的续页按日期模式受理（评审 P1-2）', () => {
  assert.equal(validateSessionRead({ sessions: ['ws1.x'], single: true, cursor: 'wd1.eyJhIjoxfQ' }), null)
  assert.match(validateSessionRead({ sessions: ['ws1.x'], single: true, cursor: 'wd1.abc', limit: 101 }) ?? '', /limit/)
  assert.match(validateSessionRead({ sessions: ['ws1.x'], single: true, cursor: 'wd1.abc', attachment: { message_id: 1 } }) ?? '', /分开使用/)
})

test('buildObservedChat：首条超预算不再无条件装入（评审 P2-20）', () => {
  const varint2 = (n: number): number[] => {
    const out: number[] = []
    for (;;) {
      const b7 = n & 0x7f
      n >>>= 7
      if (n) out.push(b7 | 0x80)
      else { out.push(b7); return out }
    }
  }
  const wrap2 = (text: string): Buffer => {
    const raw = Buffer.from(text, 'utf8')
    const inner = Buffer.from([0x0a, ...varint2(raw.length), ...raw])
    const mid = Buffer.from([0x08, 0x00, 0x12, ...varint2(inner.length), ...inner])
    return Buffer.from([0x0a, ...varint2(mid.length), ...mid])
  }
  const huge = [{
    message_id: 1, sender_id: 'acc', sender_name: '本人', content_type: 2, send_time: 100,
    conversation_id: 'R:g', text: '巨'.repeat(12000), parse_trusted: true,
  }]
  // 确认命中单条也超预算：整体省略（评审 P2-20 允许"装不下省略"），
  // 发送结论不受影响；窗口本身有截断时保留带缺口标注的空片段。
  assert.equal(buildObservedChat(huge as never, 'acc', 'R:g', 'gen1', 1, false), null)
  const marker = buildObservedChat(huge as never, 'acc', 'R:g', 'gen1', 1, true)
  assert.ok(marker)
  assert.equal((marker.messages as unknown[]).length, 0)
  assert.equal(marker.truncated, true)
})
