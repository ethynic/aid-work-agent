import { accumulateOwnerOffset, classOf, indexedValues, isDescendantOf, parentMapOf, type DomSnapshot } from './domSnapshot.js'
import { createHash } from 'node:crypto'
import { snapshotNodeVisible } from './SnapshotVisibility.js'

export type MembershipStatus = 'vip' | 'non_vip' | 'unknown'
export type MembershipHint = 'vip' | 'non_vip'
export interface MembershipObservation {
  status: MembershipStatus
  source: 'avatar' | 'paywall' | 'unknown' | 'user_hint'
  evidence: string[]
  observed_status?: 'unknown'
}

function attrsOf(snapshot: DomSnapshot, node: number, documentIndex = 0): Map<string, string> {
  const field = snapshot.documents[documentIndex]?.nodes.attributes
  const values = Array.isArray(field) ? field[node] : field?.value[field.index.indexOf(node)]
  const attrs = new Map<string, string>()
  for (let i = 0; values && i < values.length; i += 2) attrs.set(snapshot.strings[values[i]!] ?? '', snapshot.strings[values[i + 1]!] ?? '')
  return attrs
}

const AVATAR_VIP_ASSET = 'https://img.bosszhipin.com/static/zhipin/recommend-geek/955336197567184833.png'
const classTokens = (snapshot: DomSnapshot, di: number, node: number) => (classOf(snapshot, di, node) ?? '').split(/\s+/)
type Rect = [number, number, number, number]
const validRect = (rect: number[] | undefined): rect is Rect => !!rect && rect.length === 4 && rect.every(Number.isFinite) && rect[2]! > 0 && rect[3]! > 0
const contains = (outer: Rect, inner: Rect) => inner[0] >= outer[0] && inner[1] >= outer[1]
  && inner[0] + inner[2] <= outer[0] + outer[2] && inner[1] + inner[3] <= outer[1] + outer[3]

function screenRect(snapshot: DomSnapshot, di: number, node: number): Rect | null {
  const doc = snapshot.documents[di]!, b = doc.layout.bounds[doc.layout.nodeIndex.indexOf(node)]
  if (!validRect(b) || !snapshotNodeVisible(snapshot, di, node)) return null
  try {
    const offset = accumulateOwnerOffset(snapshot, di)
    return [offset.x + b[0] - (doc.scrollOffsetX ?? 0), offset.y + b[1] - (doc.scrollOffsetY ?? 0), b[2], b[3]]
  } catch { return null }
}

function insideElementClips(snapshot: DomSnapshot, di: number, node: number, rect: Rect): boolean {
  const doc = snapshot.documents[di]!, parents = parentMapOf(doc)
  if (!parents) return false
  const clipped = (value: string | undefined) => ['hidden', 'clip', 'auto', 'scroll'].includes(value ?? '')
  const seen = new Set<number>()
  let current: number | undefined = node
  while (current !== undefined && current >= 0) {
    if (seen.has(current)) return current === 0
    seen.add(current)
    const index = doc.layout.nodeIndex.indexOf(current), styles = doc.layout.styles?.[index] ?? []
    const clipX = clipped(snapshot.strings[styles[0] ?? -1]), clipY = clipped(snapshot.strings[styles[1] ?? -1])
    if (clipX || clipY) {
      const bounds = screenRect(snapshot, di, current)
      if (!bounds || (clipX && (rect[0] < bounds[0] || rect[0] + rect[2] > bounds[0] + bounds[2]))
        || (clipY && (rect[1] < bounds[1] || rect[1] + rect[3] > bounds[1] + bounds[3]))) return false
    }
    current = parents.get(current)
  }
  return true
}

/** Text can be visible outside its own parent, but never outside an iframe viewport. */
function insideVisibleFrames(snapshot: DomSnapshot, di: number, rect: Rect): boolean {
  const seen = new Set<number>()
  while (di !== 0) {
    if (seen.has(di)) return false
    seen.add(di)
    const owners: Array<{ di: number; node: number }> = []
    snapshot.documents.forEach((doc, ownerDi) => {
      for (const [node, child] of indexedValues(doc.nodes.contentDocumentIndex, 'contentDocumentIndex')) {
        if (child === di) owners.push({ di: ownerDi, node })
      }
    })
    if (owners.length !== 1) return false
    const owner = owners[0]!, clip = screenRect(snapshot, owner.di, owner.node)
    if (!clip || !contains(clip, rect) || !insideElementClips(snapshot, owner.di, owner.node, rect)) return false
    di = owner.di
  }
  return true
}

/** User-confirmed orange badge is rendered by the recommend iframe below the root avatar. */
function avatarBadgeCount(snapshot: DomSnapshot, avatar: Rect, viewport: Rect): number {
  let count = 0
  for (const [di, doc] of snapshot.documents.entries()) {
    if (di === 0 || !doc.nodes.parentIndex || !doc.nodes.nodeName) continue
    const tags = new Map(indexedValues(doc.nodes.nodeName, 'nodeName')), parents = parentMapOf(doc)!
    for (const node of doc.layout.nodeIndex) {
      if (snapshot.strings[tags.get(node) ?? -1] !== 'IMG' || !classTokens(snapshot, di, node).includes('vip-icon-v2')) continue
      const attrs = attrsOf(snapshot, node, di)
      if (attrs.get('src') !== AVATAR_VIP_ASSET) continue
      const label = parents.get(node), wrap = label === undefined ? undefined : parents.get(label)
      const filter = wrap === undefined ? undefined : parents.get(wrap), head = filter === undefined ? undefined : parents.get(filter)
      if (label === undefined || wrap === undefined || filter === undefined || head === undefined
        || !classTokens(snapshot, di, label).includes('filter-label-wrap') || !classTokens(snapshot, di, wrap).includes('filter-wrap')
        || !classTokens(snapshot, di, filter).includes('recommend-filter') || !classTokens(snapshot, di, head).includes('head-wrap')) continue
      const badge = screenRect(snapshot, di, node)
      if (!badge || !contains(viewport, badge) || !insideElementClips(snapshot, di, node, badge) || !insideVisibleFrames(snapshot, di, badge)) continue
      const gap = badge[1] - (avatar[1] + avatar[3])
      if (Math.abs(badge[0] + badge[2] / 2 - avatar[0] - avatar[2] / 2) > avatar[2] * 0.4
        || gap < 0 || gap > avatar[3] * 0.65 || badge[2] < avatar[2] * 0.6 || badge[2] > avatar[2] * 1.6
        || badge[3] < avatar[3] * 0.2 || badge[3] > avatar[3] * 0.8) continue
      count++
    }
  }
  return count
}

/** Explicit root avatar state plus the user-confirmed badge immediately below it. */
export function inspectMembership(snapshot: DomSnapshot): MembershipObservation {
  const unknown = (reason: string): MembershipObservation => ({ status: 'unknown', source: 'unknown', evidence: [reason] })
  const root = snapshot.documents[0]
  const viewport = root?.layout.bounds[0]
  if (!root || !viewport || !root.nodes.parentIndex) return unknown('未取得主页面头像结构')
  const visible = root.layout.nodeIndex.filter((node, i) => {
    const b = root.layout.bounds[i]
    return snapshotNodeVisible(snapshot, 0, node) && b && b.every(Number.isFinite) && b[2]! > 0 && b[3]! > 0
      && b[0]! >= viewport[2]! * 0.6 && b[1]! >= 0 && b[1]! < Math.min(250, viewport[3]! * 0.15)
      && b[0]! + b[2]! <= viewport[2]! && b[1]! + b[3]! <= viewport[3]!
  })
  const avatars = visible.filter((node) => (classOf(snapshot, 0, node) ?? '').split(/\s+/).includes('avatar-content'))
  if (avatars.length !== 1) return unknown('主页面导航头像不唯一或不可见')
  const descendants = visible.filter((node) => isDescendantOf(root, node, avatars[0]!) === true)
  const text = new Map(indexedValues(root.nodes.nodeValue, 'nodeValue'))
  let vip = false, nonVip = false
  for (const node of descendants) {
    const parents = parentMapOf(root)!
    let current: number | undefined = node, promotional = false
    const seen = new Set<number>()
    while (current !== undefined && !seen.has(current)) {
      seen.add(current)
      const cls = (classOf(snapshot, 0, current) ?? '').split(/\s+/)
      if (cls.some((token) => ['vip-icon-v2', 'filter-label-wrap', 'recommend-filter'].includes(token))) promotional = true
      if (current === avatars[0]) break
      current = parents.get(current)
    }
    if (promotional) continue
    const attrs = attrsOf(snapshot, node)
    const values = [snapshot.strings[text.get(node) ?? -1] ?? '', attrs.get('alt') ?? '', attrs.get('title') ?? ''].map((s) => s.trim())
    if (values.some((s) => ['VIP', 'VIP会员', '已开通VIP'].includes(s))) vip = true
    if (values.some((s) => ['非VIP会员', '未开通VIP'].includes(s))) nonVip = true
  }
  if (vip && nonVip) return unknown('头像会员状态证据冲突')
  if (nonVip) return { status: 'non_vip', source: 'avatar', evidence: ['导航头像内部明确显示未开通VIP'] }
  const avatar = screenRect(snapshot, 0, avatars[0]!)
  const badgeCount = avatar && validRect(viewport) && contains(viewport, avatar)
    && insideElementClips(snapshot, 0, avatars[0]!, avatar) ? avatarBadgeCount(snapshot, avatar, viewport) : 0
  if (badgeCount > 1) return unknown('头像下方VIP徽标不唯一，无法确认会员状态')
  if (badgeCount === 1) return { status: 'vip', source: 'avatar', evidence: ['导航头像正下方可见已确认VIP徽标（跨iframe）'] }
  if (vip) return { status: 'vip', source: 'avatar', evidence: ['导航头像内部明确显示VIP状态'] }
  return unknown('已定位导航头像，但未观察到可靠会员状态；筛选推广徽标不作为依据')
}

export function effectiveMembership(observed: MembershipObservation, hint?: MembershipHint): MembershipStatus {
  return observed.status === 'unknown' ? hint ?? 'unknown' : observed.status
}

/** 单次调用内的账号摘要，原始头像URL/名称不回显、不持久化；仅用于阻止用户提示跨账号沿用。 */
export function membershipAccountKey(snapshot: DomSnapshot): string | null {
  const root = snapshot.documents[0], viewport = root?.layout.bounds[0]
  if (!root || !viewport || !root.nodes.parentIndex) return null
  const visible = root.layout.nodeIndex.filter((ni, i) => {
    const b = root.layout.bounds[i]
    return snapshotNodeVisible(snapshot, 0, ni) && b && b[2]! > 0 && b[3]! > 0 && b[0]! >= viewport[2]! * 0.6 && b[1]! >= 0
      && b[1]! < Math.min(250, viewport[3]! * 0.15) && b[0]! + b[2]! <= viewport[2]!
  })
  const avatars = visible.filter((ni) => (classOf(snapshot, 0, ni) ?? '').split(/\s+/).includes('avatar-content'))
  if (avatars.length !== 1) return null
  const values = new Map(indexedValues(root.nodes.nodeValue, 'nodeValue'))
  const images = visible.filter((ni) => isDescendantOf(root, ni, avatars[0]!) === true).map((ni) => attrsOf(snapshot, ni).get('src')).filter((src): src is string => !!src)
  const nameAnchors = visible.filter((ni) => (classOf(snapshot, 0, ni) ?? '').split(/\s+/).includes('user-name'))
  const names = visible.filter((ni) => nameAnchors.some((anchor) => isDescendantOf(root, ni, anchor) === true))
    .map((ni) => (snapshot.strings[values.get(ni) ?? -1] ?? '').trim()).filter(Boolean)
  if (images.length === 0 && names.length === 0) return null
  return createHash('sha256').update(JSON.stringify({ images, names })).digest('hex')
}

/** 普通行白名单来自真实页面；其余高级/未知行保守视为VIP，不能绕过权益检查。 */
export function filterGroup(label: string): 'ordinary' | 'vip' {
  return ['经验要求', '学历要求', '求职状态', '薪资待遇'].includes(label.replace(/\[单选\]$/, '').trim()) ? 'ordinary' : 'vip'
}
