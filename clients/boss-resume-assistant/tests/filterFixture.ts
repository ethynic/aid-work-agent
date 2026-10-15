import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { isDescendantOf } from '../src/main/boss/domSnapshot.js'

/** 保留真机行标签与面板边界，仅移除该行选项的可见布局，模拟局部控件尚未渲染。 */
export function withoutRowOptions(snapshot: DomSnapshot, labels: string[]): DomSnapshot {
  const snap = structuredClone(snapshot)
  for (const doc of snap.documents) {
    const values = doc.nodes.nodeValue as number[], parents = doc.nodes.parentIndex as number[]
    for (let node = 0; node < values.length; node++) {
      if (!labels.includes(snap.strings[values[node]!] ?? '')) continue
      const label = doc.layout.bounds[doc.layout.nodeIndex.indexOf(node)]!
      const row = parents[parents[node]!]!
      doc.layout.nodeIndex.forEach((child, index) => {
        const bounds = doc.layout.bounds[index]!
        if (child !== node && isDescendantOf(doc, child, row) && bounds[0]! >= label[0]! + label[2]!) {
          doc.layout.bounds[index] = [0, 0, 0, 0]
        }
      })
    }
  }
  return snap
}

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
