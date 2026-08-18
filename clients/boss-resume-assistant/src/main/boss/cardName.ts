/**
 * 推荐牛人卡片行「打招呼按钮 → 候选人姓名」配对（共享模块）。
 *
 * 真机锚定（2026-08-17，视口 1249x1277）：卡片行 = 右侧「打招呼」按钮（x≈1162）
 * + 行左上「姓名 + 活跃状态」同行（如 刘草威@342,138 / 刚刚活跃@400,138 / 按钮 y=146）。
 * 配对规则：按钮同行上方带（|dy|<40、x<按钮x）内找含「活跃」的短文本节点（trim≤6 字），
 * 姓名 = 它左侧最近（同行 y±8、dx<120）的 2-4 字中文节点；配对失败返回 null。
 *
 * 使用方（同一套锚定规则，保证「读到的人」与「打招呼的人」是同一个人）：
 * - ResumeBatchReader：批量读简历时给卡片配姓名（配对失败用 OCR 首行启发式兜底）
 * - GreetExecutor：定向打招呼时按姓名匹配卡片（配对失败的按钮一律跳过，宁可不打不能打错）
 */
import {
  type DomSnapshot,
  type ClickPoint,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues,
} from './domSnapshot.js'

/** 「打招呼」按钮文本（DOM 抓取的原始文本 trim 后精确相等） */
export const GREET_TEXT = '打招呼'

/** 活跃状态文本特征：含「活跃」（OCR/DOM 均稳定），如「刚刚活跃」「今日活跃」 */
const NAME_STATUS_PATTERN = /活跃/
/** 活跃状态文本最大长度（trim 后字数）：真机「刚刚活跃」4 字，≤6 排除混入的长文本 */
const STATUS_MAX_LEN = 6
/** 活跃状态节点与「打招呼」按钮同行的判定带宽（|dy|<40；真机 dy≈8） */
const STATUS_MAX_DY = 40
/** 姓名节点与活跃状态节点同行的 y 容差（真机同行 dy≈0） */
const NAME_SAME_LINE_BAND = 8
/** 姓名节点在活跃状态节点左侧的最大 x 距离（真机 dx≈58） */
const NAME_MAX_DX = 120
/** 姓名文本模式：2-4 字中文（含·），排除 本科/上海/期望 等干扰词以外的杂文本 */
const NAME_PATTERN = /^[\u4e00-\u9fa5·]{2,4}$/

/** 「打招呼」按钮引用：点击点（device px 屏幕坐标）+ 所在文档序号（姓名配对需回同文档找文本节点） */
export interface GreetButtonRef {
  point: ClickPoint
  documentIndex: number
}

/**
 * 姓名配对（真机锚定规则，2026-08-17 从 ResumeBatchReader 抽出的共享实现，行为零变化）：
 * 按钮同行上方带（|dy|<40、x<按钮x）内找含「活跃」的短文本节点（trim≤6 字），
 * 姓名 = 它左侧最近（同行 y±8、dx<120）的 2-4 字中文节点。配对失败返回 null。
 */
export function pairCardName(
  snap: DomSnapshot,
  btn: GreetButtonRef,
  viewport: { width: number; height: number },
): string | null {
  const doc = snap.documents[btn.documentIndex]!
  let offset: { x: number; y: number }
  try {
    offset = accumulateOwnerOffset(snap, btn.documentIndex)
  } catch {
    return null
  }
  // 该 document 内有 bounds 且视口可见的文本节点（屏幕坐标，取中心）
  const texts: Array<{ t: string; x: number; y: number }> = []
  for (const [nodeIndex, valueIndex] of indexedValues(doc.nodes.nodeValue, 'nodeValue')) {
    const t = snap.strings[valueIndex]
    if (typeof t !== 'string' || !t.trim()) continue
    const layoutIndex = doc.layout.nodeIndex.indexOf(nodeIndex)
    if (layoutIndex < 0) continue
    const b = doc.layout.bounds[layoutIndex]!
    if (!b || b[2]! <= 0 || b[3]! <= 0) continue
    const c = boundsCenter([b[0]!, b[1]!, b[2]!, b[3]!])
    const x = offset.x + c.x - (doc.scrollOffsetX ?? 0)
    const y = offset.y + c.y - (doc.scrollOffsetY ?? 0)
    if (x <= 0 || x > viewport.width || y <= 0 || y > viewport.height) continue
    texts.push({ t: t.trim(), x: Math.round(x), y: Math.round(y) })
  }
  // 活跃状态节点：离按钮最近者优先（真机一行一个，防相邻行串扰）
  const statuses = texts
    .filter((n) => n.t.length <= STATUS_MAX_LEN && NAME_STATUS_PATTERN.test(n.t))
    .filter((n) => Math.abs(n.y - btn.point.y) < STATUS_MAX_DY && n.x < btn.point.x)
    .sort((a, b) => Math.abs(a.y - btn.point.y) - Math.abs(b.y - btn.point.y) || a.x - b.x)
  const status = statuses[0]
  if (!status) return null
  // 姓名：状态左侧最近（x 最大）的合格中文节点
  let best: { t: string; x: number } | null = null
  for (const n of texts) {
    if (!NAME_PATTERN.test(n.t)) continue
    if (Math.abs(n.y - status.y) > NAME_SAME_LINE_BAND) continue
    if (!(n.x < status.x) || status.x - n.x >= NAME_MAX_DX) continue
    if (!best || n.x > best.x) best = { t: n.t, x: n.x }
  }
  return best?.t ?? null
}
