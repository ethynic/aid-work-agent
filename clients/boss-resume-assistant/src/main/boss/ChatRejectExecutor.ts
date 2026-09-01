/**
 * 沟通页「不合适」标记执行器（设计文档 §10.4，CLI reject 子命令 / chat 对话模式）。
 *
 * 沟通页当前会话右侧面板底部有「不合适」按钮，点击将当前沟通的候选人标记为不合适。
 * 手动触发的单发写动作：不写 ActionStore、不滚动、不批量，一次只标当前会话。
 *
 * 流程（fail-loud，任何歧义都停止请人工查看，绝不盲点）：
 * 1. fresh snapshot 定位右侧面板（cx > 850，同 ResumeConsentExecutor 分界）唯一「不合适」按钮；
 *    0 个（未打开会话/已标记）或多个 → 报错
 * 2. Win32 真实鼠标点击（写动作按钮被风控拦截，见设计文档 §10.3 / §17）
 * 3. 点击后分支：
 *    - 「不合适」直接消失 → 成功
 *    - 出现唯一「确定」按钮（确认弹层）→ 再点「确定」，然后校验「不合适」消失
 *    - 出现不合适原因选项（REJECT_REASON_OPTIONS 文案）→ 报错请人工选择：
 *      手动触发场景没有评估理由可映射，绝不乱选原因
 * 4. 结果确认（满足其一即成功）：
 *    - 右侧区域「不合适」消失
 *    - 右侧面板头部候选人姓名已变（真机 2026-08-06：标记成功后 BOSS 自动切换到
 *      下一个会话，新会话也有「不合适」按钮，只查按钮会误报失败）
 *    两者都不满足 = 点击被拦截/弹层未处理，报错
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { REJECT_REASON_OPTIONS } from '../actions/reasonMapping.js'
import { listMaxX } from './ResumeConsentExecutor.js'
import { CancelledError } from '../operations/types.js'

export class ChatRejectError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatRejectError'
  }
}

export interface ChatRejectDeps {
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

  constructor(private readonly deps: ChatRejectDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 把当前会话的候选人标记为不合适；成功返回，任何异常/歧义抛 ChatRejectError */
  async rejectCurrent(): Promise<void> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const snap = await this.deps.snapshot()
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

    await this.deps.click(button, viewportOf(snap))
    await this.sleep(1200)

    let after = await this.deps.snapshot()
    if (this.markSucceeded(after, nameBefore)) return

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
      throw new ChatRejectError(
        '点击「不合适」后按钮仍在（会话未切换），且未找到确认弹层的「确定」按钮：' +
          '点击可能被拦截或弹层结构变化，已停止，请人工查看页面',
      )
    }
    await this.deps.click(confirm, viewportOf(after))
    await this.sleep(1200)

    after = await this.deps.snapshot()
    if (!this.markSucceeded(after, nameBefore)) {
      throw new ChatRejectError(
        '确认后「不合适」按钮仍存在且会话未切换：标记结果无法确认，已停止，请人工查看页面',
      )
    }
  }

  /**
   * 标记成功判定（真机 2026-08-06 校准）：「不合适」消失，或面板头部姓名已变。
   * BOSS 标记成功后会自动切到下一个会话（新会话也有「不合适」按钮），只查按钮会误报失败。
   */
  private markSucceeded(snap: DomSnapshot, nameBefore: string | null): boolean {
    if (this.countRejectButtons(snap) === 0) return true
    const nameNow = this.panelHeaderName(snap)
    return nameBefore !== null && nameNow !== null && nameNow !== nameBefore
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
