import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'

/** 虚构账号的导航头像，给脱敏面板fixture补可核验身份；无真实姓名/头像URL。 */
export function withAccount(snapshot: DomSnapshot, account = 'account-a', membership: 'vip' | 'non_vip' | 'unknown' = 'unknown'): DomSnapshot {
  const snap = structuredClone(snapshot), doc = snap.documents[0]!
  const values = doc.nodes.nodeValue as number[], names = (doc.nodes.nodeName ?? []) as number[], parents = (doc.nodes.parentIndex ?? values.map(() => 0)) as number[]
  const attrs = (doc.nodes.attributes ?? values.map(() => [])) as number[][]
  doc.nodes.nodeName = names; doc.nodes.parentIndex = parents; doc.nodes.attributes = attrs
  const addString = (text: string) => { snap.strings.push(text); return snap.strings.length - 1 }
  const addNode = (tag: string, parent: number, bounds: number[], text = '', pairs: string[] = []) => {
    const ni = values.length
    values.push(text ? addString(text) : -1); names[ni] = addString(tag); parents[ni] = parent; attrs[ni] = pairs.map(addString)
    doc.layout.nodeIndex.push(ni); doc.layout.bounds.push(bounds)
    if (doc.layout.styles) doc.layout.styles.push([])
    if (doc.layout.clientRects) doc.layout.clientRects.push([])
    return ni
  }
  const width = doc.layout.bounds[0]![2]!
  const avatar = addNode('DIV', 0, [width - 100, 12, 60, 60], '', ['class', 'avatar-content'])
  addNode('IMG', avatar, [width - 100, 12, 60, 60], '', ['src', `https://fixture.invalid/avatar/${account}.png`])
  const user = addNode('SPAN', 0, [width - 220, 12, 100, 40], '', ['class', 'user-name'])
  addNode('#text', user, [width - 220, 20, 90, 27], account)
  if (membership !== 'unknown') addNode('#text', avatar, [width - 94, 16, 48, 24], membership === 'vip' ? 'VIP会员' : '非VIP会员')
  return snap
}
