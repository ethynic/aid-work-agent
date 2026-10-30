/**
 * C9b 消息契约归一 + 游标编解码（任务 1.9b；设计 §4.1 消息契约 / 水位规则）。
 *
 * 输入行来自 py query 模式（drivers/py/wecom_localdb.py task=messages，按 message_id 升序
 * = 入库序/水位序）。本组件三件事：
 *  1. 契约归一：side / kind / text / time / parse_trusted + 系统类标注（system）。
 *     类型语义按 P0 冻结最小集：content_type 0/2 → 文本（kind='text'，text=py 提取正文）；
 *     其余类型【一律】占位 '[非文本消息]' 且 kind 省略——P0 只确认了 0/2 的文本语义，
 *     1011/1017/38/31 等仅确认存在、语义未确认，v1 不猜（P3 全量语义表落地后再升级为
 *     结构化表示；对齐设计 §4.1"占位 + kind 省略"决策，不做 OCR 补读）。
 *  2. 展示排序：时间升序；同秒以 server_id 决胜（P0 冻结：sequence/server_id 同秒递增），
 *     仍并列以 msg_id（入库序）稳定兜底。入库序（py 返回序）与展示序在此分离——游标推进
 *     只依据 py 的 next_cursor_id（返回集最大 message_id），与本组件展示排序无关（评审
 *     v4 #40：迟到回退消息按时间排前，但不把游标推进到未返回记录）。
 *  3. 游标编解码：'v2|<account>|<conversationId>|<generation>|<anchor>|<lastMessageId>'，account/conversationId
 *     经 encodeURIComponent 转义（'|' 等保留字符安全，字段内不可能引入分隔符）；绑定
 *     {account, conversationId, generation} 校验供 2.2 的 CURSOR_UNSUPPORTED 判定；
 *     非零水位携带消息稳定字段摘要，检测同文件重建后的水位复用。
 *
 * 防御语义（对 py 输出不信任的显式边界）：
 * - side：sender_id 与 selfUserId 严格字符串相等（不 trim；大小写敏感），非字符串一律 'peer'。
 * - isParticipating：排除表命中 → false；非数值 content_type → false（无法判定的类型保守
 *   不驱动自动回复）；其余数值类型 → true（排除表是"已确认系统类"的最小表，P3 再扩）。
 * - time：send_time 非有限非负数 → ''（数据异常显式留空，不臆造时间）；格式
 *   'YYYY-MM-DD HH:MM:SS' 本地时区。
 * - text：文本类型 py 未提取出正文（null）→ ''（保留 parse_trusted=false 供消费侧识别）；
 *   parse_trusted=false 只标注不改变 text（UI 呈现策略归后续任务）。
 * - 无有效 message_id（非安全整数）的行丢弃；非对象行跳过。
 */

/** P0 观察确认的系统/控制类 content_type 最小排除表（不参与待回复判定；P3 全量表再扩）。
 * 与 py 侧 CONFIRMED_SYSTEM_TYPES（drivers/py/wecom_localdb.py）保持同值，两处均为冻结契约。 */
export const CONFIRMED_SYSTEM_TYPES: readonly number[] = [1017, 38, 31]

/** v1 非文本占位文案（设计 §4.1：占位 + kind 省略） */
export const NON_TEXT_PLACEHOLDER = '[非文本消息]'

/** 游标格式版本前缀（绑定格式版本；语义变更须升版本） */
export const CURSOR_VERSION = 'v2'

/** py query task=messages 的行契约（drivers/py/wecom_localdb.py _message_row） */
export interface RawMessageRow {
  message_id: number
  server_id?: string | null
  sequence?: number | null
  sender_id?: string | null
  conversation_id?: string | null
  content_type?: number | null
  send_time?: number | null
  flag?: number | null
  text?: string | null
  trusted?: boolean
  sender_name?: string | null
  sender_platform?: 'wechat' | 'wecom' | 'unknown'
}

/** 归一消息（read-session 本地库通道消息契约：{side, kind?, text, time} + 增量可选字段） */
export interface NormalizedMessage {
  /** 水位基元 = message_id（入库序）；游标推进依据 */
  msg_id: number
  sender_id: string | null
  sender_name: string | null
  sender_platform: 'wechat' | 'wecom' | 'unknown'
  /** 同秒展示排序决胜键（P0 冻结）；缺省省略 */
  server_id?: string
  /** 方向 = sender_id 与本人账号严格相等；非字符串防御为 'peer' */
  side: 'self' | 'peer'
  /** v1 仅文本映射 'text'；其余类型占位且省略（语义未确认不猜） */
  kind?: 'text' | 'image' | 'file' | 'system'
  /** 文本正文 / 非文本占位 / 文本提取失败留空（parse_trusted=false 供消费侧识别） */
  text: string
  /** 'YYYY-MM-DD HH:MM:SS' 本地时区；send_time 非法 → '' */
  time: string
  /** py 内容提取可信标注：false = 递归兜底结果或非文本（正文不可作精确匹配依据） */
  parse_trusted: boolean
  /** 排除表类型（已确认系统/控制类）标注；仅供待回复判定消费，缺省 = 非系统 */
  system?: boolean
}

export interface NormalizeDeps {
  /** 非文本占位文案覆写（缺省 NON_TEXT_PLACEHOLDER；测试/本地化注入用） */
  nonTextPlaceholder?: string
}

function isSafeInt(v: unknown): v is number {
  return typeof v === 'number' && Number.isSafeInteger(v)
}

/**
 * 待回复参与性判定（P2 消费）：排除表（CONFIRMED_SYSTEM_TYPES）命中 → false；
 * 非数值 → false（保守）；其余 → true。排除表只决定"不参与"，不反向证明"必参与"
 * （未确认类型按非系统对待是最小排除表的既定语义，P3 全量表再收紧）。
 */
export function isParticipating(contentType: unknown): boolean {
  if (!isSafeInt(contentType)) return false
  return !CONFIRMED_SYSTEM_TYPES.includes(contentType)
}

/** send_time（秒）→ 'YYYY-MM-DD HH:MM:SS' 本地时区；非法（负数/非整数）→ ''。 */
export function formatTimestamp(seconds: unknown): string {
  if (!isSafeInt(seconds) || seconds < 0) return ''
  const d = new Date(seconds * 1000)
  const p2 = (n: number): string => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}` +
    ` ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`
  )
}

/**
 * 行集 → 归一消息（展示序）。输入为 py 入库序（message_id 升序），输出按
 * time 升序 → server_id 升序 → msg_id 升序（同秒决胜 + 稳定兜底）。
 */
export function normalizeMessages(
  rows: RawMessageRow[],
  selfUserId: string,
  deps: NormalizeDeps = {},
): NormalizedMessage[] {
  const placeholder = deps.nonTextPlaceholder ?? NON_TEXT_PLACEHOLDER
  const out: NormalizedMessage[] = []
  for (const row of rows) {
    if (row === null || typeof row !== 'object') continue
    if (!isSafeInt(row.message_id) || row.message_id < 0) continue // 无有效水位字段 → 丢弃
    const ct = isSafeInt(row.content_type) ? row.content_type : null
    const isTextType = ct === 0 || ct === 2
    const msg: NormalizedMessage = {
      msg_id: row.message_id,
      sender_id: typeof row.sender_id === 'string' ? row.sender_id : null,
      sender_name: typeof row.sender_name === 'string' ? row.sender_name : null,
      sender_platform: row.sender_platform === 'wechat' || row.sender_platform === 'wecom' ? row.sender_platform : 'unknown',
      side: row.sender_id === selfUserId ? 'self' : 'peer', // 严格相等不 trim；非字符串 → peer
      text: isTextType ? (typeof row.text === 'string' ? row.text : '') : placeholder,
      time: formatTimestamp(row.send_time),
      parse_trusted: row.trusted === true,
    }
    if (typeof row.server_id === 'string' && row.server_id.length > 0) msg.server_id = row.server_id
    if (isTextType) msg.kind = 'text' // 其余类型 kind 省略（占位语义，设计 §4.1）
    if (ct !== null && !isParticipating(ct)) msg.system = true
    out.push(msg)
  }
  out.sort((a, b) => {
    if (a.time !== b.time) return a.time < b.time ? -1 : 1 // 'YYYY-MM-DD HH:MM:SS' 字典序=时序
    const sa = a.server_id ?? ''
    const sb = b.server_id ?? ''
    if (sa !== sb) {
      if (/^\d+$/.test(sa) && /^\d+$/.test(sb)) {
        const left = BigInt(sa)
        const right = BigInt(sb)
        if (left !== right) return left < right ? -1 : 1
      } else return sa < sb ? -1 : 1
    }
    return a.msg_id - b.msg_id
  })
  return out
}

// ---------- 游标编解码（水位绑定 {account, conversationId, 格式版本}；基元=message_id） ----------

/** 游标载荷（message_id 空间；绑定账号与会话，跨账号/跨会话不可复用） */
export interface ReadCursor {
  account: string
  conversationId: string
  lastMessageId: number
  generation: string
  anchor: string
}

export type CursorDecodeResult =
  | ({ ok: true } & ReadCursor)
  | { ok: false; reason: 'malformed' }

/**
 * 编码游标：'v2|<account>|<conversationId>|<generation>|<anchor>|<lastMessageId>'（字段 encodeURIComponent 转义，
 * 保留字符含 '|' 均安全）。入参非法（空 account/conversationId、非安全整数 lastMessageId）
 * 抛 TypeError——属调用方编程错误而非数据错误；数据侧合法性由 decodeCursor 判定。
 */
export function encodeCursor(cursor: ReadCursor): string {
  if (typeof cursor.account !== 'string' || cursor.account.length === 0) {
    throw new TypeError('encodeCursor: account 必须为非空字符串')
  }
  if (typeof cursor.conversationId !== 'string' || cursor.conversationId.length === 0) {
    throw new TypeError('encodeCursor: conversationId 必须为非空字符串')
  }
  if (!isSafeInt(cursor.lastMessageId) || cursor.lastMessageId < 0) {
    throw new TypeError('encodeCursor: lastMessageId 必须为非负安全整数')
  }
  if (!/^[0-9a-f]{64}$/.test(cursor.generation) ||
      !(cursor.lastMessageId === 0 ? cursor.anchor === '' : /^[0-9a-f]{64}$/.test(cursor.anchor))) {
    throw new TypeError('encodeCursor: 数据库代际或水位锚点非法')
  }
  return [
    CURSOR_VERSION,
    encodeURIComponent(cursor.account),
    encodeURIComponent(cursor.conversationId),
    cursor.generation,
    cursor.anchor,
    String(cursor.lastMessageId),
  ].join('|')
}

/** 解码游标：格式/版本/字段任一不合法 → {ok:false, reason:'malformed'}（不抛）。 */
export function decodeCursor(s: string): CursorDecodeResult {
  if (typeof s !== 'string') return { ok: false, reason: 'malformed' }
  const parts = s.split('|')
  if (parts.length !== 6) return { ok: false, reason: 'malformed' }
  const [version, accountEnc, convEnc, generation, anchor, idRaw] = parts as [string, string, string, string, string, string]
  if (version !== CURSOR_VERSION) return { ok: false, reason: 'malformed' }
  if (accountEnc.length === 0 || convEnc.length === 0) return { ok: false, reason: 'malformed' }
  let account: string
  let conversationId: string
  try {
    account = decodeURIComponent(accountEnc)
    conversationId = decodeURIComponent(convEnc)
  } catch {
    return { ok: false, reason: 'malformed' } // 转义序列损坏
  }
  if (!/^\d+$/.test(idRaw)) return { ok: false, reason: 'malformed' }
  const lastMessageId = Number(idRaw)
  if (!Number.isSafeInteger(lastMessageId) || !/^[0-9a-f]{64}$/.test(generation) ||
      !(lastMessageId === 0 ? anchor === '' : /^[0-9a-f]{64}$/.test(anchor))) return { ok: false, reason: 'malformed' }
  if (!account || !conversationId) return { ok: false, reason: 'malformed' }
  return { ok: true, account, conversationId, lastMessageId, generation, anchor }
}

export type CursorMatchResult =
  | { ok: true }
  | { ok: false; reason: 'format-mismatch' | 'account-mismatch' | 'conversation-mismatch' | 'generation-mismatch' }

/**
 * 游标绑定校验（供 2.2 的 CURSOR_UNSUPPORTED 判定）：格式坏 → 'format-mismatch'；
 * 账号不符 → 'account-mismatch'；会话不符 → 'conversation-mismatch'（顺序判定：
 * 格式 → 账号 → 会话，一次只报首个失配维度）。
 */
export function ensureCursorMatches(
  cursor: string,
  bound: { account: string; conversationId: string; generation?: string },
): CursorMatchResult {
  const decoded = decodeCursor(cursor)
  if (!decoded.ok) return { ok: false, reason: 'format-mismatch' }
  if (decoded.account !== bound.account) return { ok: false, reason: 'account-mismatch' }
  if (decoded.conversationId !== bound.conversationId) {
    return { ok: false, reason: 'conversation-mismatch' }
  }
  if (bound.generation !== undefined && decoded.generation !== bound.generation) {
    return { ok: false, reason: 'generation-mismatch' }
  }
  return { ok: true }
}
