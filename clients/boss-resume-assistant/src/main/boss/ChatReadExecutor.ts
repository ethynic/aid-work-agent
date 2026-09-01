/**
 * 沟通会话消息读取执行器（设计 §10.9 真机实验结论，2026-08-27，CLI read-chat / MCP boss_read_chat）。
 *
 * 已有 send-to/send-current 发消息能力，本执行器读「对方回了什么」：
 * 当前会话消息流（谁发的+正文+时间+已读）+ 左列表全部未读会话清单 + 左导航「沟通」总徽章。
 * **纯只读**：Deps 只有 snapshot，绝不引入 click/type/pressEscape——切换会话属写操作，
 * 走既有点击链路（本工具 contact 不匹配时报错请人工切换，不自动切）。
 *
 * DOM 模型（§10.9 真机验证，窗口 1249x1277）：
 * - 消息全部在主文档 doc[0]（非 iframe），一次 captureSnapshot 读全，无需输入/滚动。
 * - `.chat-conversation > .conversation-main > .conversation-message > .chat-message-list > .message-item`
 *   （消息组：时间行 + 气泡行）；气泡行 `.item-myself`（我方）/`.item-friend`（对方）/
 *   `.item-system`（职位卡片）；正文 `SPAN.text-content > #text` 整串连续；时间戳是
 *   `.message-item` 直接子行（全宽高≤24 文本，格式 " 10:56" 带前导空格）；
 *   「已读」是 `.item-myself` 行内 `I.status.status-read` 结构子节点。
 * - class 为实测稳定语义名（非混淆 hash）——class 定位仅限本执行器（其它页面铁律不变）。
 * - 多副本坑：同一消息文本同时存在于左列表预览与聊天气泡，必须限定 doc[0] +
 *   `.conversation-message` 子树；LIST_MAX_X=850 几何分界在聊天面板失效（面板起点 x=548）。
 * - 未读徽章：左列表 `SPAN.badge-count > #text` 纯数字，**含视口外全量可读**（列表未虚拟化）；
 *   徽章向上找 bounds 高 60~100 的 DIV 祖先 = 会话项（.geek-item），项内 x≈264=姓名、
 *   x≈497=时间、第二行 x≈264=最后一条预览（可能拆成 "[送达]" + 正文两个同排文本节点）。
 * - 左导航「沟通」总徽章（如 214）：x<188 区域 trim 后**全等**匹配 /^\d+$/ 的可见文本——
 *   禁用 includes（会命中几十个 SVG path 的 d 属性数据，同样进了 strings 表）。
 */
import {
  type DomDocument,
  type DomSnapshot,
  indexedValues,
  parentMapOf,
} from './domSnapshot.js'

export interface ChatMessage {
  /** 发送方：me=我方（item-myself）/ them=对方（item-friend）/ system=系统卡片（item-system） */
  sender: 'me' | 'them' | 'system'
  text: string
  /** 消息时间（所属组的时间行，组无时间行则向后继承上一时间行；如 "08-25 18:13" / "10:56" / "昨天 09:11"；会话无任何时间行时缺省） */
  ts?: string
  /** 仅我方消息：行内 I.status 带「已读」（class 含 status-read）时为 true */
  read?: boolean
}

export interface UnreadItem {
  name: string
  count: number
  time?: string
  lastPreview?: string
}

export interface ChatReadResult {
  /** 当前打开会话的联系人姓名（头部 .base-info-single-container 左上角文本；结构漂移读不到时为 ''） */
  contact: string
  messages: ChatMessage[]
  unread: UnreadItem[]
  /** 左导航「沟通」总徽章（无未读时页面不显示该徽章 → undefined） */
  totalUnreadBadge?: number
}

export class ChatReadError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatReadError'
  }
}

export interface ChatReadDeps {
  /** 采集 fresh DOMSnapshot（每会话切换后 nodeIndex 全变，禁止缓存） */
  snapshot(): Promise<DomSnapshot>
}

/** 文本节点向上爬找特征祖先的最大层数（§10.9：#text→SPAN.text-content→DIV.text→item-* 约 3-6 层，留余量） */
const MAX_CLIMB = 8
/** 时间行归属 message-item 组的爬层上限（真机 system 卡片 #text→H3→4 层 DIV→item-system→item-resume→message-item 共 8 层，留余量） */
const MAX_GROUP_CLIMB = 16
/** 时间行几何判定：行高上限（真机 message-time 高 20） */
const TIME_ROW_MAX_HEIGHT = 24
/** 时间行几何判定：行宽 ≥ 消息区宽的比例（真机 620/680≈0.91） */
const TIME_ROW_MIN_WIDTH_RATIO = 0.8
/** 会话项 DIV 高度区间（真机 geek-item 高 74） */
const ITEM_HEIGHT_RANGE: readonly [number, number] = [60, 100]
/** 姓名长度上限（会话项首行短文本过滤，职位名如「PHP 开发工程师」11 字被排除） */
const ITEM_NAME_MAX_CHARS = 8
/** 未读项时间列的 x 下限（真机时间在 x≈497 右列；姓名 x≈264 / 职位 x≈313） */
const ITEM_TIME_MIN_X = 450
/** 未读项预览列的 x 区间（真机 x≈264；[送达] 前缀可能拆成独立文本节点同排拼接） */
const ITEM_PREVIEW_X_RANGE: readonly [number, number] = [240, 320]
/** 左导航区右边界（总徽章 x<188；真机总徽章文本 x≈131） */
const NAV_MAX_X = 188
/** 首行内视为同一行的 y 容差（真机姓名 y=445 / 职位+时间 y=447 同排） */
const ROW_Y_TOLERANCE = 6
/** 会话项徽章向上找 DIV 祖先的层数（真机 #text→SPAN→DIV 名字区→geek-item 约 4 层） */
const MAX_ITEM_CLIMB = 12

/**
 * 时间行文本判定：真机消息组的时间分隔行为「MM-DD HH:MM」（如 "08-25 18:13"）；
 * 兼容「HH:MM」（当日消息组）、「今天/昨天[ HH:MM]」「M月D日[ HH:MM]」（跨日/跨年会话）。
 * 空串与非时间文本一律不匹配（防把普通短文本误挂为时间）。
 */
export function isTimeRowText(text: string): boolean {
  const t = text.trim()
  if (!t) return false
  return /^(?:\d{2}-\d{2}|\d{1,2}月\d{1,2}日|今天|昨天)?(?: ?\d{1,2}:\d{2})?$/.test(t)
}

interface TextNode {
  nodeIndex: number
  text: string
  bounds: [number, number, number, number]
}

/**
 * doc[0] 的一次性索引上下文（class/布局/父链/可见文本预计算，避免逐节点 O(n) 重扫）。
 * classOf 每次 O(n) 扫 attributes 列，消息解析要逐文本节点爬祖先——预计算成 Map。
 */
interface DocCtx {
  snapshot: DomSnapshot
  document: DomDocument
  parents: Map<number, number> | null
  /** nodeIndex → class 属性值（无 class 的节点不在表中） */
  classes: Map<number, string>
  /** nodeIndex → tagName（无 nodeName 字段时为空表，DIV 过滤自动失活） */
  tags: Map<number, string>
  /** nodeIndex → layout 下标（boundsOf O(1)；layout.nodeIndex.indexOf 每调用 O(n)，循环内用会 O(n²)） */
  layoutIndex: Map<number, number>
  /** 可见文本节点（trim 非空 + 非零 bounds），按 y 升序、x 升序 */
  texts: TextNode[]
}

/** 子树判定的爬层上限（与 domSnapshot.isDescendantOf 的 10000 对齐，深树安全） */
const MAX_DEPTH_CLIMB = 10000

function buildCtx(snapshot: DomSnapshot, document: DomDocument): DocCtx {
  // class：attributes[nodeIdx]=[nameIdx,valIdx,...]，name='class' 的 val 即 class 值
  const classes = new Map<number, string>()
  const attrs = document.nodes.attributes
  if (attrs) {
    for (const [nodeIndex, pairs] of indexedValues(attrs, 'attributes')) {
      if (!Array.isArray(pairs)) continue
      for (let i = 0; i + 1 < pairs.length; i += 2) {
        if (snapshot.strings[pairs[i]!] === 'class') {
          classes.set(nodeIndex, snapshot.strings[pairs[i + 1]!] ?? '')
          break
        }
      }
    }
  }
  const tags = new Map<number, string>()
  const nameField = document.nodes.nodeName
  if (nameField) {
    for (const [nodeIndex, stringIdx] of indexedValues(nameField, 'nodeName')) {
      tags.set(nodeIndex, snapshot.strings[stringIdx] ?? '')
    }
  }
  const layoutIndex = new Map<number, number>()
  document.layout.nodeIndex.forEach((nodeIndex, i) => layoutIndex.set(nodeIndex, i))
  const texts: TextNode[] = []
  for (const [nodeIndex, stringIdx] of indexedValues(document.nodes.nodeValue, 'nodeValue')) {
    if (stringIdx < 0) continue
    const text = snapshot.strings[stringIdx]
    if (typeof text !== 'string' || text.trim() === '') continue
    const li = layoutIndex.get(nodeIndex)
    if (li === undefined) continue
    const b = document.layout.bounds[li]
    if (!b || b.length !== 4 || (b[2] ?? 0) <= 0 || (b[3] ?? 0) <= 0) continue
    texts.push({ nodeIndex, text, bounds: [b[0]!, b[1]!, b[2]!, b[3]!] })
  }
  texts.sort((a, b) => a.bounds[1] - b.bounds[1] || a.bounds[0] - b.bounds[0])
  return { snapshot, document, parents: parentMapOf(document), classes, tags, layoutIndex, texts }
}

function classContains(ctx: DocCtx, nodeIndex: number, token: string): boolean {
  const cls = ctx.classes.get(nodeIndex)
  return cls !== undefined && cls.includes(token)
}

function isDiv(ctx: DocCtx, nodeIndex: number): boolean {
  return ctx.tags.get(nodeIndex) === 'DIV'
}

function boundsOf(ctx: DocCtx, nodeIndex: number): [number, number, number, number] | undefined {
  const li = ctx.layoutIndex.get(nodeIndex)
  if (li === undefined) return undefined
  const b = ctx.document.layout.bounds[li]
  return b && b.length === 4 ? [b[0]!, b[1]!, b[2]!, b[3]!] : undefined
}

/**
 * 子树判定（nodeIndex 是否为 ancestor 的子孙，含自身）——基于 ctx.parents 预计算映射逐层上爬。
 * 不能用公共 isDescendantOf：它每次调用都经 parentMapOf 重建全表 Map（5203 项），子树过滤
 * 场景（每徽章/会话项 × 全部文本节点）会退化成 O(n²)（真机快照实测 read() 10s）。
 * parents 缺失（快照无 parentIndex 字段）时返回 false，与 isDescendantOf 的 null→false 回退语义一致。
 */
function isWithin(ctx: DocCtx, nodeIndex: number, ancestor: number): boolean {
  const parents = ctx.parents
  if (!parents) return false
  let cur: number | undefined = nodeIndex
  for (let i = 0; i < MAX_DEPTH_CLIMB && cur !== undefined; i++) {
    if (cur === ancestor) return true
    const next = parents.get(cur)
    if (next === cur) break // 根节点自指/环，防死循环
    cur = next
  }
  return false
}

/** 沿 parentIndex 向上爬 ≤max 层，找第一个 class 含 token 的祖先（含自身）；返回其 nodeIndex 或 null */
function climbToClass(ctx: DocCtx, nodeIndex: number, token: string, max: number): number | null {
  if (!ctx.parents) return null
  let cur: number | undefined = nodeIndex
  for (let i = 0; i < max && cur !== undefined; i++) {
    if (classContains(ctx, cur, token)) return cur
    const next = ctx.parents.get(cur)
    if (next === cur) break
    cur = next
  }
  return null
}

/** 沿 parentIndex 向上爬 ≤max 层，找 class 含 tokens 之一的祖先（含自身）；返回 [nodeIndex, token] 或 null */
function climbToAnyClass(ctx: DocCtx, nodeIndex: number, tokens: readonly string[], max: number): { nodeIndex: number; token: string } | null {
  if (!ctx.parents) return null
  let cur: number | undefined = nodeIndex
  for (let i = 0; i < max && cur !== undefined; i++) {
    for (const token of tokens) {
      if (classContains(ctx, cur, token)) return { nodeIndex: cur, token }
    }
    const next = ctx.parents.get(cur)
    if (next === cur) break
    cur = next
  }
  return null
}

/** 找 doc[0] 内 class 含 token 且有非零 bounds 的 DIV（消息区/头部带等容器定位） */
function findClassDiv(ctx: DocCtx, token: string): { nodeIndex: number; count: number } {
  let found = -1
  let count = 0
  for (const nodeIndex of ctx.classes.keys()) {
    if (!classContains(ctx, nodeIndex, token)) continue
    const b = boundsOf(ctx, nodeIndex)
    if (!b || b[2] <= 0 || b[3] <= 0) continue
    if (!isDiv(ctx, nodeIndex)) continue
    count++
    found = nodeIndex
  }
  return { nodeIndex: found, count }
}

/** 子树内的可见文本（按 ctx.texts 预排序保持 y 序） */
function textsInSubtree(ctx: DocCtx, ancestor: number): TextNode[] {
  return ctx.texts.filter((tn) => isWithin(ctx, tn.nodeIndex, ancestor))
}

/** 解析当前会话消息流：逐文本节点按祖先特征归类（消息/已读标记/时间行/噪声） */
function readMessages(ctx: DocCtx, areaNode: number): ChatMessage[] {
  const areaBounds = boundsOf(ctx, areaNode)
  const areaWidth = areaBounds ? areaBounds[2] : 0

  interface Draft {
    msg: ChatMessage
    y: number
    senderRow: number // item-myself/item-friend/item-system 祖先 nodeIndex（已读标记按行匹配）
    group: number | null // message-item 祖先 nodeIndex（时间行按组挂 ts）
  }
  const drafts: Draft[] = []
  const readRows = new Set<number>()
  const groupTs = new Map<number, string>()
  let noiseCount = 0

  for (const tn of textsInSubtree(ctx, areaNode)) {
    const trimmed = tn.text.trim()

    // 已读标记：I.status 下的文本（如「已读」）不作为消息；位于 item-myself 行内则给该行消息标 read。
    // status-read class 或「已读」文案任一命中即算已读（status-unread 变体真机待验证：不标 read、不报错）
    const statusAnc = climbToClass(ctx, tn.nodeIndex, 'status', MAX_CLIMB)
    if (statusAnc !== null) {
      const rowAnc = climbToAnyClass(ctx, tn.nodeIndex, ['item-myself', 'item-friend', 'item-system'], MAX_CLIMB)
      if (rowAnc && rowAnc.token === 'item-myself' && (ctx.classes.get(statusAnc)?.includes('status-read') || trimmed === '已读')) {
        readRows.add(rowAnc.nodeIndex)
      }
      continue
    }

    const senderAnc = climbToAnyClass(ctx, tn.nodeIndex, ['item-myself', 'item-friend', 'item-system'], MAX_CLIMB)
    if (senderAnc) {
      // 正文只在 SPAN.text-content 下生效；item-system（职位卡片 H3 标题）例外——真机其正文无 text-content 祖先
      if (senderAnc.token === 'item-system' || climbToClass(ctx, tn.nodeIndex, 'text-content', MAX_CLIMB) !== null) {
        const groupAnc = climbToClass(ctx, tn.nodeIndex, 'message-item', MAX_GROUP_CLIMB)
        drafts.push({
          msg: {
            sender: senderAnc.token === 'item-myself' ? 'me' : senderAnc.token === 'item-friend' ? 'them' : 'system',
            text: trimmed,
          },
          y: tn.bounds[1],
          senderRow: senderAnc.nodeIndex,
          group: groupAnc,
        })
        continue
      }
      noiseCount++ // item 行内但不在 text-content 下的文本（忽略防噪声，计数诊断用）
      continue
    }

    // 时间行：短文本匹配时间格式（真机「MM-DD HH:MM」，兼容「HH:MM」「今天/昨天 HH:MM」「M月D日[ HH:MM]」）
    // 且所在行（DIV 祖先）宽≈消息区宽、高≤24 → 挂到所属 message-item 组
    if (isTimeRowText(trimmed) && ctx.parents) {
      let rowOk = false
      let cur: number | undefined = tn.nodeIndex
      for (let i = 0; i < MAX_CLIMB && cur !== undefined; i++) {
        const b = boundsOf(ctx, cur)
        if (b && isDiv(ctx, cur) && b[3] <= TIME_ROW_MAX_HEIGHT && areaWidth > 0 && b[2] >= areaWidth * TIME_ROW_MIN_WIDTH_RATIO) {
          rowOk = true
          break
        }
        const next = ctx.parents.get(cur)
        if (next === cur) break
        cur = next
      }
      if (rowOk) {
        const groupAnc = climbToClass(ctx, tn.nodeIndex, 'message-item', MAX_GROUP_CLIMB)
        if (groupAnc !== null) groupTs.set(groupAnc, trimmed)
        continue
      }
    }
    noiseCount++ // 无任何归属特征的子树文本（防噪声忽略；计数供诊断，不进结果契约）
  }

  // 消息区存在但一条消息都解析不出 → 大概率结构漂移（class 失效），fail-loud 请人工查看
  if (drafts.length === 0) {
    throw new ChatReadError(
      `消息区存在（node=${areaNode}）但未解析出任何消息（忽略噪声文本 ${noiseCount} 个）：页面结构可能已变化，请人工查看`,
    )
  }

  // 合并：按 y 升序输出；时间行向后继承（BOSS 时间分隔行描述其后所有消息，直到下一时间行）、已读挂行
  drafts.sort((a, b) => a.y - b.y)
  let lastTs: string | undefined
  return drafts.map((d) => {
    const msg: ChatMessage = { ...d.msg }
    if (d.group !== null && groupTs.has(d.group)) lastTs = groupTs.get(d.group)
    if (lastTs !== undefined) msg.ts = lastTs
    if (msg.sender === 'me' && readRows.has(d.senderRow)) msg.read = true
    return msg
  })
}

/** 头部识别带 .base-info-single-container 内 y 最小、x 最小的文本 = 联系人姓名（真机姓名在最左上） */
function readContactName(ctx: DocCtx): string {
  const { nodeIndex } = findClassDiv(ctx, 'base-info-single-container')
  if (nodeIndex < 0) return ''
  const texts = textsInSubtree(ctx, nodeIndex)
  if (texts.length === 0) return ''
  const minY = texts.reduce((m, t) => Math.min(m, t.bounds[1]), Infinity)
  const topRow = texts.filter((t) => t.bounds[1] <= minY + ROW_Y_TOLERANCE)
  topRow.sort((a, b) => a.bounds[0] - b.bounds[0])
  return topRow[0]!.text.trim()
}

/** 未读会话项定位：badge-count（子树纯数字文本）→ 向上找高 60~100 的 DIV 祖先 */
function locateUnreadItems(ctx: DocCtx): Array<{ nodeIndex: number; count: number; bounds: [number, number, number, number] }> {
  const out: Array<{ nodeIndex: number; count: number; bounds: [number, number, number, number] }> = []
  for (const badgeNode of ctx.classes.keys()) {
    if (!classContains(ctx, badgeNode, 'badge-count')) continue
    // 徽章子树第一个可见文本须为纯数字（「新」「首充礼」等非数字徽章跳过）
    const badgeTexts = textsInSubtree(ctx, badgeNode)
    if (badgeTexts.length === 0) continue
    const value = badgeTexts[0]!.text.trim()
    if (!/^\d+$/.test(value)) continue
    // 向上找 bounds 高 60~100 的 DIV 祖先 = 会话项（左导航「沟通」总徽章等无此祖先 → 跳过）
    let cur: number | undefined = badgeNode
    for (let i = 0; i < MAX_ITEM_CLIMB && cur !== undefined && ctx.parents; i++) {
      const next = ctx.parents.get(cur)
      if (next === undefined || next === cur) break
      cur = next
      const b = boundsOf(ctx, cur)
      if (b && isDiv(ctx, cur) && b[3] >= ITEM_HEIGHT_RANGE[0] && b[3] <= ITEM_HEIGHT_RANGE[1]) {
        if (!out.some((item) => item.nodeIndex === cur)) {
          out.push({ nodeIndex: cur, count: Number(value), bounds: b })
        }
        break
      }
    }
  }
  return out
}

/** 会话项内可见文本（排除徽章子树——count 徽章文本不参与姓名/时间/预览解析） */
function itemTextsExcludingBadges(ctx: DocCtx, itemNode: number): TextNode[] {
  const badgeNodes = [...ctx.classes.keys()].filter(
    (n) => classContains(ctx, n, 'badge-count') && isWithin(ctx, n, itemNode),
  )
  return textsInSubtree(ctx, itemNode).filter(
    (tn) => !badgeNodes.some((badge) => isWithin(ctx, tn.nodeIndex, badge)),
  )
}

/** 项内姓名：y 最小行的 x 最小短文本（≤8 字；真机首行姓名 x≈264 在最左，职位名更长更靠右） */
function readItemName(ctx: DocCtx, itemNode: number): string | null {
  const texts = itemTextsExcludingBadges(ctx, itemNode)
  if (texts.length === 0) return null
  const minY = texts.reduce((m, t) => Math.min(m, t.bounds[1]), Infinity)
  const topRow = texts.filter((t) => t.bounds[1] <= minY + ROW_Y_TOLERANCE && t.text.trim().length <= ITEM_NAME_MAX_CHARS)
  if (topRow.length === 0) return null
  topRow.sort((a, b) => a.bounds[0] - b.bounds[0])
  return topRow[0]!.text.trim()
}

/** 项内时间：右列（x≥450，真机 x≈497）y 最小的文本（真机为含换行缩进的 " 10:42"，trim） */
function readItemTime(ctx: DocCtx, itemNode: number): string | undefined {
  const texts = itemTextsExcludingBadges(ctx, itemNode).filter((t) => t.bounds[0] >= ITEM_TIME_MIN_X)
  if (texts.length === 0) return undefined
  texts.sort((a, b) => a.bounds[1] - b.bounds[1] || a.bounds[0] - b.bounds[0])
  return texts[0]!.text.trim()
}

/**
 * 项内最后一条预览：姓名行之下的第二行、x≈264 列文本；同排多文本按 x 拼接
 * （真机 "[送达]" 与正文拆成两个同排 #text），拼接后去 "[送达]" 前缀再 trim。
 */
function readItemPreview(ctx: DocCtx, itemNode: number): string | undefined {
  const texts = itemTextsExcludingBadges(ctx, itemNode)
  if (texts.length === 0) return undefined
  const minY = texts.reduce((m, t) => Math.min(m, t.bounds[1]), Infinity)
  const lowerRows = texts.filter(
    (t) => t.bounds[1] > minY + ROW_Y_TOLERANCE && t.bounds[0] >= ITEM_PREVIEW_X_RANGE[0] && t.bounds[0] < ITEM_PREVIEW_X_RANGE[1],
  )
  if (lowerRows.length === 0) return undefined
  const secondMinY = lowerRows.reduce((m, t) => Math.min(m, t.bounds[1]), Infinity)
  const row = lowerRows.filter((t) => t.bounds[1] <= secondMinY + ROW_Y_TOLERANCE)
  row.sort((a, b) => a.bounds[0] - b.bounds[0])
  const stripped = row.map((t) => t.text.trim()).join('').replace(/^\[送达\]/, '').trim()
  return stripped !== '' ? stripped : undefined
}

/** 未读清单：按会话项 y 升序输出（含视口外项——列表未虚拟化，bounds 全量可读） */
function readUnread(ctx: DocCtx): UnreadItem[] {
  return locateUnreadItems(ctx)
    .sort((a, b) => a.bounds[1] - b.bounds[1])
    .flatMap((item) => {
      const name = readItemName(ctx, item.nodeIndex)
      if (name === null) return [] // 姓名解析失败（结构漂移）→ 跳过该项（name 必填，宁缺毋错）
      const unread: UnreadItem = { name, count: item.count }
      const time = readItemTime(ctx, item.nodeIndex)
      if (time !== undefined) unread.time = time
      const preview = readItemPreview(ctx, item.nodeIndex)
      if (preview !== undefined) unread.lastPreview = preview
      return [unread]
    })
}

/** 左导航「沟通」总徽章：x<188、trim 后全等 /^\d+$/ 的可见文本（禁 includes——SVG path 的 d 属性数据全是数字片段） */
function readTotalBadge(ctx: DocCtx): number | undefined {
  const hits = ctx.texts.filter((t) => t.bounds[0] < NAV_MAX_X && /^\d+$/.test(t.text.trim()))
  if (hits.length === 0) return undefined
  if (hits.length === 1) return Number(hits[0]!.text.trim())
  // 多命中（导航其它菜单也挂纯数字徽章 / 结构漂移）：无法确定哪个是「沟通」总未读，fail-loud 请人工查看
  throw new ChatReadError(
    `左导航区域（x<${NAV_MAX_X}）发现 ${hits.length} 个纯数字徽章（${hits.map((h) => h.text.trim()).join('、')}），无法确定「沟通」总未读数，请人工查看`,
  )
}

/** 会话列表全部联系人姓名：.user-list 容器子树内 class 含 geek-item 的节点逐个解析（容器缺失时回退未读项全集），按出现顺序去重 */
function listContactNames(ctx: DocCtx): string[] {
  const names: string[] = []
  const itemNodes: number[] = []
  const listRoot = findClassDiv(ctx, 'user-list').nodeIndex // <0 时下方子树过滤自动放行全部 geek-item
  const collect = (nodeIndex: number) => {
    if (itemNodes.includes(nodeIndex)) return
    itemNodes.push(nodeIndex)
    const name = readItemName(ctx, nodeIndex)
    if (name !== null && !names.includes(name)) names.push(name)
  }
  for (const nodeIndex of ctx.classes.keys()) {
    if (!classContains(ctx, nodeIndex, 'geek-item')) continue
    if (listRoot >= 0 && !isWithin(ctx, nodeIndex, listRoot)) continue
    collect(nodeIndex)
  }
  for (const item of locateUnreadItems(ctx)) collect(item.nodeIndex)
  return names
}

export class ChatReadExecutor {
  constructor(private readonly deps: ChatReadDeps) {}

  /**
   * 读取当前会话消息 + 全部未读会话清单 + 总未读徽章（单次 snapshot，纯只读）。
   * contact 给定时校验当前打开的会话就是该联系人（trim 全等）；不匹配/未打开会话 →
   * 在会话列表里找该姓名：找到报「存在但未打开」（本工具不自动切换），找不到报错并列出可用姓名。
   */
  async read(opts: { contact?: string } = {}): Promise<ChatReadResult> {
    const snapshot = await this.deps.snapshot()
    return readChatWithSnapshot(snapshot, opts)
  }
}

/**
 * 共享读取入口（2026-08-27 抽取）：headerContactOf / conversationListNamesOf 与 read() 用同一套
 * 解析函数（buildCtx + readContactName + listContactNames），禁止复制粘贴两份几何/爬树逻辑。
 */
function readChatWithSnapshot(snapshot: DomSnapshot, opts: { contact?: string }): ChatReadResult {
  const document = snapshot.documents[0]
  if (!document) throw new ChatReadError('DOMSnapshot 缺少主文档 doc[0]，无法读取沟通会话')
  // attributes 缺失时所有 class 定位都不可用——与其误报「未打开会话」不如明说形状缺失（fail-loud 带诊断方向）
  if (!document.nodes.attributes) {
    throw new ChatReadError('DOMSnapshot 缺少 nodes.attributes 字段：无法按 class 解析沟通页结构，请检查快照采集配置')
  }
  const ctx = buildCtx(snapshot, document)

  // 消息区：class 含 conversation-message 且有 bounds 的 DIV（真机唯一；缺 → 未打开会话，fail-loud）
  const area = findClassDiv(ctx, 'conversation-message')
  if (area.count > 1) {
    throw new ChatReadError(
      `找到 ${area.count} 个 conversation-message 消息区容器（期望唯一）：页面结构可能已变化，请人工查看`,
    )
  }
  const areaOpen = area.count === 1
  const currentContact = areaOpen ? readContactName(ctx) : ''

  // contact 前置校验：当前未打开会话 / 头部姓名 ≠ contact → 在会话列表里找该姓名（fail-loud 给可操作指引）
  const wanted = opts.contact?.trim() ?? ''
  if (wanted !== '' && (!areaOpen || currentContact !== wanted)) {
    const available = listContactNames(ctx)
    if (available.includes(wanted)) {
      throw new ChatReadError(`会话「${wanted}」存在但未打开，请先切换（本工具不自动切换会话）`)
    }
    const list = available.length > 0 ? available.join('、') : '（列表为空或不可读）'
    throw new ChatReadError(`会话列表中不存在「${wanted}」，可用联系人：${list}`)
  }

  if (!areaOpen) {
    throw new ChatReadError('当前沟通页未打开会话（未找到 conversation-message 消息区）：请在左侧列表点开一个会话后重试')
  }

  const messages = readMessages(ctx, area.nodeIndex)
  const unread = readUnread(ctx)
  const totalUnreadBadge = readTotalBadge(ctx)
  return { contact: currentContact, messages, unread, ...(totalUnreadBadge !== undefined ? { totalUnreadBadge } : {}) }
}

/**
 * 头部当前会话联系人姓名（共享函数，ChatOpenExecutor 的 already-判定用）。
 * 读不到（无 doc[0] / 无 attributes / 头部结构漂移 / 未打开会话）返回 ''——调用方按
 * 「未在目标会话」处理，继续走搜索/列表路径，不在共享层 fail-loud（打开会话本身有兜底链路）。
 */
export function headerContactOf(snapshot: DomSnapshot): string {
  const document = snapshot.documents[0]
  if (!document?.nodes?.attributes) return ''
  return readContactName(buildCtx(snapshot, document))
}

/**
 * 会话列表全部联系人姓名（共享函数，ChatOpenExecutor 列表兜底/报错名单用）。
 * 读不到（形状缺失）返回空数组——调用方在报错文案里提示「列表为空或不可读」。
 */
export function conversationListNamesOf(snapshot: DomSnapshot): string[] {
  const document = snapshot.documents[0]
  if (!document?.nodes?.attributes) return []
  return listContactNames(buildCtx(snapshot, document))
}
