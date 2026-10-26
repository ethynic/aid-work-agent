import { accumulateOwnerOffset, classOf, indexedValues, isDescendantOf, parentMapOf, type DomSnapshot } from './domSnapshot.js'
import { snapshotNodeVisible } from './SnapshotVisibility.js'

export type RecommendationStopReason = 'no_matching_candidates' | 'recommendations_boundary'
interface Boundary {
  documentIndex: number
  list: number
  card: number
  cards: number[]
  empty: boolean
  reached: boolean
}
export interface RecommendationResultState {
  boundaries: Boundary[]
  stopReason?: RecommendationStopReason
}
const cache = new WeakMap<DomSnapshot, RecommendationResultState>()
const tokens = (snap: DomSnapshot, di: number, node: number) => (classOf(snap, di, node) ?? '').split(/\s+/)

/** 实测推荐列表组件边界；不按全页strings或视口文案猜测，空态滚离视口仍有效。 */
export function inspectRecommendationResults(snap: DomSnapshot): RecommendationResultState {
  const cached = cache.get(snap)
  if (cached) return cached
  const boundaries: Boundary[] = [], viewport = snap.documents[0]?.layout.bounds[0]
  if (!viewport || viewport[2]! <= 0 || viewport[3]! <= 0) return { boundaries }
  snap.documents.forEach((doc, di) => {
    const parents = parentMapOf(doc)
    if (!parents) return
    let offset: { x: number; y: number }
    try { offset = accumulateOwnerOffset(snap, di) } catch { return }
    const frame = doc.layout.bounds[0]
    if (!frame || frame[2]! <= 0 || frame[3]! <= 0 || offset.x >= viewport[2]! || offset.y >= viewport[3]!
      || offset.x + frame[2]! <= 0 || offset.y + frame[3]! <= 0) return
    const nodeValues = new Map(indexedValues(doc.nodes.nodeValue, 'nodeValue'))
    const childNodes = new Map<number, number[]>()
    for (const [node, parent] of parents) {
      const siblings = childNodes.get(parent) ?? []
      siblings.push(node); childNodes.set(parent, siblings)
    }
    const lists = doc.layout.nodeIndex.filter((node) => tokens(snap, di, node).includes('card-list') && snapshotNodeVisible(snap, di, node))
    for (const list of lists) {
      const body = parents.get(list), wrap = body === undefined ? undefined : parents.get(body)
      if (body === undefined || wrap === undefined || !tokens(snap, di, body).includes('list-body')
        || !tokens(snap, di, wrap).includes('card-list-wrap') || !tokens(snap, di, wrap).includes('list-wrap')) continue
      const listBounds = doc.layout.bounds[doc.layout.nodeIndex.indexOf(list)]
      if (!listBounds || listBounds[2]! <= 0 || listBounds[3]! <= 0) continue
      // DOMSnapshot node indices are document order; layout order alone is not a sibling-order contract.
      const cards = (childNodes.get(list) ?? []).filter((node) => tokens(snap, di, node).includes('card-item')).sort((a, b) => a - b)
      for (const card of cards) {
        const components = (childNodes.get(card) ?? []).filter((node) => tokens(snap, di, node).includes('mome-main-ui') && snapshotNodeVisible(snap, di, node))
        for (const component of components) {
          const children: number[] = [], visited = new Set<number>(), pending = [component]
          while (pending.length) {
            const node = pending.pop()!
            if (visited.has(node)) continue
            visited.add(node)
            if (snapshotNodeVisible(snap, di, node)) children.push(node)
            pending.push(...(childNodes.get(node) ?? []))
          }
          const visibleText = (anchor: number) => children.filter((node) => isDescendantOf(doc, node, anchor) === true)
            .map((node) => (snap.strings[nodeValues.get(node) ?? -1] ?? '').trim()).filter(Boolean)
          const tips = children.filter((node) => tokens(snap, di, node).includes('tip-inner'))
          const hasEmptyTip = tips.some((tip) => visibleText(tip).includes('暂无符合牛人，更换筛选范围试试'))
          const index = cards.indexOf(card)
          // Only explicit empty-result evidence proves the following cards are fallback.
          // A promotional/divider component inside a nonempty list says nothing about later cards.
          if (index !== 0 || !hasEmptyTip) continue
          const b = doc.layout.bounds[doc.layout.nodeIndex.indexOf(card)]
          if (!b || b[2]! <= 0 || b[3]! <= 0) continue
          boundaries.push({ documentIndex: di, list, card, cards, empty: index === 0 && hasEmptyTip,
            reached: offset.y + b[1]! - (doc.scrollOffsetY ?? 0) < viewport[3]! })
        }
      }
    }
  })
  const boundary = boundaries[0]
  const result = { boundaries, ...(boundary?.empty ? { stopReason: 'no_matching_candidates' as const }
    : boundary?.reached ? { stopReason: 'recommendations_boundary' as const } : {}) }
  cache.set(snap, result)
  return result
}

/** 只剔除已证明属于推荐分区之后card-item的节点；详情操作列和旧无结构fixture保持兼容。 */
export function excludedRecommendationNode(snap: DomSnapshot, di: number, node: number): boolean {
  const boundary = inspectRecommendationResults(snap).boundaries.find((b) => b.documentIndex === di)
  if (!boundary) return false
  const doc = snap.documents[di]!
  const parents = parentMapOf(doc)!, seen = new Set<number>()
  let current: number | undefined = node
  while (current !== undefined && !seen.has(current)) {
    seen.add(current)
    if (parents.get(current) === boundary.list && tokens(snap, di, current).includes('card-item')) return current >= boundary.card
    current = parents.get(current)
  }
  return false
}

/** 已打开详情只在可靠列表证据证明其姓名属于fallback时阻断；不要求其仍在当前视口名单。 */
export function excludedRecommendationName(snap: DomSnapshot, name: string): RecommendationStopReason | undefined {
  const state = inspectRecommendationResults(snap)
  for (const boundary of state.boundaries) {
    const di = boundary.documentIndex, doc = snap.documents[di]!, parents = parentMapOf(doc)!
    // 真机2026-10-13：姓名文本→.name→.row.name-wrap→卡片；公司/经历中的同文案不是姓名证据。
    const nameNodes = indexedValues(doc.nodes.nodeValue, 'nodeValue').filter(([node, value]) => {
      if ((snap.strings[value] ?? '').trim() !== name || !snapshotNodeVisible(snap, di, node)) return false
      const parent = parents.get(node), wrap = parent === undefined ? undefined : parents.get(parent)
      return parent !== undefined && wrap !== undefined && tokens(snap, di, parent).includes('name')
        && tokens(snap, di, wrap).includes('name-wrap') && boundary.cards.some((card) => isDescendantOf(doc, node, card) === true)
    }).map(([node]) => node)
    const excluded = nameNodes.filter((node) => excludedRecommendationNode(snap, di, node))
    if (excluded.length > 0 && nameNodes.length > excluded.length) throw new Error('筛选结果与补充推荐出现同名候选人，不能确认详情来源，已停止')
    if (excluded.length > 0) return boundary.empty ? 'no_matching_candidates' : 'recommendations_boundary'
  }
  return undefined
}
