import { accumulateOwnerOffset, classOf, indexedValues, isDescendantOf, type DomSnapshot } from './domSnapshot.js'
import { collectOverlayCandidates, isDismissText } from './OverlayInspector.js'
import { OverlayDismissExecutor } from './OverlayDismissExecutor.js'
import { snapshotNodeVisible } from './SnapshotVisibility.js'

export interface FilterPaywall {
  documentIndex: number
  nodeIndex: number
  closeText: string | null
}

function attributes(snapshot: DomSnapshot, di: number, ni: number): Map<string, string> {
  const field = snapshot.documents[di]?.nodes.attributes
  const pair = Array.isArray(field) ? field[ni] : field?.value[field.index.indexOf(ni)]
  const result = new Map<string, string>()
  for (let i = 0; pair && i < pair.length; i += 2) result.set(snapshot.strings[pair[i]!] ?? '', snapshot.strings[pair[i + 1]!] ?? '')
  return result
}

/** 确认可见弹层结构+会员文案+购买/开通文案；面板宣传标题或任意UI_CHANGED都不满足。 */
export function inspectFilterPaywall(snapshot: DomSnapshot): FilterPaywall | null {
  const candidates: FilterPaywall[] = []
  const root = snapshot.documents[0]?.layout.bounds[0]
  if (!root) return null
  snapshot.documents.forEach((doc, di) => {
    let offset: { x: number; y: number }
    try { offset = accumulateOwnerOffset(snapshot, di) } catch { return }
    const values = new Map(indexedValues(doc.nodes.nodeValue, 'nodeValue'))
    for (let i = 0; i < doc.layout.nodeIndex.length; i++) {
      const ni = doc.layout.nodeIndex[i]!, b = doc.layout.bounds[i]
      if (!b || b[2]! <= 0 || b[3]! <= 0 || !b.every(Number.isFinite)) continue
      if (!snapshotNodeVisible(snapshot, di, ni)) continue
      const x = b[0]! + offset.x - (doc.scrollOffsetX ?? 0), y = b[1]! + offset.y - (doc.scrollOffsetY ?? 0)
      if (x < 0 || y < 0 || x + b[2]! > root[2]! || y + b[3]! > root[3]!) continue
      const cls = classOf(snapshot, di, ni) ?? ''
      if (attributes(snapshot, di, ni).get('role') !== 'dialog' && !/(?:dialog|modal|popup|pop-layer)/i.test(cls)) continue
      const texts = doc.layout.nodeIndex.filter((node) => snapshotNodeVisible(snapshot, di, node) && isDescendantOf(doc, node, ni) === true)
        .map((node) => (snapshot.strings[values.get(node) ?? -1] ?? '').trim()).filter(Boolean)
      if (!texts.some((text) => /VIP|会员/i.test(text)) || !texts.some((text) => /(?:开通|购买|订购).*(?:VIP|会员)|立即开通|立即购买|升级VIP/i.test(text)) || new Set(texts).size < 2) continue
      const paywall = { documentIndex: di, nodeIndex: ni, closeText: null as string | null }
      const projected = scopedPaywallSnapshot(snapshot, paywall)
      const inspection = collectOverlayCandidates(projected)
      const textsToClose = [...new Set(inspection.candidates.filter((c) => isDismissText(c.text)).map((c) => c.text))]
      const explicit = textsToClose.filter((text) => ['关闭', '关闭弹窗', '×', '✕', '✖', 'Close'].includes(text))
      if (explicit.length === 1) paywall.closeText = explicit[0]!
      else if (explicit.length === 0 && inspection.icon_candidates.length === 1) paywall.closeText = `icon:${inspection.icon_candidates[0]!.icon_cls}`
      else if (explicit.length === 0 && inspection.icon_candidates.length === 0 && textsToClose.length === 1) paywall.closeText = textsToClose[0]!
      candidates.push(paywall)
    }
  })
  // 父/子都带modal class时保留最内层已确认完整文案结构；多个独立购买窗不能猜测。
  const inner = candidates.filter((candidate) => !candidates.some((other) => other !== candidate && other.documentIndex === candidate.documentIndex
    && isDescendantOf(snapshot.documents[candidate.documentIndex]!, other.nodeIndex, candidate.nodeIndex) === true))
  if (inner.length > 1) throw new Error('多个会员付费弹窗结构同时存在，无法安全关闭')
  return inner[0] ?? null
}

/** 将确认弹层子树投影成主文档，复用已有关闭原语并排除弹层外的取消/关闭。 */
function scopedPaywallSnapshot(snapshot: DomSnapshot, paywall: FilterPaywall): DomSnapshot {
  const doc = snapshot.documents[paywall.documentIndex]!
  const root = snapshot.documents[0]!.layout.bounds[0]!
  const offset = accumulateOwnerOffset(snapshot, paywall.documentIndex)
  const nodes = doc.layout.nodeIndex.filter((ni) => snapshotNodeVisible(snapshot, paywall.documentIndex, ni) && isDescendantOf(doc, ni, paywall.nodeIndex) === true)
  const value = new Map(indexedValues(doc.nodes.nodeValue, 'nodeValue'))
  const names = doc.nodes.nodeName ? new Map(indexedValues(doc.nodes.nodeName, 'nodeName')) : new Map<number, number>()
  const attrs = doc.nodes.attributes ? new Map(indexedValues(doc.nodes.attributes, 'attributes')) : new Map<number, number[]>()
  return { strings: snapshot.strings, documents: [{
    nodes: { nodeValue: [-1, ...nodes.map((ni) => value.get(ni) ?? -1)], nodeName: [-1, ...nodes.map((ni) => names.get(ni) ?? -1)], attributes: [[], ...nodes.map((ni) => attrs.get(ni) ?? [])], contentDocumentIndex: [] },
    layout: { nodeIndex: [0, ...nodes.map((_, i) => i + 1)], bounds: [root, ...nodes.map((ni) => {
      const b = doc.layout.bounds[doc.layout.nodeIndex.indexOf(ni)]!
      return [b[0]! + offset.x - (doc.scrollOffsetX ?? 0), b[1]! + offset.y - (doc.scrollOffsetY ?? 0), b[2]!, b[3]!]
    })] },
  }] }
}

export async function closeFilterPaywall(deps: {
  snapshot(): Promise<DomSnapshot>
  click(point: { x: number; y: number }, viewport: { width: number; height: number }): Promise<void>
  signal?: AbortSignal
  sleep?(ms: number): Promise<void>
}): Promise<void> {
  let full = await deps.snapshot()
  const original = inspectFilterPaywall(full)
  if (!original?.closeText) throw new Error('会员付费弹窗没有唯一且明确的安全关闭控件')
  let attempted = false
  const safeClick = async (point: { x: number; y: number }): Promise<void> => {
    if (attempted) throw new Error('会员付费弹窗首次关闭未确认，禁止使用缓存坐标再次点击')
    full = await deps.snapshot()
    const current = inspectFilterPaywall(full)
    if (!current || current.closeText !== original.closeText) throw new Error('会员付费弹窗关闭目标已变化，拒绝点击')
    const projected = scopedPaywallSnapshot(full, current), doc = projected.documents[0]!
    const values = new Map(indexedValues(doc.nodes.nodeValue, 'nodeValue'))
    const icon = original.closeText!.startsWith('icon:'), fragment = original.closeText!.slice(5)
    const points = doc.layout.nodeIndex.flatMap((node, i) => {
      const matches = icon ? (classOf(projected, 0, node) ?? '').includes(fragment)
        : (projected.strings[values.get(node) ?? -1] ?? '').trim() === original.closeText
      const b = doc.layout.bounds[i]
      return matches && b && b[2]! > 0 && b[3]! > 0 ? [{ x: Math.round(b[0]! + b[2]! / 2), y: Math.round(b[1]! + b[3]! / 2) }] : []
    })
    if (points.length !== 1 || points[0]!.x !== point.x || points[0]!.y !== point.y) throw new Error('会员付费弹窗关闭落点在真实输入前变化或不唯一，拒绝盲点')
    attempted = true
    const root = full.documents[0]!.layout.bounds[0]!
    await deps.click(point, { width: root[2]!, height: root[3]! })
  }
  const closer = new OverlayDismissExecutor({
    snapshot: async () => {
      full = await deps.snapshot()
      const current = inspectFilterPaywall(full)
      if (!current) return { strings: [''], documents: [{ nodes: { nodeValue: [-1], contentDocumentIndex: [] }, layout: { nodeIndex: [0], bounds: [full.documents[0]!.layout.bounds[0]!] } }] }
      if (current.closeText !== original.closeText) throw new Error('会员付费弹窗关闭目标已变化，拒绝点击')
      return scopedPaywallSnapshot(full, current)
    },
    // 复用已有原语的白名单和fresh消失校验；此业务关闭统一走真实Win32，不发送未经CSS桥接的浏览点击。
    clickBrowse: safeClick,
    click: safeClick, signal: deps.signal, sleep: deps.sleep,
  })
  await closer.dismiss({ text: original.closeText })
  if (inspectFilterPaywall(await deps.snapshot())) throw new Error('会员付费弹窗未完整消失，拒绝继续筛选')
}
