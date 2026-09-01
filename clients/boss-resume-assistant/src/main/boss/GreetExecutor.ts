/**
 * 打招呼执行器（CLI greet 子命令）。
 *
 * 直接沿用筛选验证过的技术路线：DOMSnapshot 找全部「打招呼」按钮 → Win32 真实鼠标逐个点击。
 * 每点一个重新抓快照重新定位（点完列表会刷新，坐标永不复用）。
 * 当前视口点完后用 CDP mouseWheel 向下滚动加载更多（浏览类操作，不走 Win32），
 * 直到达到 limit 或滚到底（滚动前后列表文档 scrollOffsetY 不变判定到底）。
 *
 * 定向模式（2026-08-18 修复：真机发现只认 limit 不认人，列表顺序与 matched 名单顺序
 * 不保证一致，可能打错人）：传 names 姓名清单时，每轮 snapshot 后对可见按钮用 cardName.ts
 * 的真机锚定规则配对卡片姓名，只点姓名 ∈ names 的按钮；姓名配对失败或不在名单的按钮
 * 一律跳过（宁可不打，不能打错），滚动加载继续找，直到 names 全部完成或滚到底。
 *
 * 安全设计（fail-loud）：
 * - 只点当前视口内可见的按钮（视口外的先滚动再点）
 * - 每次点击后校验成功证据（2026-08-27 真机事故修复：点击高长磊实际成功，但页面同时插入
 *   「为你推荐」区块带来 2 个新「打招呼」按钮，总数 6→7 不降反升，旧「总数必须减少」
 *   校验误报 EXECUTION_UNKNOWN 中止。滚动懒加载/推荐区块插入都会新增按钮，按总数校验
 *   必然误报）：
 *   - 定向模式（传 names）：彻底放弃计数校验，按目标验证——after 快照中任一可见「打招呼」
 *     按钮仍配对出目标姓名 → 点击未生效，立即停止；无按钮配对出目标 → 成功
 *     （新增的无关按钮与验证无关）。
 *   - 非定向模式：计数证据（总数较点击前减少，原规则）| 位置证据（点击位置附近出现
 *     「继续沟通」按钮）任一满足即成功；两者都不满足才报 UNKNOWN 停止。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'
import { GREET_TEXT, CONTINUE_TEXT, pairCardName, type GreetButtonRef } from './cardName.js'
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
  /** 定向模式滚动查找时每滚一屏回调一次（screens 为累计屏数）。
   *  2026-09-01 真机事故：定向找人滚动期间零进度事件，用户只看到鼠标疯滚几分钟
   *  （服务端心跳一直停在上一个 stage），误以为出问题手动关掉 Chrome → CDP 断连 */
  onSearchProgress?(screens: number): void
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

/** greetVisible 结果 */
export interface GreetOutcome {
  /** 成功打招呼人数 */
  greeted: number
  /** 是否滚到底（false 表示被 limit 截断或定向名单全部完成） */
  reachedEnd: boolean
  /** 定向模式（传 names）时返回：实际打过招呼的姓名（按完成顺序） */
  greetedNames?: string[]
  /** 定向模式时返回：names 中滚到底也没找到（或被 limit 截断）的姓名 */
  missingNames?: string[]
  /** 定向模式滚动查找达到 MAX_SCROLL_ROUNDS 上限而停止（未滚到底）。提示用户目标可能不在当前筛选结果中 */
  stoppedByLimit?: boolean
}

/** 单次滚动距离：远小于视口高（1905），保证相邻两屏有重叠不漏人 */
const SCROLL_DELTA = 800

/**
 * 定向模式滚动查找上限（屏）。2026-09-01 真机事故：目标不在当前列表时滚到底要 4 分钟+，
 * 用户全程只看到鼠标疯滚（无进度提示），恐慌关窗导致 CDP 断连。到上限即停止并按
 * missing_names 返回——宁可少打不能打错，也绝不无限滚。
 */
const MAX_SCROLL_ROUNDS = 30

/**
 * 付费墙弹层特征文案（真机 2026-08-05：点击「打招呼」后弹「该职位无开聊权益」购买弹层）。
 * 命中即给出可操作的错误信息，而不是泛泛的「按钮数未减少」。
 */
const PAYWALL_MARKERS = ['该职位无开聊权益', '商品价格', '扫码支付']

/**
 * 非定向模式点击后「位置证据」容差（device px）：after 快照中「继续沟通」按钮 center 与
 * 点击点比较的允许偏差。按钮翻转为「继续沟通」且卡片基本不动，容忍轻微 reflow。
 */
const VERIFY_ROW_DY = 40
const VERIFY_COL_DX = 100

export class GreetExecutor {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: GreetDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 逐个点击「打招呼」，滚到底或达到 limit 结束。
   *
   * - 无 names：只按 limit 数量点击（历史行为，完全不变）。
   * - 有 names：定向模式——只点姓名精确匹配（trim 后相等）names 的按钮；配对失败或不在
   *   名单的按钮一律跳过；视口内无目标时滚动加载继续找，直到 names 全部完成或滚到底。
   *   limit 缺省取 names.length，作为总上限保险（显式传更小值时截断，剩余进 missingNames）。
   * 返回成功数与是否到底；定向模式额外返回 greetedNames / missingNames（谁打了、谁没找到）。
   */
  async greetVisible(opts: { limit?: number; names?: string[] } = {}): Promise<GreetOutcome> {
    const names = opts.names
    if (names !== undefined && names.length === 0) {
      throw new GreetError('names 不能是空数组：定向打招呼必须提供至少 1 个姓名')
    }
    const limit = opts.limit ?? (names ? names.length : 10)
    // 定向待打名单（trim 后精确匹配）；非定向为 null（走历史路径）
    const pending = names ? new Set(names.map((n) => n.trim())) : null
    const greetedNames: string[] = []
    let greeted = 0
    // 定向模式滚动查找计数（屏）；到 MAX_SCROLL_ROUNDS 停止（防无限滚）
    let scrollRounds = 0
    let stoppedByLimit = false
    for (;;) {
      // 1. 点完当前视口内所有可见按钮（定向时只点姓名 ∈ names 的）
      for (;;) {
        if (this.deps.signal?.aborted) throw new CancelledError(`已取消：成功打招呼 ${greeted} 人后中止`)
        if (pending !== null && pending.size === 0) return outcome(greeted, false, names, greetedNames)
        if (greeted >= limit) return outcome(greeted, false, names, greetedNames)
        const snap = await this.deps.snapshot()
        const buttons = this.findGreetButtons(snap)
        if (buttons.length === 0) break

        // 定向模式：从上往下找第一个「配对姓名 ∈ 名单」的按钮；配对失败/不在名单的按钮跳过不点
        let target: GreetButtonRef | null = buttons[0]!
        let targetName: string | null = null
        if (pending !== null) {
          target = null
          const viewport = viewportOf(snap)
          for (const btn of buttons) {
            const name = pairCardName(snap, btn, viewport)
            if (name !== null && pending.has(name)) {
              target = btn
              targetName = name
              break
            }
          }
          // 视口内没有目标：退出内层循环去滚动加载继续找
          if (target === null) break
        }

        await this.deps.click(target.point, viewportOf(snap))
        await this.sleep(1500)

        const after = await this.deps.snapshot()
        if (PAYWALL_MARKERS.some((m) => after.strings.some((s) => s.includes(m)))) {
          throw new GreetError(
            `第 ${greeted + 1} 个打招呼触发付费墙：当前职位无开聊权益（BOSS 弹出购买弹层），已停止。` +
              '请关闭弹层并切换到有开聊权益的职位后重试',
          )
        }
        // 点击后校验（2026-08-27 真机事故修复）：滚动懒加载/「为你推荐」区块插入都会新增按钮，
        // 「全局总数必须减少」必然误报。定向模式按目标验证，非定向放宽为「位置证据 | 计数证据」任一。
        if (targetName !== null) {
          // 定向模式：彻底放弃计数校验——只要没有任何可见「打招呼」按钮再配对出目标姓名即成功，
          // 推荐区块/懒加载新增的无关按钮与验证无关
          const afterViewport = viewportOf(after)
          const targetStillVisible = this.findGreetButtons(after).some(
            (btn) => pairCardName(after, btn, afterViewport) === targetName,
          )
          if (targetStillVisible) {
            throw new GreetError(
              `第 ${greeted + 1} 个打招呼点击后目标「${targetName}」的按钮仍在页面上：` +
                '可能出现确认弹层或点击被拦截（若「为你推荐」区块出现同名卡片也会触发此判定），已停止，请人工查看页面',
            )
          }
        } else {
          // 非定向模式：计数证据（原规则，保留）或位置证据（点击点附近出现「继续沟通」）任一满足即成功
          const remaining = this.findGreetButtons(after).length
          const flippedNearClick = this.findButtonsByExactText(after, CONTINUE_TEXT).some(
            (b) =>
              Math.abs(b.point.y - target.point.y) <= VERIFY_ROW_DY &&
              Math.abs(b.point.x - target.point.x) <= VERIFY_COL_DX,
          )
          if (remaining >= buttons.length && !flippedNearClick) {
            throw new GreetError(
              `第 ${greeted + 1} 个打招呼点击后按钮数未减少（${buttons.length}→${remaining}）且点击位置附近未翻转为「继续沟通」：` +
                '可能出现确认弹层或点击被拦截，已停止，请人工查看页面' +
                '（列表若同时插入「为你推荐」区块或懒加载新卡，总数校验不可靠，已同时校验点击位置翻转证据）',
            )
          }
        }
        greeted++
        if (targetName !== null) {
          greetedNames.push(targetName)
          pending!.delete(targetName)
        }
        this.deps.onProgress?.(greeted)
      }

      // 2. 视口内点完了，向下滚动加载更多。
      // 到底判定：比较滚动前后列表文档的 scrollOffsetY（不能比 strings——
      // strings 是全量已加载文本表，在已加载内容内滚动时不变，会误判到底）。
      // 滚不动时多试一次：列表底部可能异步加载更多。
      if (!this.deps.scroll) return outcome(greeted, true, names, greetedNames)
      if (pending !== null && scrollRounds >= MAX_SCROLL_ROUNDS) {
        // 定向查找滚动上限：再滚下去只会让用户盯着疯滚的鼠标更久（真机事故见 MAX_SCROLL_ROUNDS 注释）
        stoppedByLimit = true
        return outcome(greeted, false, names, greetedNames, stoppedByLimit)
      }
      for (let attempt = 0; attempt < 2; attempt++) {
        const before = await this.deps.snapshot()
        // 真机实测实际滚动距离约为 deltaY 的 1.5 倍，且窗口可能只有 1270 高：取 min(800, 视口半高) 保证重叠
        const delta = Math.min(SCROLL_DELTA, Math.floor(viewportOf(before).height / 2))
        await this.deps.scroll(delta)
        await this.sleep(attempt === 0 ? 1200 : 1800)
        const after = await this.deps.snapshot()
        if (scrollOffsetOf(before) !== scrollOffsetOf(after)) break
        if (attempt === 1) return outcome(greeted, true, names, greetedNames)
      }
      if (pending !== null) {
        scrollRounds++
        this.deps.onSearchProgress?.(scrollRounds)
      }
    }
  }

  /** 视口内全部「打招呼」按钮，按 y 从上到下排序（含所在文档序号，定向模式配对姓名用） */
  private findGreetButtons(snap: DomSnapshot): GreetButtonRef[] {
    return this.findButtonsByExactText(snap, GREET_TEXT)
  }

  /**
   * 视口内全部指定文案按钮（trim 后精确相等），按 y 从上到下排序（含所在文档序号，配对姓名用）。
   * 坑修复（2026-08-27）：原实现 findIndex 只取第一个匹配的 string 下标——strings 表中同文案
   * 可出现多个下标（不同节点分别 intern），只认第一个会漏掉其余按钮；现遍历全部下标
   * （与 ChatSendExecutor.locateSendButton 同款 forEach 写法）。
   */
  private findButtonsByExactText(snap: DomSnapshot, text: string): GreetButtonRef[] {
    const viewport = viewportOf(snap)
    const points: GreetButtonRef[] = []
    snap.strings.forEach((s, stringIndex) => {
      if (s.trim() !== text) return
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
          points.push({ point: { x, y }, documentIndex })
        }
      })
    })
    return points.sort((a, b) => a.point.y - b.point.y)
  }
}

/** 统一构造结果：非定向模式保持 {greeted, reachedEnd} 两键；定向模式附 greetedNames/missingNames */
function outcome(
  greeted: number,
  reachedEnd: boolean,
  names: string[] | undefined,
  greetedNames: string[],
  stoppedByLimit = false,
) {
  if (names === undefined) return { greeted, reachedEnd }
  const greetedSet = new Set(greetedNames)
  const missingNames = [...new Set(names.map((n) => n.trim()))].filter((n) => !greetedSet.has(n))
  return { greeted, reachedEnd, greetedNames: [...greetedNames], missingNames, stoppedByLimit }
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
