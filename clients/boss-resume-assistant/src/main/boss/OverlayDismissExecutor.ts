/**
 * 弹层自愈原语二：按白名单文案关闭弹层（2026-08-31）。
 *
 * 流程：fresh 快照定位目标文本节点（取文档序最后一个匹配——弹层节点通常在 DOM 尾部）
 * → Win32 真实鼠标点击控件中心（弹层按钮属页面交互控件，与筛选/发送同通道防风控）
 * → 等待动画 → fresh 快照校验该文本已消失（出现次数减少即算），仍在则 UI_CHANGED。
 *
 * 安全铁律：只接受 isDismissText 白名单内的关闭语义文案（关闭/知道了/以后再说/取消/× 等）。
 * 云端 LLM 只能在白名单里挑，本层再校验一次——就算 LLM 幻觉出「立即领取」也会被拒绝，
 * 绝不产生领券/跳转/开通等非关闭副作用。
 */
import { indexedValues, classOf, type ClickPoint, type DomSnapshot } from './domSnapshot.js'
import { ICON_CLOSE_HINT, isDismissText } from './OverlayInspector.js'
import { CancelledError } from '../operations/types.js'

export class OverlayDismissError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'OverlayDismissError'
  }
}

export interface OverlayDismissDeps {
  /** 采集 fresh DOMSnapshot */
  snapshot(): Promise<DomSnapshot>
  /** CDP 合成点击（浏览类 UI 通道）。2026-08-31 真机对照实验：弹层关闭 × 用 Win32 真实点击
   *  无效（事件被吞/未生效）、CDP 合成点击一击命中——弹层关闭属页面 UI 操作，CDP 为主通道 */
  clickBrowse(point: ClickPoint): Promise<void>
  /** Win32 真实鼠标点击（兜底通道：CDP 点击后未生效时重试一次） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 协作式取消信号 */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

/** 点击后等弹层关闭动画（ms） */
const DISMISS_SETTLE = 800

interface TextHit {
  center: ClickPoint
  cls: string
}

/** 快照中精确匹配 text 的节点（主文档，视口内），取文档序最后一个（弹层通常在 DOM 尾部） */
function locateText(snapshot: DomSnapshot, text: string): TextHit | null {
  const doc = snapshot.documents[0]
  if (!doc?.layout) return null
  const valueByNode = new Map<number, number>()
  for (const [nodeIdx, strIdx] of indexedValues(doc.nodes.nodeValue, 'nodeValue')) {
    valueByNode.set(nodeIdx, strIdx)
  }
  const root = doc.layout.bounds[0] as [number, number, number, number] | undefined
  const vpW = root ? root[0] + root[2] : Infinity
  const vpH = root ? root[1] + root[3] : Infinity

  let last: TextHit | null = null
  for (let i = 0; i < Math.min(doc.layout.nodeIndex.length, doc.layout.bounds.length); i++) {
    const nodeIdx = doc.layout.nodeIndex[i]!
    const b = doc.layout.bounds[i] as [number, number, number, number] | undefined
    if (!b || b.length < 4) continue
    const strIdx = valueByNode.get(nodeIdx)
    if (strIdx === undefined) continue
    if ((snapshot.strings[strIdx] ?? '').trim() !== text) continue
    const [x, y, w, h] = b
    if (w <= 0 || h <= 0 || x < 0 || y < 0 || x > vpW || y > vpH) continue
    last = {
      center: { x: Math.round(x + w / 2), y: Math.round(y + h / 2) },
      cls: (classOf(snapshot, 0, nodeIdx) ?? '').slice(0, 36),
    }
  }
  return last
}

/** 快照中 class 含 clsFragment 的节点（icon 关闭控件定位），取文档序最后一个 */
function locateIcon(snapshot: DomSnapshot, clsFragment: string): TextHit | null {
  const doc = snapshot.documents[0]
  if (!doc?.layout) return null
  const root = doc.layout.bounds[0] as [number, number, number, number] | undefined
  const vpW = root ? root[0] + root[2] : Infinity
  const vpH = root ? root[1] + root[3] : Infinity
  let last: TextHit | null = null
  for (let i = 0; i < Math.min(doc.layout.nodeIndex.length, doc.layout.bounds.length); i++) {
    const nodeIdx = doc.layout.nodeIndex[i]!
    const b = doc.layout.bounds[i] as [number, number, number, number] | undefined
    if (!b || b.length < 4) continue
    const cls = classOf(snapshot, 0, nodeIdx) ?? ''
    if (!cls.includes(clsFragment)) continue
    const [x, y, w, h] = b
    if (w <= 0 || h <= 0 || x < 0 || y < 0 || x > vpW || y > vpH) continue
    last = { center: { x: Math.round(x + w / 2), y: Math.round(y + h / 2) }, cls: cls.slice(0, 36) }
  }
  return last
}

function countText(snapshot: DomSnapshot, text: string): number {
  const doc = snapshot.documents[0]
  if (!doc?.layout) return 0
  const valueByNode = new Map<number, number>()
  for (const [nodeIdx, strIdx] of indexedValues(doc.nodes.nodeValue, 'nodeValue')) {
    valueByNode.set(nodeIdx, strIdx)
  }
  let count = 0
  for (let i = 0; i < Math.min(doc.layout.nodeIndex.length, doc.layout.bounds.length); i++) {
    const strIdx = valueByNode.get(doc.layout.nodeIndex[i]!)
    if (strIdx === undefined) continue
    if ((snapshot.strings[strIdx] ?? '').trim() === text) count++
  }
  return count
}

function countIcon(snapshot: DomSnapshot, clsFragment: string): number {
  const doc = snapshot.documents[0]
  if (!doc?.layout) return 0
  let count = 0
  for (let i = 0; i < Math.min(doc.layout.nodeIndex.length, doc.layout.bounds.length); i++) {
    const cls = classOf(snapshot, 0, doc.layout.nodeIndex[i]!) ?? ''
    if (cls.includes(clsFragment)) count++
  }
  return count
}

export class OverlayDismissExecutor {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: OverlayDismissDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  async dismiss(opts: { text: string }): Promise<{ dismissed: boolean; text: string }> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const raw = (opts.text ?? '').trim()
    if (!raw) throw new OverlayDismissError('text（关闭控件文本）不能为空')

    // 安全铁律：白名单双重校验的第二道（第一道在云端 LLM 选择约束）。
    // 两种引用形态：普通文本（命中关闭语义白名单）/ icon:<cls>（class 命中关闭语义惯例
    // close/guanbi——2026-08-31 真机实证广告弹窗关闭 × 无文字，class 即其语义）
    const iconMode = raw.toLowerCase().startsWith('icon:')
    const iconCls = iconMode ? raw.slice(5).trim() : ''
    if (iconMode) {
      if (!iconCls || !ICON_CLOSE_HINT.test(iconCls)) {
        throw new OverlayDismissError(
          `「${raw}」class 未命中关闭语义（close/guanbi），拒绝点击（防止误点领取/开通类按钮）`,
        )
      }
    } else if (!isDismissText(raw)) {
      throw new OverlayDismissError(`「${raw}」不在关闭语义白名单内，拒绝点击（防止误点领取/开通类按钮）`)
    }

    const before = await this.deps.snapshot()
    const hit = iconMode ? locateIcon(before, iconCls) : locateText(before, raw)
    if (!hit) {
      throw new OverlayDismissError(`页面上未找到「${raw}」控件（可能弹层已自动消失）`)
    }

    const root = before.documents[0]?.layout?.bounds[0] as [number, number, number, number] | undefined
    const viewport = {
      width: root ? Math.round(root[0] + root[2]) : 1249,
      height: root ? Math.round(root[1] + root[3]) : 1277,
    }
    // 双通道：CDP 合成点击为主（真机实证弹层 × 有效），未生效回退 Win32 真实点击再验一次
    await this.deps.clickBrowse(hit.center)
    await this.sleep(DISMISS_SETTLE)
    let after = await this.deps.snapshot()
    let remaining = iconMode ? countIcon(after, iconCls) : countText(after, raw)
    if (remaining >= (iconMode ? countIcon(before, iconCls) : countText(before, raw))) {
      await this.deps.click(hit.center, viewport)
      await this.sleep(DISMISS_SETTLE)
      after = await this.deps.snapshot()
      remaining = iconMode ? countIcon(after, iconCls) : countText(after, raw)
    }
    const previous = iconMode ? countIcon(before, iconCls) : countText(before, raw)
    if (remaining >= previous) {
      throw new OverlayDismissError(`已点击「${raw}」但弹层未关闭（控件仍存在），请人工查看页面`)
    }
    return { dismissed: true, text: raw }
  }
}
