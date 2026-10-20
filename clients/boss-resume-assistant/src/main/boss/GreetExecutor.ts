/** Greet only approved cards, using a fresh target for every input.
 * A positive result is the same card becoming “继续沟通”; disappearance/global totals are not proof.
 * Executors record partial per-person results; Runner owns page recovery and any safe continuation.
 */
import { type DomSnapshot, type ClickPoint, findNodesByString } from './domSnapshot.js'
import { GREET_TEXT, CONTINUE_TEXT, pairCardName, findButtonsByExactText, type GreetButtonRef } from './cardName.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'
import { inspectRecommendationResults, type RecommendationStopReason } from './RecommendationResultBoundary.js'
import { candidateButtonVisible, loadedCandidates, hasLoadedRecommendationList, candidateOption, resolveCandidateReference } from './LoadedCandidateNavigation.js'
import { locateResumeCanvas } from './ResumeReader.js'
import { BusinessOperationState } from './BusinessOperationState.js'

export class GreetError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'GreetError'
  }
}

export interface GreetDeps {
  state?: BusinessOperationState
  /** 采集 fresh DOMSnapshot（每次点击前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px） */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 列表滚动距离为 device px（负值向上）；适配层用这份 fresh snapshot 换算 CDP CSS 输入。 */
  scroll?(deltaY: number, snapshot: DomSnapshot): Promise<void>
  /** 协作式取消信号：每轮循环顶部检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 每成功打完 1 人回调一次（done 为累计成功数） */
  onProgress?(done: number): void
  /** 定向模式滚动定位后回调一次（参数为累计滚动次数）。
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
  ambiguousNames?: Array<{ name: string; candidates: Array<ReturnType<typeof candidateOption>> }>
  /** 定向模式滚动查找达到 MAX_SCROLL_ROUNDS 上限而停止（未滚到底）。提示用户目标可能不在当前筛选结果中 */
  stoppedByLimit?: boolean
  stopReason?: RecommendationStopReason
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

export class GreetExecutor {
  private readonly sleep: (ms: number) => Promise<void>
  readonly state = new BusinessOperationState()

  constructor(private readonly deps: GreetDeps) {
    this.state = deps.state ?? this.state
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
  async greetVisible(opts: { limit?: number; names?: string[]; selection?: { name: string; candidate_ref: string } } = {}): Promise<GreetOutcome> {
    const names = opts.names
    if (opts.selection && (names?.length !== 1 || names[0]?.trim() !== opts.selection.name)) throw new GreetError('候选引用必须绑定唯一批准姓名')
    if (names !== undefined && names.length === 0) {
      throw new GreetError('names 不能是空数组：定向打招呼必须提供至少 1 个姓名')
    }
    const limit = opts.limit ?? (names ? names.length : 10)
    // 定向待打名单（trim 后精确匹配）；非定向为 null（走历史路径）
    const pending = names ? new Set(names.map((n) => n.trim())) : null
    const greetedNames: string[] = []
    const ambiguousNames: NonNullable<GreetOutcome['ambiguousNames']> = []
    let greeted = 0
    const finish = (end: boolean, limited = false, reason?: RecommendationStopReason): GreetOutcome =>
      outcome(greeted,end,names,greetedNames,limited,reason,ambiguousNames)
    // 定向模式滚动次数；未加载目标的后续加载搜索仍有总上限。
    let scrollRounds = 0
    let stoppedByLimit = false
    const seekAttempts = new Map<string, number>()
    let seekingName: string | null = null
    let seekingIdentity: string | null = null
    const identity = (c: ReturnType<typeof loadedCandidates>[number]): string => JSON.stringify([c.identityDigest,c.cardInfo])
    for (;;) {
      // 1. 点完当前视口内所有可见按钮（定向时只点姓名 ∈ names 的）
      for (;;) {
        if (this.deps.signal?.aborted) throw new CancelledError(`已取消：成功打招呼 ${greeted} 人后中止`)
        if (pending !== null && pending.size === 0) return finish(false)
        if (greeted >= limit) return finish(false)
        const snap = await this.deps.snapshot()
        if (opts.selection && locateResumeCanvas(snap)) throw new GreetError('选定发送前出现简历详情，请先关闭后重新查询候选卡片')
        const resultState = inspectRecommendationResults(snap)
        if (resultState.stopReason === 'no_matching_candidates') return finish(true,false,greeted === 0 ? resultState.stopReason : 'recommendations_boundary')
        const buttons = this.findGreetButtons(snap)
        const loaded = pending ? loadedCandidates(snap) : []
        const indexedList = pending !== null && hasLoadedRecommendationList(snap)
        const selected = opts.selection ? resolveCandidateReference(snap,opts.selection.name,opts.selection.candidate_ref) : null
        if (opts.selection && !selected) throw new GreetError('候选引用已过期或卡片、账号、职位已变化，已停止；请重新查询候选人，不回退姓名发送')
        if (opts.selection && selected?.buttonText !== GREET_TEXT) throw new GreetError('指定候选人当前没有可用打招呼按钮，已停止，请重新查询状态')
        if (pending) for (const name of pending) {
          const matches = loaded.filter((c) => c.name === name && (!selected || c.identityDigest === selected.identityDigest))
          if (matches.length > 1) {
            ambiguousNames.push({name,candidates:matches.map((c,i)=>candidateOption(c,i+1))})
            pending.delete(name)
            if (seekingName === name) { seekingName = null; seekingIdentity = null }
          } else if (indexedList && matches.length === 0) {
            // A named historical target must be present in the full loaded DOM. Do not scan screens for a typo.
            pending.delete(name)
            if (seekingName === name) { seekingName = null; seekingIdentity = null }
          }
        }
        if (pending && pending.size === 0) return finish(false)
        if (seekingName !== null && !loaded.some((c) => c.name === seekingName && identity(c) === seekingIdentity && !c.excluded && c.buttonText === GREET_TEXT && c.point)) {
          // The same name now refers to a changed card: skip it rather than select a replacement.
          pending!.delete(seekingName); seekingName = null; seekingIdentity = null
          continue
        }
        // Full-DOM identity index precedes the viewport/boundary test: a matching card may be ABOVE a visible fallback boundary.
        const indexedTarget = pending ? loaded.filter((c) => pending.has(c.name)
          && (!selected || c.identityDigest === selected.identityDigest)
          && (!seekingName || (c.name === seekingName && identity(c) === seekingIdentity)) && !c.excluded && c.buttonText === GREET_TEXT && c.point)
          .sort((a, b) => Number(candidateButtonVisible(b)) - Number(candidateButtonVisible(a))
            || Math.abs(a.point!.y - (a.top + a.bottom) / 2) - Math.abs(b.point!.y - (b.top + b.bottom) / 2))[0] : undefined
        if (indexedTarget && !candidateButtonVisible(indexedTarget)) {
          if (!this.deps.scroll) return finish(false)
          const attempts = seekAttempts.get(indexedTarget.name) ?? 0
          if (attempts >= 3 || indexedTarget.bottom <= indexedTarget.top || indexedTarget.point!.x <= indexedTarget.left
            || indexedTarget.point!.x >= indexedTarget.right) throw new GreetError('已加载目标无法进入可操作视窗，已停止，禁止使用旧坐标点击')
          const delta = indexedTarget.point!.y - (indexedTarget.top + indexedTarget.bottom) / 2
          if (!Number.isFinite(delta) || Math.abs(delta) < 1) throw new GreetError('目标滚动距离无效，已停止')
          if (this.deps.signal?.aborted) throw new CancelledError('已取消：目标定位中止')
          await this.deps.scroll(delta, snap)
          seekingName = indexedTarget.name
          seekingIdentity = identity(indexedTarget)
          seekAttempts.set(indexedTarget.name, attempts + 1)
          scrollRounds++
          this.deps.onSearchProgress?.(scrollRounds)
          await this.sleep(1200)
          continue // always re-index and verify before a click; never reuse this point
        }
        if (buttons.length === 0 && !indexedTarget) {
          if (resultState.stopReason) return finish(true,false,resultState.stopReason)
          break
        }

        // 可靠结构优先精确匹配同卡片按钮；旧无结构快照保持原可见按钮配对路径。
        let target: GreetButtonRef | null = indexedTarget ? { point: indexedTarget.point!, documentIndex: indexedTarget.documentIndex } : buttons[0]!
        let targetName: string | null = null
        if (pending !== null) {
          target = indexedTarget ? target : null
          targetName = indexedTarget?.name ?? null
          const viewport = viewportOf(snap)
          for (const btn of indexedList ? [] : buttons) {
            const name = pairCardName(snap, btn, viewport)
            if (name !== null && pending.has(name)) {
              target = btn
              targetName = name
              break
            }
          }
          // 视口内没有目标：退出内层循环去滚动加载继续找
          if (target === null) {
            if (resultState.stopReason) return finish(true,false,resultState.stopReason)
            break
          }
        }

        if (this.deps.signal?.aborted) throw new CancelledError('已取消：打招呼点击前中止')
        const boundTarget = indexedTarget ?? loadedCandidates(snap).find(c => c.point?.x === target!.point.x && c.point?.y === target!.point.y)
        targetName ??= boundTarget?.name ?? pairCardName(snap, target, viewportOf(snap))
        if (!targetName) throw new GreetError('未能绑定待打招呼卡片姓名，未发送招呼')
        await this.state.input(() => this.deps.click(target!.point, viewportOf(snap)), true)
        await this.sleep(1500)

        let after: DomSnapshot
        try { after = await this.deps.snapshot() } catch (error) {
          if (opts.selection) throw new GreetError('点击后无法确认选定候选人发送结果，已停止，请人工核对，不自动重试')
          throw error
        }
        if (PAYWALL_MARKERS.some((m) => after.strings.some((s) => s.includes(m)))) {
          throw new GreetError(
            `第 ${greeted + 1} 个打招呼触发付费墙：当前职位无开聊权益（BOSS 弹出购买弹层），已停止。` +
              '请关闭弹层并切换到有开聊权益的职位后重试',
          )
        }
        // Verify the bound card or uniquely paired original name, never unrelated button totals.
        if (targetName !== null) {
          if (boundTarget) {
            const confirmed = loadedCandidates(after).filter((c) => c.name === targetName && identity(c) === identity(boundTarget))
            if (confirmed.length !== 1 || confirmed[0]!.excluded || confirmed[0]!.buttonText !== CONTINUE_TEXT) {
              throw new GreetError('点击后无法确认原候选人翻转为继续沟通，已停止，请人工确认结果')
            }
          } else {
            // A disappeared greet button alone can be a rerender, occlusion, or a removed card.
            const afterViewport = viewportOf(after)
            const continues = findButtonsByExactText(after, CONTINUE_TEXT).filter(
              (btn) => pairCardName(after, btn, afterViewport) === targetName,
            )
            if (continues.length !== 1) {
              this.state.unconfirmed('原姓名卡片未唯一观察到继续沟通状态；不以按钮消失确认发送')
              throw new GreetError(
                `无法确认第 ${greeted + 1} 个目标「${targetName}」已翻转为继续沟通，发送结果未知`,
              )
            }
          }
        }
        greeted++
        this.state.confirmed({ name: targetName, greeted: true }, '原目标卡片明确翻转为继续沟通')
        if (targetName !== null) {
          greetedNames.push(targetName)
          pending?.delete(targetName)
          seekingName = null
          seekingIdentity = null
        }
        this.deps.onProgress?.(greeted)
      }

      // 2. 视口内点完了，向下滚动加载更多。
      // 到底判定：比较滚动前后列表文档的 scrollOffsetY（不能比 strings——
      // strings 是全量已加载文本表，在已加载内容内滚动时不变，会误判到底）。
      // 滚不动时多试一次：列表底部可能异步加载更多。
      if (!this.deps.scroll) return finish(true)
      if (pending !== null && scrollRounds >= MAX_SCROLL_ROUNDS) {
        // 定向查找滚动上限：再滚下去只会让用户盯着疯滚的鼠标更久（真机事故见 MAX_SCROLL_ROUNDS 注释）
        stoppedByLimit = true
        return finish(false,stoppedByLimit)
      }
      for (let attempt = 0; attempt < 2; attempt++) {
        const before = await this.deps.snapshot()
        // 真机实测实际滚动距离约为 deltaY 的 1.5 倍，且窗口可能只有 1270 高：取 min(800, 视口半高) 保证重叠
        const delta = Math.min(SCROLL_DELTA, Math.floor(viewportOf(before).height / 2))
        if (this.deps.signal?.aborted) throw new CancelledError('已取消：列表加载中止')
        await this.deps.scroll(delta, before)
        await this.sleep(attempt === 0 ? 1200 : 1800)
        const after = await this.deps.snapshot()
        if (scrollOffsetOf(before) !== scrollOffsetOf(after)) break
        if (attempt === 1) return finish(true)
      }
      if (pending !== null) {
        scrollRounds++
        this.deps.onSearchProgress?.(scrollRounds)
      }
    }
  }

  /** 视口内全部「打招呼」按钮，按 y 从上到下排序（含所在文档序号，定向模式配对姓名用） */
  private findGreetButtons(snap: DomSnapshot): GreetButtonRef[] {
    return findButtonsByExactText(snap, GREET_TEXT)
  }
}

/** 统一构造结果：非定向模式保持 {greeted, reachedEnd} 两键；定向模式附 greetedNames/missingNames */
function outcome(
  greeted: number,
  reachedEnd: boolean,
  names: string[] | undefined,
  greetedNames: string[],
  stoppedByLimit = false,
  stopReason?: RecommendationStopReason,
  ambiguousNames: NonNullable<GreetOutcome['ambiguousNames']> = [],
) {
  if (names === undefined) return { greeted, reachedEnd, ...(stopReason ? { stopReason } : {}) }
  const greetedSet = new Set(greetedNames)
  const ambiguousSet = new Set(ambiguousNames.map(c=>c.name))
  const missingNames = [...new Set(names.map((n) => n.trim()))].filter((n) => !greetedSet.has(n) && !ambiguousSet.has(n))
  return { greeted, reachedEnd, greetedNames: [...greetedNames], missingNames, ambiguousNames, stoppedByLimit, ...(stopReason ? { stopReason } : {}) }
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
