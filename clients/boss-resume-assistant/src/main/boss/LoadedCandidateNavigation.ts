import { accumulateOwnerOffset, classOf, indexedValues, parentMapOf, type DomSnapshot, type ClickPoint } from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { excludedRecommendationNode } from './RecommendationResultBoundary.js'
import { snapshotNodeVisible } from './SnapshotVisibility.js'
import { createHash } from 'node:crypto'
import { membershipAccountKey } from './MembershipInspector.js'

export interface CandidateCardInfo {
  base_info: string | null
  salary: string | null
  expectation: string | null
  work_experience: string[]
  education: string[]
  tags: string[]
  advantage: string | null
}

export interface LoadedCandidate {
  name: string
  advantage: string | null
  cardInfo: CandidateCardInfo
  /** Opaque identity fingerprint; no position, active status or action label participates. */
  identityDigest: string | null
  excluded: boolean
  documentIndex: number
  buttonText: string | null
  point: ClickPoint | null
  /** Effective frame viewport and sticky list header, in device pixels. */
  top: number
  bottom: number
  left: number
  right: number
  buttonBounds: number[] | null
}
const has = (s: DomSnapshot, di: number, ni: number, token: string): boolean => (classOf(s, di, ni) ?? '').split(/\s+/).includes(token)
const rect = (b: number[] | undefined): b is number[] => !!b && b.length === 4 && b.every(Number.isFinite) && b[2]! > 0 && b[3]! > 0
export const normalizeAdvantage = (text: string): string => text.replace(/\s+/gu, ' ').trim()
export const CANDIDATE_REF_TTL_MS = 30 * 60 * 1000

export function candidateReference(c: LoadedCandidate, issuedAt = Date.now()): string | null {
  const digest = c.identityDigest ? createHash('sha256').update(JSON.stringify([issuedAt,c.identityDigest])).digest('hex') : null
  return digest ? `boss-card-v1:${issuedAt}:${digest}` : null
}

export function resolveCandidateReference(s: DomSnapshot, name: string, ref: string, now = Date.now()): LoadedCandidate | null {
  const match = ref.length === 91 ? /^boss-card-v1:(\d{13}):([a-f0-9]{64})$/.exec(ref) : null
  if (!match || Number(match[1]) > now || now - Number(match[1]) > CANDIDATE_REF_TTL_MS) return null
  const cards = loadedCandidates(s).filter(c => c.name === name && !c.excluded && candidateReference(c,Number(match[1])) === ref)
  return cards.length === 1 ? cards[0]! : null
}


/** A child document's size is not its visible owner size. Intersect every owner and overflow clip. */
function frameViewport(s: DomSnapshot, documentIndex: number): { left: number; right: number; top: number; bottom: number } {
  const vp = viewportOf(s), area = { left: 0, right: vp.width, top: 0, bottom: vp.height }, seen = new Set<number>()
  let current = documentIndex
  const invalid = () => ({ left: 0, right: 0, top: 0, bottom: 0 })
  while (current !== 0) {
    if (seen.has(current)) return invalid()
    seen.add(current)
    const owners: Array<{di: number; node: number}> = []
    s.documents.forEach((d, di) => {
      for (const [node, child] of indexedValues(d.nodes.contentDocumentIndex, 'contentDocumentIndex')) if (child === current) owners.push({di,node})
    })
    if (owners.length !== 1) return invalid()
    const owner = owners[0]!, d = s.documents[owner.di]!, parents = parentMapOf(d)
    const offset = accumulateOwnerOffset(s, owner.di), visited = new Set<number>()
    let node: number | undefined = owner.node
    while (node !== undefined && node >= 0 && !visited.has(node)) {
      visited.add(node)
      const i = d.layout.nodeIndex.indexOf(node), b = d.layout.bounds[i], styles = d.layout.styles?.[i]
      const clipX = node === owner.node || ['hidden','clip','auto','scroll'].includes(s.strings[styles?.[0] ?? -1] ?? '')
      const clipY = node === owner.node || ['hidden','clip','auto','scroll'].includes(s.strings[styles?.[1] ?? -1] ?? '')
      if (clipX || clipY) {
        if (!rect(b) || !snapshotNodeVisible(s,owner.di,node)) return invalid()
        const x = offset.x + b[0]! - (d.scrollOffsetX ?? 0), y = offset.y + b[1]! - (d.scrollOffsetY ?? 0)
        if (clipX) { area.left = Math.max(area.left,x); area.right = Math.min(area.right,x+b[2]!) }
        if (clipY) { area.top = Math.max(area.top,y); area.bottom = Math.min(area.bottom,y+b[3]!) }
      }
      node = parents?.get(node)
    }
    current = owner.di
  }
  return area
}

/** Fresh full-document index; coordinates are never retained between snapshots or invocations. */
export function loadedCandidates(s: DomSnapshot): LoadedCandidate[] {
  const out: LoadedCandidate[] = []
  const account = membershipAccountKey(s)
  s.documents.forEach((d, di) => {
    const parents = parentMapOf(d)
    if (!parents || !d.nodes.attributes) return
    const values = indexedValues(d.nodes.nodeValue, 'nodeValue')
    const children = new Map<number, number[]>()
    for (const [node, parent] of parents) { const siblings = children.get(parent) ?? []; siblings.push(node); children.set(parent, siblings) }
    const tags = new Map(indexedValues(d.nodes.nodeName, 'nodeName'))
    const layout = new Map(d.layout.nodeIndex.map((n, i) => [n, i]))
    const nearestCard = (n: number): number | null => {
      const seen = new Set<number>()
      let current: number | undefined = n
      while (current !== undefined && current >= 0 && !seen.has(current)) {
        seen.add(current)
        if (has(s, di, current, 'card-item')) return current
        current = parents.get(current)
      }
      return null
    }
    const descendants = (anchor: number): number[] => {
      const found: number[] = [], pending = [anchor], seen = new Set<number>()
      while (pending.length) {
        const node = pending.pop()!
        if (seen.has(node)) continue
        seen.add(node)
        if (!snapshotNodeVisible(s,di,node)) continue
        found.push(node); pending.push(...(children.get(node) ?? []).slice().reverse())
      }
      return found
    }
    const nodeValues = new Map(values)
    const textOf = (anchor: number): string => descendants(anchor)
      .map(n => s.strings[nodeValues.get(n) ?? -1] ?? '').map(normalizeAdvantage).filter(Boolean).join(' | ')
    const heads = d.layout.nodeIndex.filter(n => has(s,di,n,'head-wrap') && snapshotNodeVisible(s,di,n))
    const jobs = heads.flatMap(head => descendants(head)).filter(n => {
      const label = parents.get(n), selector = label === undefined ? undefined : parents.get(label)
      return label !== undefined && selector !== undefined && has(s,di,label,'ui-dropmenu-label') && has(s,di,selector,'job-selecter-wrap')
    }).map(n => (s.strings[nodeValues.get(n) ?? -1] ?? '').trim())
      .filter(text => /^.+? _ \S+ .+$/.test(text))
    const scope = account && jobs.length === 1 ? JSON.stringify([account,jobs[0]]) : null
    const actions = new Map<number, Array<{ text: string; node: number }>>()
    const advantageSections = new Map<number, string[]>()
    const valueMap = new Map(values)
    for (const [node, v] of values) {
      if (s.strings[v]?.trim() !== '优势' || !snapshotNodeVisible(s,di,node)) continue
      const label = parents.get(node), row = label === undefined ? undefined : parents.get(label)
      if (label === undefined || row === undefined || !has(s,di,label,'label') || !has(s,di,row,'geek-desc')) continue
      const card = nearestCard(row), content = (children.get(row) ?? []).filter((n) => has(s,di,n,'content') && snapshotNodeVisible(s,di,n))
      if (card === null) continue
      const sections = advantageSections.get(card) ?? []
      if (content.length !== 1) { sections.push(''); advantageSections.set(card,sections); continue }
      const pending = [content[0]!], seen = new Set<number>(), texts: string[] = []
      while (pending.length) {
        const child = pending.pop()!
        if (seen.has(child)) continue
        seen.add(child)
        if (!snapshotNodeVisible(s,di,child)) continue
        const value = valueMap.get(child), text = value === undefined ? undefined : s.strings[value]
        if (text) texts.push(text)
        pending.push(...(children.get(child) ?? []).slice().reverse())
      }
      sections.push(normalizeAdvantage(texts.join(''))); advantageSections.set(card,sections)
    }
    for (const [node, v] of values) {
      const text = s.strings[v]?.trim()
      if (text !== '打招呼' && text !== '继续沟通') continue
      // Confirm the native action element; company/experience text is never an action anchor.
      const button = parents.get(node)
      if (button === undefined || s.strings[tags.get(button) ?? -1] !== 'BUTTON') continue
      // The real list replaces btn-greet with btn-continue after sending.
      // Keep the action class tied to its label; unrelated text is never success evidence.
      const actionClass = text === '打招呼'
        ? has(s, di, button, 'btn-greet')
        : has(s, di, button, 'btn-continue') || has(s, di, button, 'btn-greet')
      if (!actionClass) continue
      if (!snapshotNodeVisible(s, di, node)) continue
      const card = nearestCard(node)
      if (card === null) continue
      const list = actions.get(card) ?? []; list.push({ text, node: button }); actions.set(card, list)
    }
    let offset: ClickPoint
    try { offset = accumulateOwnerOffset(s, di) } catch { return }
    const area = frameViewport(s, di), { left, right, bottom } = area
    let top = area.top
    for (const [node, i] of layout) {
      if (!has(s, di, node, 'head-wrap') || !snapshotNodeVisible(s, di, node)) continue
      const b = d.layout.bounds[i], position = s.strings[d.layout.styles?.[i]?.[3] ?? -1]
      if (rect(b) && (position === 'fixed' || position === 'sticky')) top = Math.max(top, offset.y + b[1]! + b[3]! - (d.scrollOffsetY ?? 0))
    }
    for (const [node, v] of values) {
      const name = s.strings[v]?.trim(), parent = parents.get(node), wrap = parent === undefined ? undefined : parents.get(parent)
      if (!name || parent === undefined || wrap === undefined || !has(s, di, parent, 'name')
        || !has(s, di, wrap, 'name-wrap') || !snapshotNodeVisible(s, di, node)) continue
      const card = nearestCard(wrap), list = card === null ? undefined : parents.get(card)
      const body = list === undefined ? undefined : parents.get(list), container = body === undefined ? undefined : parents.get(body)
      if (card === null || list === undefined || body === undefined || container === undefined
        || !has(s, di, list, 'card-list') || !has(s, di, body, 'list-body')
        || !has(s, di, container, 'list-wrap') || !has(s, di, container, 'card-list-wrap')) continue
      const action = actions.get(card), selected = action?.length === 1 ? action[0] : undefined
      const b = selected ? d.layout.bounds[layout.get(selected.node) ?? -1] : undefined
      const screen = rect(b) ? [offset.x + b[0]! - (d.scrollOffsetX ?? 0), offset.y + b[1]! - (d.scrollOffsetY ?? 0), b[2]!, b[3]!] : null
      const advantages = advantageSections.get(card)
      const advantage = advantages?.length === 1 && advantages[0] ? advantages[0] : null
      const nodes = descendants(card)
      const section = (token: string): string | null => {
        const anchors = nodes.filter(n => has(s,di,n,token))
        return anchors.length === 1 ? textOf(anchors[0]!) || null : null
      }
      const history = (token: string): string[] => nodes.filter(n => has(s,di,n,token))
        .flatMap(anchor => descendants(anchor).filter(n => has(s,di,n,'timeline-item')).map(textOf)).filter(Boolean)
      const cardInfo: CandidateCardInfo = { base_info: section('base-info'), salary: section('salary-wrap'),
        expectation: section('expect-wrap'), work_experience: history('work-exps'), education: history('edu-exps'),
        tags: nodes.filter(n => has(s,di,n,'tag-item')).map(textOf).filter(Boolean), advantage }
      // Use avatar only inside the card, and hash it locally; never return raw URLs or identifiers.
      const avatarSources = nodes.filter(n => s.strings[tags.get(n) ?? -1] === 'IMG' && has(s,di,n,'avatar'))
        .map(n => {
          const attrs = indexedValues(d.nodes.attributes,'attributes').find(([ni])=>ni===n)?.[1] ?? []
          for (let i=0;i<attrs.length;i+=2) if (s.strings[attrs[i]!] === 'src') return s.strings[attrs[i+1]!] ?? ''
          return ''
        }).filter(Boolean)
      const hasDescription = Object.values(cardInfo).some(v => Array.isArray(v) ? v.length > 0 : !!v)
      const identityDigest = scope && hasDescription ? createHash('sha256')
        .update(JSON.stringify({ scope,name,cardInfo,avatarSources })).digest('hex') : null
      out.push({ name, advantage, cardInfo, identityDigest,
        excluded: excludedRecommendationNode(s, di, node), documentIndex: di,
        buttonText: selected?.text ?? null, point: screen ? { x: screen[0]! + screen[2]! / 2, y: screen[1]! + screen[3]! / 2 } : null,
        buttonBounds: screen, top, bottom, left, right })
    }
  })
  return out
}

export function candidateOption(c: LoadedCandidate, optionIndex: number): {
  option_index: number; candidate_ref: string | null; card_info: CandidateCardInfo; advantage: string | null
} {
  return { option_index: optionIndex, candidate_ref: candidateReference(c), card_info: c.cardInfo, advantage: c.advantage }
}

/** Even an empty loaded list is authoritative for a historical name-only request. */
export function hasLoadedRecommendationList(s: DomSnapshot): boolean {
  return s.documents.some((d,di) => {
    const parents = parentMapOf(d)
    if (!parents || !d.nodes.attributes) return false
    return d.layout.nodeIndex.some((node) => {
      const body = parents.get(node), wrap = body === undefined ? undefined : parents.get(body)
      return body !== undefined && wrap !== undefined && has(s,di,node,'card-list') && has(s,di,body,'list-body')
        && has(s,di,wrap,'list-wrap') && has(s,di,wrap,'card-list-wrap') && snapshotNodeVisible(s,di,node)
    })
  })
}


export function candidateButtonVisible(c: LoadedCandidate): boolean {
  const b = c.buttonBounds
  return !!b && b[0]! >= c.left && b[0]! + b[2]! <= c.right && b[1]! >= c.top + 8 && b[1]! + b[3]! <= c.bottom - 8
}

/** Bridge only mouse-wheel input from fresh snapshot device geometry to CDP CSS pixels. */
export function candidateScrollEvent(s: DomSnapshot, deltaY: number): { x: number; y: number; deltaY: number } {
  const d = s.documents[0], invalid = (): never => { throw new Error('列表缺少有效根 HTML CSS 几何，未发送滚动') }
  if (!d?.nodes.nodeName || !d.layout.clientRects || !Number.isFinite(deltaY)) return invalid()
  const html = indexedValues(d.nodes.nodeName, 'nodeName').filter(([, v]) => s.strings[v] === 'HTML')
  if (html.length !== 1) return invalid()
  const i = d.layout.nodeIndex.indexOf(html[0]![0]), b = d.layout.bounds[i], css = d.layout.clientRects[i], vp = viewportOf(s)
  if (!rect(b) || !rect(css) || Math.abs(b[0]!) > .01 || Math.abs(b[1]!) > .01
    || Math.abs(css[0]!) > .01 || Math.abs(css[1]!) > .01 || Math.abs(b[2]! - vp.width) > .01
    || Math.abs(b[3]! - vp.height) > .01 || (d.scrollOffsetX ?? 0) !== 0 || (d.scrollOffsetY ?? 0) !== 0) return invalid()
  const sx = b[2]! / css[2]!, sy = b[3]! / css[3]!
  if (!Number.isFinite(sx) || !Number.isFinite(sy) || sx <= 0 || sy <= 0 || Math.abs(sx - sy) > Math.max(sx, sy) * .001) return invalid()
  return { x: vp.width * .55 / sx, y: vp.height * .65 / sy, deltaY: deltaY / sy }
}
