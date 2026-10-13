import { classOf, indexedValues, isDescendantOf, parentMapOf, type DomSnapshot } from './domSnapshot.js'
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

function attrsOf(snapshot: DomSnapshot, node: number): Map<string, string> {
  const field = snapshot.documents[0]?.nodes.attributes
  const values = Array.isArray(field) ? field[node] : field?.value[field.index.indexOf(node)]
  const attrs = new Map<string, string>()
  for (let i = 0; values && i < values.length; i += 2) attrs.set(snapshot.strings[values[i]!] ?? '', snapshot.strings[values[i + 1]!] ?? '')
  return attrs
}

/** 仅主页面导航头像内部的明确状态；推荐iframe的vip-icon-v2是推广，不是会员证明。 */
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
