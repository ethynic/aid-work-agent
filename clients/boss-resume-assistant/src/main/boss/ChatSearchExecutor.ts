/**
 * 沟通页搜索找人执行器（设计文档 §10.6，CLI send-to 子命令的找人段）。
 *
 * 沟通页顶部有搜索入口（CSS 背景图标，DOMSnapshot 抓不到节点），点击后弹出搜索框；
 * 输入姓名后结果按「联系人/职位/...」分类，点「联系人」下第一张结果卡片进入对话。
 *
 * 真机校准（2026-08-13，窗口 1249x1277）：
 * - 搜索图标：SEARCH_ICON_POINT。GetCursorPos 校准值——CSS 背景图标在 DOMSnapshot 里抓不到节点，
 *   无法几何定位，只能用真机标定的固定坐标（窗口尺寸/布局变化时需重新校准）。
 * - 搜索框：点图标后弹出的 doc0 INPUT（cx<850、y∈[100,200]、w>200，按 nodeName=INPUT 过滤唯一定位）。
 * - 搜索结果项：输入姓名后等异步渲染（SEARCH_RESULT_DELAY=2500ms），结果项有 layout bounds，
 *   直接按目标姓名在视口内（cx<850）唯一定位点击。真机修正（2026-08-13）：曾因 sleep 太早（1100ms）
 *   误判"结果卡片人名 DOMSnapshot 抓不到"，实为延时不够；且不再点固定第一项（目标未必在第一项，会点错人）。
 * - 进入对话校验：发送按钮出现（locateSendButton 命中 1 个）。
 *
 * 关键结论（会话列表滚动 CDP mouseWheel + Win32 mouse_event 双失效，BOSS 非标准滚动）→ 改用搜索找人。
 *
 * 安全设计（fail-loud）：搜索框 0/多个、进入对话后无发送按钮 → 抛 ChatSearchError，绝不盲点。
 * 写动作（点图标/搜索框/结果卡片）走 Win32（deps.click）；逐字输入姓名走 CDP（typeChar）。
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
import { locateSendButton } from './ChatSendExecutor.js'
import { CancelledError } from '../operations/types.js'

export class ChatSearchError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatSearchError'
  }
}

export interface ChatSearchDeps {
  /** 采集 fresh DOMSnapshot（每次定位前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** CDP char 事件逐字输入（调用方保证焦点已在目标输入框） */
  typeChar(ch: string): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

/** 搜索图标屏幕坐标（device px）。GetCursorPos 校准值：CSS 背景图标 DOMSnapshot 抓不到节点，只能用真机标定的固定坐标（窗口尺寸/布局变化时需重新校准） */
const SEARCH_ICON_POINT: ClickPoint = { x: 519, y: 135 }
/** 搜索框几何特征（doc0 layout bounds，device px）：cx<850、y∈[100,200]、w>200 */
const SEARCH_BOX_MAX_CX = 850
const SEARCH_BOX_MIN_CY = 100
const SEARCH_BOX_MAX_CY = 200
const SEARCH_BOX_MIN_W = 200
/** 「联系人」标签下方结果项的 y 范围（device px）：结果浮层项在联系人紧贴下方此范围内（排除更下方的会话列表连续姓名节点） */
const CONTACTS_RESULT_BAND = 80
/** 点搜索图标后等弹层（ms） */
const SEARCH_OPEN_DELAY = 1400
/** 输入姓名后等搜索结果异步渲染（ms）。真机修正（2026-08-13）：搜索结果项（公司名）bounds 异步渲染较慢，
 *  太早（<3000ms）项无 bounds 会误判"抓不到"；真机实测需 ~4000ms 稳定出现 */
const SEARCH_RESULT_DELAY = 4000
/** 点结果卡片后等进入对话（ms） */
const ENTER_CHAT_DELAY = 2000
/** 点击搜索框后等待聚焦（ms） */
const SEARCH_FOCUS_DELAY = 500
/** 逐字输入间隔（ms，拟人节奏；风控拦鼠标合成事件不拦键盘） */
const TYPE_INTERVAL_MS = 200

export class ChatSearchExecutor {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: ChatSearchDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 搜索姓名并进入其会话：点搜索图标 → 输入姓名 → 点「联系人」下第一张结果卡片。
   * 成功进入对话（发送按钮出现）后返回；任何歧义抛 ChatSearchError（fail-loud，绝不盲点）。
   */
  async openContact(opts: { name: string }): Promise<void> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const name = opts.name
    if (!name) throw new ChatSearchError('搜索姓名不能为空')

    // 1. 点搜索图标（CSS 背景图标，DOMSnapshot 抓不到节点，用真机标定固定坐标）
    const snap0 = await this.deps.snapshot()
    await this.deps.click(SEARCH_ICON_POINT, viewportOf(snap0))
    await this.sleep(SEARCH_OPEN_DELAY)

    // 2. 定位搜索框（doc0 layout 节点，cx<850/y∈[100,200]/w>200，视口内唯一）
    const snap1 = await this.deps.snapshot()
    const box = this.findSearchBox(snap1)
    if (!box.point) {
      throw new ChatSearchError(
        box.count === 0
          ? '点击搜索图标后未找到搜索框（doc0 无 cx<850/y100-200/w>200 的节点）：搜索弹层未打开或页面结构已变，请人工查看'
          : `点击搜索图标后找到 ${box.count} 个候选搜索框节点，无法唯一定位，已停止，请人工查看`,
      )
    }
    await this.deps.click(box.point, viewportOf(snap1))
    await this.sleep(SEARCH_FOCUS_DELAY)

    // 3. CDP 逐字输入姓名（风控拦鼠标合成事件，不拦键盘 char 事件）
    for (const ch of name) {
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：输入搜索姓名时中止')
      await this.deps.typeChar(ch)
      await this.sleep(TYPE_INTERVAL_MS)
    }
    await this.sleep(SEARCH_RESULT_DELAY)

    // 4. 定位目标姓名的搜索结果项（视口内 cx<SEARCH_BOX_MAX_CX 唯一命中，直接点击该坐标）
    //    真机修正（2026-08-13）：搜索结果项异步渲染，等够 SEARCH_RESULT_DELAY 后项有 layout bounds，
    //    可直接按目标姓名定位——不再用「联系人」锚点+固定偏移点第一项（目标未必在第一项，会点错人）。
    const snap2 = await this.deps.snapshot()
    const { point: target, count: targetCount } = this.locateTargetResult(snap2)
    if (targetCount !== 1) {
      throw new ChatSearchError(
        targetCount === 0
          ? `搜索「${name}」后未在结果列表找到该姓名的可见项：搜索无结果或目标在列表深处，请人工查看`
          : `搜索「${name}」在结果列表有 ${targetCount} 个可见命中，无法唯一定位，请人工查看`,
      )
    }
    await this.deps.click(target!, viewportOf(snap2))
    await this.sleep(ENTER_CHAT_DELAY)

    // 6. 校验进入对话：发送按钮出现（唯一命中）
    const snap3 = await this.deps.snapshot()
    const { count } = locateSendButton(snap3)
    if (count !== 1) {
      throw new ChatSearchError(
        count === 0
          ? `点击结果卡片后未进入对话（未找到发送按钮）：搜索结果「${name}」可能不存在或点击未生效，请人工查看`
          : `点击结果卡片后找到 ${count} 个发送按钮，页面状态异常，已停止，请人工查看`,
      )
    }
  }

  /**
   * 搜索框定位（doc0 INPUT 节点 + 几何）：nodeName=INPUT 且 cx<850、y∈[100,200]、w>200。
   * 返回命中数与（唯一时的）屏幕坐标；doc0 是根文档 owner 偏移为 0，无需 accumulateOwnerOffset。
   *
   * 必须 按 nodeName=INPUT 过滤（真机 2026-08-13：doc0 顶部行有多个大 DIV 容器也满足
   * cx<850/y100-200/w>200 的几何条件，只看几何会误判 8 个候选；INPUT 节点唯一）。
   */
  private findSearchBox(snap: DomSnapshot): { point: ClickPoint | null; count: number } {
    const hits: ClickPoint[] = []
    const document = snap.documents[0]
    if (document) {
      const nameByNode = new Map(indexedValues(document.nodes.nodeName, 'nodeName'))
      document.layout.nodeIndex.forEach((nodeIndex, layoutIndex) => {
        if (snap.strings[nameByNode.get(nodeIndex) ?? -1] !== 'INPUT') return
        const b = document.layout.bounds[layoutIndex]!
        if (b[2]! <= 0 || b[3]! <= 0) return
        const cx = b[0]! + b[2]! / 2
        const cy = b[1]! + b[3]! / 2
        if (cx >= SEARCH_BOX_MAX_CX) return
        if (cy < SEARCH_BOX_MIN_CY || cy > SEARCH_BOX_MAX_CY) return
        if (b[2]! < SEARCH_BOX_MIN_W) return
        hits.push({ x: cx, y: cy })
      })
    }
    return { point: hits.length === 1 ? hits[0]! : null, count: hits.length }
  }

  /**
   * 定位搜索结果项：在「联系人」分类标签紧贴下方的结果区，找公司名文本（"_" 开头连续字符串）点击。
   *
   * 真机关键（2026-08-13）：搜索结果浮层姓名是逐字单独节点（反爬，"施""文""斌"），但**公司名是连续字符串
   * 且以 "_" 开头**（如 "_上海功存智能科技有限公司"）。点公司名所在结果项即可进入对话——不靠姓名逐字匹配，
   * 简单可靠（搜索精确匹配，结果第一项即目标）。
   * 用「联系人下方紧贴」y 范围（联系人y+4 ~ +CONTACTS_RESULT_BAND）排除上方搜索框区 + 下方会话列表。
   */
  private locateTargetResult(snap: DomSnapshot): { point: ClickPoint | null; count: number } {
    const viewport = viewportOf(snap)
    const contactsY = this.contactsLabelY(snap)
    if (contactsY === null) return { point: null, count: 0 } // 无联系人标签 = 搜索无结果/浮层未开
    const yMin = contactsY + 4
    const yMax = contactsY + CONTACTS_RESULT_BAND
    const hits: ClickPoint[] = []
    snap.documents.forEach((document, documentIndex) => {
      let offset: { x: number; y: number }
      try {
        offset = accumulateOwnerOffset(snap, documentIndex)
      } catch {
        return
      }
      const valueByNode = new Map(indexedValues(document.nodes.nodeValue, 'nodeValue'))
      document.layout.nodeIndex.forEach((ni, li) => {
        const si = valueByNode.get(ni)
        const t = si !== undefined ? snap.strings[si] : ''
        if (typeof t !== 'string') return
        const trimmed = t.trim()
        if (trimmed.length === 0 || !trimmed.startsWith('_')) return // 只要公司名（"_" 开头）
        const b = document.layout.bounds[li]
        if (!b || b[2]! <= 0 || b[3]! <= 0) return
        const x = offset.x + b[0]! + b[2]! / 2 - (document.scrollOffsetX ?? 0)
        const y = offset.y + b[1]! + b[3]! / 2 - (document.scrollOffsetY ?? 0)
        if (x <= 0 || x >= SEARCH_BOX_MAX_CX || y <= 0 || y > viewport.height) return
        if (y < yMin || y > yMax) return
        hits.push({ x: Math.round(x), y: Math.round(y) })
      })
    })
    return { point: hits.length === 1 ? hits[0]! : null, count: hits.length }
  }

  /** 「联系人」分类标签 y（结果项定位的 y 下限锚点）：全文档可见精确命中取最上方；无命中返回 null */
  private contactsLabelY(snap: DomSnapshot): number | null {
    let best: number | null = null
    snap.strings.forEach((s, stringIndex) => {
      if (s.trim() !== '联系人') return
      snap.documents.forEach((document, documentIndex) => {
        let offset: { x: number; y: number }
        try {
          offset = accumulateOwnerOffset(snap, documentIndex)
        } catch {
          return
        }
        for (const { bounds } of findNodesByString(document, stringIndex)) {
          if (bounds[2] <= 0 || bounds[3] <= 0) continue
          const c = boundsCenter(bounds)
          const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
          if (y <= 0 || y > viewportOf(snap).height) continue
          if (best === null || y < best) best = y
        }
      })
    })
    return best
  }
}
