import fs from 'node:fs'
import { classOf, parentMapOf, type DomSnapshot } from '../src/main/boss/domSnapshot.js'

/** 真实列表结构/几何投影；原候选人文本已移除，姓名为虚构。原3项style未复用。 */
export function emptyRecommendations(): DomSnapshot {
  return JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/boss-no-matching-recommendations.json', import.meta.url), 'utf8'))
}

/** 同一已取证组件前插入一张合成匹配卡片，用于边界前后回归；不是正常页真机证据。 */
export function matchingThenRecommendations(): DomSnapshot {
  const snap = emptyRecommendations(), doc = snap.documents[2]!, parents = parentMapOf(doc)!
  const list = doc.layout.nodeIndex.find((node) => (classOf(snap, 2, node) ?? '') === 'card-list')!
  const at = [...parents.keys()].find((node) => parents.get(node) === list && (classOf(snap, 2, node) ?? '') === 'card-item')!
  const str = (text: string) => { snap.strings.push(text); return snap.strings.length - 1 }
  const count = 5
  const values = doc.nodes.nodeValue as number[], names = doc.nodes.nodeName as number[], attrs = doc.nodes.attributes as number[][]
  const oldParents = doc.nodes.parentIndex as number[], content = doc.nodes.contentDocumentIndex as number[]
  const shift = (node: number) => node >= at ? node + count : node
  const mappedParents = oldParents.map((node) => shift(node))
  values.splice(at, 0, -1, -1, -1, str('钱测试'), str('打招呼'))
  names.splice(at, 0, ...Array(count).fill(str('DIV')))
  attrs.splice(at, 0, [str('class'), str('card-item')], [str('class'), str('row name-wrap')], [str('class'), str('name')], [], [])
  mappedParents.splice(at, 0, list, at, at + 1, at + 2, at)
  doc.nodes.parentIndex = mappedParents
  content.splice(at, 0, ...Array(count).fill(-1))
  doc.layout.nodeIndex = doc.layout.nodeIndex.map(shift)
  doc.layout.bounds = doc.layout.bounds.map((b, index) => doc.layout.nodeIndex[index]! >= at + count ? [b[0]!, b[1]! + 200, b[2]!, b[3]!] : b)
  doc.layout.nodeIndex.push(at, at + 1, at + 2, at + 3, at + 4)
  doc.layout.bounds.push([242, 132, 2368, 200], [500, 142, 200, 40], [500, 142, 100, 40], [500, 150, 100, 30], [2250, 150, 100, 30])
  return snap
}

export function setHidden(snapshot: DomSnapshot, di: number, node: number): DomSnapshot {
  const snap = structuredClone(snapshot), doc = snap.documents[di]!
  snap.strings.push('hidden')
  doc.layout.styles = doc.layout.nodeIndex.map((ni) => ni === node ? [-1, -1, -1, -1, -1, snap.strings.length - 1, -1] : [])
  return snap
}

export function withCanvas(snapshot: DomSnapshot): DomSnapshot {
  const snap = structuredClone(snapshot), doc = snap.documents[0]!, ni = (doc.nodes.nodeValue as number[]).length
  snap.strings.push('CANVAS')
  ;(doc.nodes.nodeValue as number[]).push(-1)
  ;(doc.nodes.nodeName as number[]).push(snap.strings.length - 1)
  ;(doc.nodes.attributes as number[][]).push([])
  ;(doc.nodes.parentIndex as number[]).push(0)
  ;(doc.nodes.contentDocumentIndex as number[]).push(-1)
  doc.layout.nodeIndex.push(ni); doc.layout.bounds.push([400, 100, 1200, 1400])
  return snap
}
