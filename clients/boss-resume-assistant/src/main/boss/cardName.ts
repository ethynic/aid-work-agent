/** Candidate actions and names must originate in the same structural list card. */
import { accumulateOwnerOffset, classOf, indexedValues, parentMapOf, type DomSnapshot, type ClickPoint } from './domSnapshot.js'
import { candidateButtonVisible, frameViewport, loadedCandidates } from './LoadedCandidateNavigation.js'
import { excludedRecommendationNode } from './RecommendationResultBoundary.js'
import { snapshotNodeVisible } from './SnapshotVisibility.js'

export const GREET_TEXT = '打招呼'
export const CONTINUE_TEXT = '继续沟通'
export interface GreetButtonRef {
  point: ClickPoint
  documentIndex: number
}

export function pairCardName(snap: DomSnapshot, btn: GreetButtonRef,
  viewport: { width: number; height: number }): string | null {
  return pairCardNameWithPoint(snap, btn, viewport).name
}

export function pairCardNameWithPoint(snap: DomSnapshot, btn: GreetButtonRef,
  viewport: { width: number; height: number }): { name: string | null; point: ClickPoint | null } {
  const matches = loadedCandidates(snap).filter(c => !c.excluded && c.documentIndex === btn.documentIndex
    && c.point?.x === btn.point.x && c.point.y === btn.point.y)
  if (matches.length !== 1) return { name: null, point: null }
  const c = matches[0]!, p = c.namePoint
  if (!p || !candidateButtonVisible(c) || p.x < c.left || p.x > Math.min(c.right,viewport.width)
    || p.y < c.top + 8 || p.y > Math.min(c.bottom - 8,viewport.height)) return { name: null, point: null }
  return { name: c.name, point: p }
}

export function findButtonsByExactText(snap: DomSnapshot, text: string): GreetButtonRef[] {
  const loaded = loadedCandidates(snap)
  const points = loaded.filter(c => !c.excluded && c.buttonText === text && c.point && candidateButtonVisible(c))
    .map(c => ({ point: c.point!, documentIndex: c.documentIndex }))
  // Keep malformed/nameless real cards observable as local failures, with no guessed name.
  snap.documents.forEach((d,di) => {
    const parents = parentMapOf(d)
    if (!parents || !d.nodes.attributes || !d.nodes.nodeName) return
    const tags = new Map(indexedValues(d.nodes.nodeName,'nodeName'))
    const has = (n: number, cls: string) => (classOf(snap,di,n) ?? '').split(/\s+/).includes(cls)
    let offset: ClickPoint
    try { offset = accumulateOwnerOffset(snap,di) } catch { return }
    const area = frameViewport(snap,di)
    for (const [node,value] of indexedValues(d.nodes.nodeValue,'nodeValue')) {
      if (snap.strings[value]?.trim() !== text) continue
      const button = parents.get(node)
      if (button === undefined || snap.strings[tags.get(button) ?? -1] !== 'BUTTON'
        || !has(button,text === GREET_TEXT ? 'btn-greet' : 'btn-continue')
        || !snapshotNodeVisible(snap,di,node) || excludedRecommendationNode(snap,di,node)) continue
      let card: number | undefined = button
      const seen = new Set<number>()
      while (card !== undefined && !seen.has(card) && !has(card,'card-item')) { seen.add(card); card = parents.get(card) }
      if (card === undefined || !has(card,'card-item')) continue
      const list = parents.get(card), body = list === undefined ? undefined : parents.get(list), wrap = body === undefined ? undefined : parents.get(body)
      if (list === undefined || body === undefined || wrap === undefined || !has(list,'card-list') || !has(body,'list-body')
        || !has(wrap,'list-wrap') || !has(wrap,'card-list-wrap')) continue
      const b = d.layout.bounds[d.layout.nodeIndex.indexOf(button)]
      if (!b || b.length !== 4 || !b.every(Number.isFinite) || b[2]! <= 0 || b[3]! <= 0) continue
      const x = offset.x + b[0]! - (d.scrollOffsetX ?? 0), y = offset.y + b[1]! - (d.scrollOffsetY ?? 0)
      const point = { x: x + b[2]! / 2, y: y + b[3]! / 2 }
      if (loaded.some(c => c.documentIndex === di && c.point?.x === point.x && c.point.y === point.y)) continue
      if (x < area.left || x+b[2]! > area.right || y < area.top+8 || y+b[3]! > area.bottom-8) continue
      points.push({ point, documentIndex: di })
    }
  })
  return points.sort((a,b) => a.point.y - b.point.y)
}
