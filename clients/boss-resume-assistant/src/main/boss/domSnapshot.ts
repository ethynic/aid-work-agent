/**
 * DOMSnapshot 类型与索引工具（产品化 spike）。
 * 兼容两种序列化：
 * - sparse: { index: number[], value: T[] }
 * - dense: T[]（Chrome 150）
 *
 * 坐标换算只累加嵌套 iframe owner 的可见 bounds，不减 document/owner scrollOffset
 * （设计文档 §7.1：Chrome 150 layout.bounds 已是各文档视口相对坐标）。
 */

/** DOMSnapshot 顶层结构 */
export interface DomSnapshot {
  strings: string[]
  documents: DomDocument[]
}

export interface DomDocument {
  nodes: {
    nodeValue: SparseOrDense<number>
    contentDocumentIndex: SparseOrDense<number>
  }
  layout: {
    nodeIndex: number[]
    bounds: number[][] // [x, y, width, height]
  }
}

/** sparse {index,value} 或 dense 数组 */
export type SparseOrDense<T> = T[] | { index: number[]; value: T[] }

export interface ClickPoint {
  x: number
  y: number
}

export interface LayoutNode {
  documentIndex: number
  nodeIndex: number
  bounds: [number, number, number, number] // [x, y, w, h]
}

/** 将 sparse/dense 字段统一成 [nodeIndex, value][] 对 */
export function indexedValues<T>(field: SparseOrDense<T> | undefined, name: string): Array<[number, T]> {
  if (Array.isArray(field)) {
    return field.map((value, index) => [index, value])
  }
  if (field && Array.isArray(field.index) && Array.isArray(field.value) && field.index.length === field.value.length) {
    return field.index.map((index, position) => [index, field.value[position]!])
  }
  throw new Error(`Unsupported DOMSnapshot ${name} shape`)
}

/** 找到 string 在 strings 数组中的下标，不存在抛错（fail-loud） */
export function stringIndexOf(snapshot: DomSnapshot, text: string): number {
  const idx = snapshot.strings.indexOf(text)
  if (idx < 0) throw new Error(`text absent from snapshot strings: ${text}`)
  return idx
}

/** 在某 document 中找所有 nodeValue === stringIndex 的节点的 layout bounds */
export function findNodesByString(
  document: DomDocument,
  stringIndex: number,
): Array<{ nodeIndex: number; bounds: [number, number, number, number] }> {
  const out: Array<{ nodeIndex: number; bounds: [number, number, number, number] }> = []
  for (const [nodeIndex, value] of indexedValues(document.nodes.nodeValue, 'nodeValue')) {
    if (value !== stringIndex) continue
    const layoutIndex = document.layout.nodeIndex.indexOf(nodeIndex)
    if (layoutIndex < 0) continue
    const b = document.layout.bounds[layoutIndex]
    if (b && b.length === 4) {
      out.push({ nodeIndex, bounds: [b[0]!, b[1]!, b[2]!, b[3]!] })
    }
  }
  return out
}

/** 计算某 documentIndex 的祖先 owner 偏移累加（视口坐标） */
export function accumulateOwnerOffset(snapshot: DomSnapshot, documentIndex: number): { x: number; y: number } {
  let offsetX = 0
  let offsetY = 0
  let current = documentIndex
  const visited = new Set<number>()
  while (current !== 0) {
    if (visited.has(current)) throw new Error('frame ancestry cycle detected')
    visited.add(current)
    const owners: Array<{ document: DomDocument; nodeIndex: number }> = []
    snapshot.documents.forEach((document, ownerDocIndex) => {
      for (const [nodeIndex, value] of indexedValues(document.nodes.contentDocumentIndex, 'contentDocumentIndex')) {
        if (value === current) owners.push({ document, nodeIndex })
      }
    })
    if (owners.length !== 1) {
      throw new Error(`frame must have exactly one visible owner; found ${owners.length} for document ${current}`)
    }
    const owner = owners[0]!
    const layoutIndex = owner.document.layout.nodeIndex.indexOf(owner.nodeIndex)
    if (layoutIndex < 0) throw new Error('owner frame has no visible layout bounds')
    const b = owner.document.layout.bounds[layoutIndex]!
    offsetX += b[0]!
    offsetY += b[1]!
    current = snapshot.documents.indexOf(owner.document)
  }
  return { x: offsetX, y: offsetY }
}

/** bounds 中心点（仅本 document 坐标，未含 owner 偏移） */
export function boundsCenter(bounds: [number, number, number, number]): ClickPoint {
  const [x, y, w, h] = bounds
  return { x: x + w / 2, y: y + h / 2 }
}
