/** Mark the original current conversation unsuitable.
 * Stable success: its unique full-list entry disappears/is hidden while the loaded list remains readable;
 * a new explicit rejection result is another positive. Header changes or a missing button alone are unknown.
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues,
  classOf, isDescendantOf,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { REJECT_REASON_OPTIONS } from '../actions/reasonMapping.js'
import { listMaxX } from './ResumeConsentExecutor.js'
import { CancelledError } from '../operations/types.js'
import { BusinessOperationState } from './BusinessOperationState.js'
import { conversationListNamesOf, headerContactOf } from './ChatReadExecutor.js'

export class ChatRejectError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatRejectError'
  }
}

export interface ChatRejectDeps {
  state?: BusinessOperationState
  /** 采集 fresh DOMSnapshot（每次点击前后重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

const REJECT_TEXT = '不合适'
const CONFIRM_TEXT = '确定'
/** 右侧面板头部姓名识别带（device px）：顶部全局栏和会话 tab 行在 y<130，姓名行 y≈207，再往下是履历信息 */
const HEADER_MIN_Y = 130
const HEADER_MAX_Y = 400

export class ChatRejectExecutor {
  private readonly sleep: (ms: number) => Promise<void>
  readonly state: BusinessOperationState

  constructor(private readonly deps: ChatRejectDeps) {
    this.state = deps.state ?? new BusinessOperationState()
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 把当前会话的候选人标记为不合适；成功返回，任何异常/歧义抛 ChatRejectError */
  async rejectCurrent(opts: { expectedContactRef?: string } = {}): Promise<void> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const snap = await this.deps.snapshot()
    this.state.bindContact(snap, opts.expectedContactRef)
    const button = this.locateRejectButton(snap)
    if (!button) {
      const count = this.countRejectButtons(snap)
      throw new ChatRejectError(
        count === 0
          ? '当前页面右侧面板没有「不合适」按钮：请先打开一个会话（或该候选人已标记不合适）'
          : `右侧面板找到 ${count} 个「不合适」按钮，无法确定目标，已停止，请人工查看`,
      )
    }

    // 点击前记录面板头部姓名：标记成功后 BOSS 自动切会话，靠它区分「成功切走」和「点击没生效」
    const nameBefore = this.panelHeaderName(snap)

    await this.state.input(() => this.deps.click(button, viewportOf(snap)), true)
    await this.sleep(1200)

    let after = await this.deps.snapshot()
    if (this.markSucceeded(after, snap)) { this.state.confirmed({ rejected: true, contact: nameBefore }, '原目标会话已从可读列表中移除或隐藏，或出现新的明确标记结果'); return }

    // 原因选择层：没有评估理由可映射，绝不乱选
    const reasonHit = REJECT_REASON_OPTIONS.find((o) => after.strings.some((s) => s.trim() === o))
    if (reasonHit) {
      throw new ChatRejectError(
        `点击「不合适」后弹出了原因选择层（含「${reasonHit}」等选项）：` +
          '手动标记不支持自动选原因，请在页面上人工完成选择',
      )
    }

    // 确认弹层：唯一「确定」则点，歧义/找不到则停止
    const confirm = this.locateConfirmButton(after)
    if (!confirm) {
      this.state.unconfirmed('按钮或会话变化均不能单独证明标记完成')
      throw new ChatRejectError(
        '点击「不合适」后按钮仍在（会话未切换），且未找到确认弹层的「确定」按钮：' +
          '点击可能被拦截或弹层结构变化，已停止，请人工查看页面',
      )
    }
    // Confirm belongs to the original open conversation. A changed header is not a success signal.
    this.state.bindContact(after)
    this.state.businessEffect.status = 'not_started'
    await this.state.input(() => this.deps.click(confirm, viewportOf(after)), true)
    await this.sleep(1200)

    after = await this.deps.snapshot()
    if (!this.markSucceeded(after, snap)) {
      this.state.unconfirmed('缺少明确已标记不合适的结果，未凭按钮消失或会话切换报成功')
      throw new ChatRejectError(
        '确认后「不合适」按钮仍存在且会话未切换：标记结果无法确认，已停止，请人工查看页面',
      )
    }
    this.state.confirmed({ rejected: true, contact: nameBefore }, '确认后原目标会话已从可读列表中移除或隐藏，或出现新的明确标记结果')
  }

  /**
   * The original loaded list entry must be removed/hidden; a panel switch alone is not sufficient.
   */
  private markSucceeded(snap: DomSnapshot, before: DomSnapshot): boolean {
    if (['已标记为不合适', '已标记不合适'].some(text =>
      !before.strings.some(s => s.trim() === text) && this.findPanelText(snap, text).length === 1)) return true
    const contact = headerContactOf(before)
    const prior = this.listState(before, contact), next = this.listState(snap, contact)
    if (!prior || !next || prior.targetItems !== 1 || next.targetItems !== 0) return false
    // BOSS fills the list tail after removing a conversation. Keep every original survivor;
    // when new rows arrive, those survivors must form the unchanged prefix before the new tail.
    const survivors = prior.names.filter(n => n !== contact)
    if (prior.scroll !== next.scroll || survivors.some(name => !next.names.includes(name))) return false
    if (next.names.some(name => !prior.names.includes(name))
      && survivors.some((name, index) => next.names[index] !== name)) return false
    return survivors.length > 0
      || ['暂无沟通记录', '暂无沟通会话'].some(t => snap.strings.includes(t))
  }

  private listState(snapshot: DomSnapshot, contact: string): { names: string[]; targetItems: number; scroll: string } | null {
    const doc = snapshot.documents[0]
    if (!doc?.nodes.attributes || !doc.nodes.parentIndex || !contact) return null
    const roots = indexedValues(doc.nodes.attributes, 'attributes').filter(([ni]) =>
      (classOf(snapshot, 0, ni) ?? '').split(/\s+/).includes('user-list')).map(([ni]) => ni)
    if (roots.length !== 1 || /加载中|正在加载/.test(snapshot.strings.join('\n'))) return null
    const targetItems = indexedValues(doc.nodes.attributes, 'attributes').filter(([ni]) =>
      (classOf(snapshot, 0, ni) ?? '').split(/\s+/).includes('geek-item')
      && isDescendantOf(doc, ni, roots[0]!) === true).filter(([item]) =>
      indexedValues(doc.nodes.nodeValue, 'nodeValue').some(([ni, si]) => snapshot.strings[si]?.trim() === contact
        && isDescendantOf(doc, ni, item) === true && doc.layout.nodeIndex.includes(ni))).length
    return { names: conversationListNamesOf(snapshot), targetItems,
      scroll: JSON.stringify(snapshot.documents.map(d => [d.scrollOffsetX ?? 0, d.scrollOffsetY ?? 0])) }
  }

  /** 右侧面板头部候选人姓名：识别带（cx>LIST_MAX_X, 130≤y≤400）内最上再最左的可见文本 */
  private panelHeaderName(snap: DomSnapshot): string | null {
    const viewport = viewportOf(snap)
    let best: { x: number; y: number; text: string } | null = null
    snap.documents.forEach((document, documentIndex) => {
      const valueByNode = new Map<number, number>()
      for (const [ni, v] of indexedValues(document.nodes.nodeValue, 'nodeValue')) valueByNode.set(ni, v)
      // 后台标签页/隐藏 iframe 的 owner 无可见 bounds，accumulateOwnerOffset 会抛——
      // 这类文档不可能含可见姓名，跳过（真机 2026-08-06 实证）
      let offset: { x: number; y: number }
      try {
        offset = accumulateOwnerOffset(snap, documentIndex)
      } catch {
        return
      }
      document.layout.nodeIndex.forEach((ni, layoutIndex) => {
        const stringIndex = valueByNode.get(ni)
        if (stringIndex === undefined) return
        const b = document.layout.bounds[layoutIndex]
        if (!b || b[2]! <= 0 || b[3]! <= 0) return
        const text = (snap.strings[stringIndex] ?? '').trim()
        if (!text) return
        const x = offset.x + b[0]! + b[2]! / 2 - (document.scrollOffsetX ?? 0)
        const y = offset.y + b[1]! + b[3]! / 2 - (document.scrollOffsetY ?? 0)
        if (x <= listMaxX(viewport.width) || y < HEADER_MIN_Y || y > HEADER_MAX_Y) return
        if (!best || y < best.y - 1 || (Math.abs(y - best.y) <= 1 && x < best.x)) {
          best = { x, y, text }
        }
      })
    })
    return best ? (best as { text: string }).text : null
  }

  /** 右侧面板唯一「不合适」按钮；0 或多个返回 null（由调用方区分报错文案） */
  private locateRejectButton(snap: DomSnapshot): ClickPoint | null {
    const hits = this.findRejectButtons(snap)
    return hits.length === 1 ? hits[0]! : null
  }

  /** 右侧面板（cx > LIST_MAX_X）视口内全部「不合适」命中，按 y 排序 */
  private findRejectButtons(snap: DomSnapshot): ClickPoint[] {
    return this.findPanelText(snap, REJECT_TEXT).sort((a, b) => a.y - b.y)
  }

  private countRejectButtons(snap: DomSnapshot): number {
    return this.findRejectButtons(snap).length
  }

  /** 全页唯一可见「确定」按钮（确认弹层）；0 或多个返回 null */
  private locateConfirmButton(snap: DomSnapshot): ClickPoint | null {
    const stringIndexes = allStringIndexes(snap, CONFIRM_TEXT)
    if (stringIndexes.length === 0) return null
    const viewport = viewportOf(snap)
    const hits: ClickPoint[] = []
    for (const stringIndex of stringIndexes) {
      snap.documents.forEach((document, documentIndex) => {
        for (const { bounds } of findNodesByString(document, stringIndex)) {
          if (bounds[2] <= 0 || bounds[3] <= 0) continue
          const offset = accumulateOwnerOffset(snap, documentIndex)
          const c = boundsCenter(bounds)
          const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
          const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
          if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) continue
          hits.push({ x, y })
        }
      })
    }
    return hits.length === 1 ? hits[0]! : null
  }

  /** 右侧面板内全文精确命中（trim，cx > LIST_MAX_X + 视口内），返回屏幕坐标（已减文档滚动偏移） */
  private findPanelText(snap: DomSnapshot, text: string): ClickPoint[] {
    const viewport = viewportOf(snap)
    const hits: ClickPoint[] = []
    for (const stringIndex of allStringIndexes(snap, text)) {
      snap.documents.forEach((document, documentIndex) => {
        for (const { bounds } of findNodesByString(document, stringIndex)) {
          if (bounds[2] <= 0 || bounds[3] <= 0) continue
          const offset = accumulateOwnerOffset(snap, documentIndex)
          const c = boundsCenter(bounds)
          const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
          const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
          if (x <= listMaxX(viewport.width) || x > viewport.width || y < 0 || y > viewport.height) continue
          hits.push({ x, y })
        }
      })
    }
    return hits
  }
}

/**
 * 同一文案的全部 string 下标。真机实测（2026-08-06）：同一文案在 strings 表可能有多个条目
 * （如「不合适」有 2 个下标，可见布局节点挂在第 2 个上），findIndex 只取第一个会漏。
 */
function allStringIndexes(snap: DomSnapshot, text: string): number[] {
  const indexes: number[] = []
  snap.strings.forEach((s, i) => {
    if (s.trim() === text) indexes.push(i)
  })
  return indexes
}
