import { indexedValues, parentMapOf, type DomSnapshot } from './domSnapshot.js'

interface VisibilityIndex {
  documents: Array<{ parents: Map<number, number> | null; layout: Map<number, number>; visible: Map<number, boolean> }>
  owners: Map<number, Array<{ di: number; ni: number }>>
}
const indexCache = new WeakMap<DomSnapshot, VisibilityIndex>()

function indexOf(snapshot: DomSnapshot): VisibilityIndex {
  const cached = indexCache.get(snapshot)
  if (cached) return cached
  const owners: VisibilityIndex['owners'] = new Map()
  const documents = snapshot.documents.map((doc, di) => {
    for (const [ni, child] of indexedValues(doc.nodes.contentDocumentIndex, 'contentDocumentIndex')) {
      if (child < 0 || child === undefined) continue
      const existing = owners.get(child) ?? []
      existing.push({ di, ni }); owners.set(child, existing)
    }
    return { parents: parentMapOf(doc), layout: new Map(doc.layout.nodeIndex.map((node, index) => [node, index])), visible: new Map<number, boolean>() }
  })
  const index = { documents, owners }
  indexCache.set(snapshot, index)
  return index
}

/** FILTER_SNAPSHOT_OPTIONS preserves display/visibility/opacity at indices 2/5/6. */
export function snapshotNodeVisible(snapshot: DomSnapshot, documentIndex: number, nodeIndex: number): boolean {
  const seen = new Set<string>()
  const index = indexOf(snapshot), path: Array<{ di: number; ni: number }> = []
  const result = (visible: boolean) => {
    for (const node of path) index.documents[node.di]?.visible.set(node.ni, visible)
    return visible
  }
  let di = documentIndex, ni: number | undefined = nodeIndex
  while (ni !== undefined) {
    const key = `${di}:${ni}`
    if (seen.has(key)) return result(false)
    seen.add(key)
    const doc = snapshot.documents[di]
    if (!doc) return result(false)
    const indexes = index.documents[di]!
    const cached = indexes.visible.get(ni)
    if (cached !== undefined) return result(cached)
    path.push({ di, ni })
    const layoutIndex = indexes.layout.get(ni) ?? -1
    const styles = layoutIndex < 0 ? [] : doc.layout.styles?.[layoutIndex] ?? []
    const display = snapshot.strings[styles[2] ?? -1], visibility = snapshot.strings[styles[5] ?? -1], opacity = snapshot.strings[styles[6] ?? -1]
    if (display === 'none' || visibility === 'hidden' || visibility === 'collapse' || (opacity !== undefined && opacity.trim() !== '' && Number(opacity) === 0)) return result(false)
    const parent: number | undefined = indexes.parents?.get(ni)
    if (parent === ni && ni !== 0) return result(false)
    if (parent !== undefined && parent >= 0 && parent !== ni) { ni = parent; continue }
    if (di === 0) return result(true)
    const owners = index.owners.get(di) ?? []
    if (owners.length !== 1) return result(false)
    di = owners[0]!.di; ni = owners[0]!.ni
  }
  return result(true)
}
