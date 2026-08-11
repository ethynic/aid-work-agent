/**
 * 打招呼执行器（CLI greet 子命令）。
 *
 * 直接沿用筛选验证过的技术路线：DOMSnapshot 找全部「打招呼」按钮 → Win32 真实鼠标逐个点击。
 * 每点一个重新抓快照重新定位（点完列表会刷新，坐标永不复用）。
 * 当前视口点完后用 CDP mouseWheel 向下滚动加载更多（浏览类操作，不走 Win32），
 * 直到达到 limit 或滚到底（滚动前后列表文档 scrollOffsetY 不变判定到底）。
 *
 * 安全设计（fail-loud）：
 * - 只点当前视口内可见的按钮（视口外的先滚动再点）
 * - 每次点击后校验「打招呼」按钮总数必须减少（成功会变成「继续沟通」）；
 *   未减少说明可能弹了确认层/被风控拦截，立即停止并提示人工查看，绝不盲点下一个
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'

export class GreetError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'GreetError'
  }
}

export interface GreetDeps {
  /** 采集 fresh DOMSnapshot（每次点击前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** CDP mouseWheel 向下滚动（deltaY>0，device px）；缺省时无可滚动，视口点完即结束 */
  scroll?(deltaY: number): Promise<void>
  /** 协作式取消信号：每轮循环顶部检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 每成功打完 1 人回调一次（done 为累计成功数） */
  onProgress?(done: number): void
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

const GREET_TEXT = '打招呼'
/** 单次滚动距离：远小于视口高（1905），保证相邻两屏有重叠不漏人 */
const SCROLL_DELTA = 800

/**
 * 付费墙弹层特征文案（真机 2026-08-05：点击「打招呼」后弹「该职位无开聊权益」购买弹层）。
 * 命中即给出可操作的错误信息，而不是泛泛的「按钮数未减少」。
 */
const PAYWALL_MARKERS = ['该职位无开聊权益', '商品价格', '扫码支付']

export class GreetExecutor {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: GreetDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 逐个点击「打招呼」，滚到底或达到 limit 结束。
   * 返回成功数与是否到底（reachedEnd=false 表示被 limit 截断）。
   */
  async greetVisible(opts: { limit?: number } = {}): Promise<{ greeted: number; reachedEnd: boolean }> {
    const limit = opts.limit ?? 10
    let greeted = 0
    for (;;) {
      // 1. 点完当前视口内所有可见按钮
      for (;;) {
        if (this.deps.signal?.aborted) throw new CancelledError(`已取消：成功打招呼 ${greeted} 人后中止`)
        if (greeted >= limit) return { greeted, reachedEnd: false }
        const snap = await this.deps.snapshot()
        const buttons = this.findGreetButtons(snap)
        if (buttons.length === 0) break

        await this.deps.click(buttons[0]!, viewportOf(snap))
        await this.sleep(1500)

        const after = await this.deps.snapshot()
        if (PAYWALL_MARKERS.some((m) => after.strings.some((s) => s.includes(m)))) {
          throw new GreetError(
            `第 ${greeted + 1} 个打招呼触发付费墙：当前职位无开聊权益（BOSS 弹出购买弹层），已停止。` +
              '请关闭弹层并切换到有开聊权益的职位后重试',
          )
        }
        const remaining = this.findGreetButtons(after).length
        if (remaining >= buttons.length) {
          throw new GreetError(
            `第 ${greeted + 1} 个打招呼点击后按钮数未减少（${buttons.length}→${remaining}）：` +
              '可能出现确认弹层或点击被拦截，已停止，请人工查看页面',
          )
        }
        greeted++
        this.deps.onProgress?.(greeted)
      }

      // 2. 视口内点完了，向下滚动加载更多。
      // 到底判定：比较滚动前后列表文档的 scrollOffsetY（不能比 strings——
      // strings 是全量已加载文本表，在已加载内容内滚动时不变，会误判到底）。
      // 滚不动时多试一次：列表底部可能异步加载更多。
      if (!this.deps.scroll) return { greeted, reachedEnd: true }
      for (let attempt = 0; attempt < 2; attempt++) {
        const before = await this.deps.snapshot()
        // 真机实测实际滚动距离约为 deltaY 的 1.5 倍，且窗口可能只有 1270 高：取 min(800, 视口半高) 保证重叠
        const delta = Math.min(SCROLL_DELTA, Math.floor(viewportOf(before).height / 2))
        await this.deps.scroll(delta)
        await this.sleep(attempt === 0 ? 1200 : 1800)
        const after = await this.deps.snapshot()
        if (scrollOffsetOf(before) !== scrollOffsetOf(after)) break
        if (attempt === 1) return { greeted, reachedEnd: true }
      }
    }
  }

  /** 视口内全部「打招呼」按钮，按 y 从上到下排序 */
  private findGreetButtons(snap: DomSnapshot): ClickPoint[] {
    const viewport = viewportOf(snap)
    const stringIndex = snap.strings.findIndex((s) => s.trim() === GREET_TEXT)
    if (stringIndex < 0) return []
    const points: ClickPoint[] = []
    snap.documents.forEach((document, documentIndex) => {
      for (const { bounds } of findNodesByString(document, stringIndex)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        // bounds 是文档绝对坐标（不随滚动变化，真机实测：滚动后 bounds 不动、scrollOffsetY 变），
        // 屏幕坐标 = owner 偏移 + bounds - 文档滚动偏移；只收视口内的：视口外的按钮 Win32 点不到
        const offset = accumulateOwnerOffset(snap, documentIndex)
        const c = boundsCenter(bounds)
        const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
        const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
        if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) continue
        points.push({ x, y })
      }
    })
    return points.sort((a, b) => a.y - b.y)
  }
}

/** 列表滚动位置：打招呼按钮所在文档的 scrollOffsetY；无按钮时取各文档最大值（列表是唯一滚动文档） */
function scrollOffsetOf(snap: DomSnapshot): number {
  const greetIndex = snap.strings.findIndex((s) => s.trim() === GREET_TEXT)
  if (greetIndex >= 0) {
    for (const document of snap.documents) {
      if (findNodesByString(document, greetIndex).length > 0) return document.scrollOffsetY ?? 0
    }
  }
  return Math.max(0, ...snap.documents.map((d) => d.scrollOffsetY ?? 0))
}
