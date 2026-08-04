/**
 * 按钮定位器（设计文档 §10.2）。
 * 在 DOMSnapshot 中按文本定位唯一可见按钮，返回视口坐标点击点。
 * - 文本匹配：snapshot.strings 精确匹配（trim 后相等）
 * - 跨 document 收集所有可见匹配，必须恰好 1 个；歧义 → UNLOCATABLE，绝不猜坐标
 * - 点击点 = bounds 中心 + 嵌套 iframe owner 偏移（复用 domSnapshot.ts 工具）
 *
 * 与 ListSnapshotParser 的区别：这里定位的是写动作按钮本身（允许"打招呼"等文案），
 * 不做"候选人正文安全区"校验，但仍要求坐标落在视口内。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'

export type ButtonLocateResult =
  | { status: 'LOCATED'; point: ClickPoint; matchedText: string }
  | { status: 'UNLOCATABLE'; reason: string }

export class ButtonLocator {
  /**
   * 定位 texts 中任一文本的唯一可见匹配。
   * @param texts 候选按钮文案（如 ['打招呼'] 或 ['不合适','不感兴趣']）
   * @param viewport 根文档视口尺寸
   */
  locateUnique(
    snapshot: DomSnapshot,
    texts: readonly string[],
    viewport: { width: number; height: number },
  ): ButtonLocateResult {
    const targets = new Set(texts.map((t) => t.trim()))
    // 收集所有命中的 stringIndex（同一个文案可能对应多个节点）
    const matchedIndexes: Array<{ stringIndex: number; text: string }> = []
    snapshot.strings.forEach((s, i) => {
      if (targets.has(s.trim())) matchedIndexes.push({ stringIndex: i, text: s.trim() })
    })
    if (matchedIndexes.length === 0) {
      return { status: 'UNLOCATABLE', reason: `button text absent from snapshot: ${texts.join('|')}` }
    }

    // 跨 document 收集可见匹配
    const matches: Array<{ documentIndex: number; bounds: [number, number, number, number]; text: string }> = []
    for (const { stringIndex, text } of matchedIndexes) {
      snapshot.documents.forEach((document, documentIndex) => {
        for (const { bounds } of findNodesByString(document, stringIndex)) {
          matches.push({ documentIndex, bounds, text })
        }
      })
    }
    if (matches.length !== 1) {
      return {
        status: 'UNLOCATABLE',
        reason: `button must have exactly one visible match; found ${matches.length}`,
      }
    }

    const match = matches[0]!
    let offset
    try {
      offset = accumulateOwnerOffset(snapshot, match.documentIndex)
    } catch (e) {
      return { status: 'UNLOCATABLE', reason: (e as Error).message }
    }

    const center = boundsCenter(match.bounds)
    const point: ClickPoint = { x: offset.x + center.x, y: offset.y + center.y }

    const [x, y, w, h] = match.bounds
    if (![x, y, w, h, point.x, point.y].every(Number.isFinite) || w <= 0 || h <= 0) {
      return { status: 'UNLOCATABLE', reason: 'button has no safe visible click area' }
    }
    if (point.x < 0 || point.y < 0 || point.x > viewport.width || point.y > viewport.height) {
      return {
        status: 'UNLOCATABLE',
        reason: `click point ${point.x},${point.y} outside viewport`,
      }
    }

    return { status: 'LOCATED', point, matchedText: match.text }
  }
}

/** 判断 snapshot 文本中是否仍包含 texts 中任一文案（结果确认用） */
export function snapshotContainsText(snapshot: DomSnapshot, texts: readonly string[]): boolean {
  const targets = new Set(texts.map((t) => t.trim()))
  return snapshot.strings.some((s) => targets.has(s.trim()))
}
