/**
 * DOMSnapshot 类型与索引工具（产品化 spike）。
 * 兼容两种序列化：
 * - sparse: { index: number[], value: T[] }
 * - dense: T[]（Chrome 150）
 *
 * 坐标换算只累加嵌套 iframe owner 的可见 bounds。
 * 注意：layout.bounds 是**文档绝对坐标**（真机 2026-08-05 实测：列表 iframe 滚动后 bounds 不变、
 * document.scrollOffsetY 变），需要屏幕坐标时必须再减 document.scrollOffsetX/Y（见 GreetExecutor）。
 * 主文档 scrollOffset 恒为 0，不减也不错；滚动过的 iframe 文档不减会点歪。
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
    /** DOMSnapshot 恒返回的父节点索引（nodeIndex → parent nodeIndex），探针的容器级消歧用 */
    parentIndex?: SparseOrDense<number>
    /** 节点标签名（指向 strings 的下标，大写如 'INPUT'）；按元素类型过滤用（如定位无文本的搜索框 INPUT） */
    nodeName?: SparseOrDense<number>
    /**
     * 元素属性（指向 strings 的下标）：Chrome 151 真机实测（.tmp/chat-snapshot-01.json，2026-08-27）
     * 为 **dense 嵌套数组** —— attributes[nodeIdx] = [nameIdx, valIdx, nameIdx, valIdx, ...]，
     * name/val 都是 strings 下标；无属性节点为空数组 []（非 undefined），共 5203 项与节点数一致。
     * 类型按 indexedValues 兼容 sparse {index,value}（value 为扁平下标对数组），但真机只见过 dense。
     */
    attributes?: SparseOrDense<number[]>
  }
  layout: {
    nodeIndex: number[]
    bounds: number[][] // [x, y, width, height]
  }
  /** 文档滚动偏移（device px）：BOSS 列表在 iframe 文档内滚动，到底判定用 */
  scrollOffsetX?: number
  scrollOffsetY?: number
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

/** 构建 nodeIndex → parentIndex 映射；快照缺 parentIndex 字段时返回 null */
export function parentMapOf(document: DomDocument): Map<number, number> | null {
  const field = document.nodes.parentIndex
  if (!field) return null
  const map = new Map<number, number>()
  for (const [nodeIndex, parent] of indexedValues(field, 'parentIndex')) {
    map.set(nodeIndex, parent)
  }
  return map
}

const MAX_ANCESTOR_CLIMB = 10000

/** 多节点最近公共祖先（LCA）；缺 parentIndex / 节点不在树中 / 空列表时返回 null */
export function lowestCommonAncestor(document: DomDocument, nodeIndexes: number[]): number | null {
  if (nodeIndexes.length === 0) return null
  const parents = parentMapOf(document)
  if (!parents) return null
  if (nodeIndexes.some((n) => !parents.has(n))) return null
  const ancestorSets = nodeIndexes.map((n) => {
    const set = new Set<number>()
    let cur: number | undefined = n
    for (let i = 0; i < MAX_ANCESTOR_CLIMB && cur !== undefined && !set.has(cur); i++) {
      set.add(cur)
      cur = parents.get(cur)
    }
    return set
  })
  let cur: number | undefined = nodeIndexes[0]!
  for (let i = 0; i < MAX_ANCESTOR_CLIMB && cur !== undefined; i++) {
    if (ancestorSets.every((s) => s.has(cur!))) return cur
    const next = parents.get(cur)
    if (next === cur) break // 根节点自指，防死循环
    cur = next
  }
  return null
}

/**
 * 判断 nodeIndex 是否为 ancestor 的子孙（含自身）。
 * 缺 parentIndex 字段时返回 null 表示「未知」，调用方应回退到几何规则。
 */
export function isDescendantOf(document: DomDocument, nodeIndex: number, ancestor: number): boolean | null {
  const parents = parentMapOf(document)
  if (!parents) return null
  let cur: number | undefined = nodeIndex
  for (let i = 0; i < MAX_ANCESTOR_CLIMB && cur !== undefined; i++) {
    if (cur === ancestor) return true
    const next = parents.get(cur)
    if (next === cur) break
    cur = next
  }
  return false
}

/**
 * 拼出某节点的 class 属性值（无 attributes 字段 / 节点无 class 属性 → undefined）。
 * class 为实测稳定语义名时（如沟通页 .conversation-message / .item-myself，设计 §10.9 真机验证），
 * 这是比几何更精确的定位信号；其它页面的 class 若为混淆 hash 不可依赖（铁律不变）。
 * 注意：dense 形状按 nodeIndex 直取（O(1)）——若在全节点循环里调用，勿改成 indexedValues 全表扫（O(n²)）。
 */
export function classOf(snapshot: DomSnapshot, documentIndex: number, nodeIndex: number): string | undefined {
  const document = snapshot.documents[documentIndex]
  const attrs = document?.nodes?.attributes
  if (!attrs) return undefined
  let pairs: number[] | undefined
  if (Array.isArray(attrs)) {
    // dense：attributes[nodeIdx] 即该节点属性对（Chrome 151 真机形状，见上方字段注释）
    pairs = attrs[nodeIndex]
  } else if (Array.isArray(attrs.index) && Array.isArray(attrs.value)) {
    // sparse：{index,value} 按下标对齐取该节点条目（真机未见过，形状兼容用）
    const pos = attrs.index.indexOf(nodeIndex)
    pairs = pos >= 0 ? attrs.value[pos] : undefined
  } else {
    return undefined
  }
  if (!Array.isArray(pairs)) return undefined
  // pairs = [nameIdx, valIdx, ...]（Chrome 151 实测形状，见 DomDocument.nodes.attributes 注释）
  for (let i = 0; i + 1 < pairs.length; i += 2) {
    if (snapshot.strings[pairs[i]!] === 'class') return snapshot.strings[pairs[i + 1]!]
  }
  return undefined
}
