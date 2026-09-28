/**
 * 详情页打招呼执行器（boss_greet_detail / boss_open_detail / boss_close_detail 三件套共用，
 * 2026-09-28，docs/plans/desktop-automation/plan-boss-detail-greet.md）。
 *
 * 真机探查结论（2026-09-28 两轮实证，attach 9222 推荐牛人页）：
 * - 详情弹层：canvas（locateResumeCanvas 可命中），canvas 右侧操作列为纯 DOM：
 *   收藏 / 举报 / 不合适 一行，「打招呼」按钮在其正下方；与列表同 document。
 * - 列表页卡片「打招呼」与详情按钮横向区带交叉，纯几何（x/y）区分不可靠
 *   （第一行列表按钮与详情按钮 cy 仅差 13px）。**可靠区分 = DOM 结构**：
 *   用 LCA(收藏,举报,不合适) 得操作列容器，再 isDescendantOf 判定哪个「打招呼」在容器内；
 *   容器外的命中一律视为列表诱饵。
 * - 详情页头部候选人为 DOM 文本（canvas 顶部区）——canvas 顶区精确匹配预期姓名，防打错人。
 * - Escape 关闭详情可靠；点击后按钮原地翻转「继续沟通」（与列表按钮同状态机）。
 *
 * 安全设计（fail-loud，绝不盲点）：
 * - 打招呼前强校验：canvas 存在 + 顶部区姓名==预期 + 操作列容器内按钮状态二选一恰好 1 个；
 *  任一不满足立即抛 DetailGreetError，零点击。
 * - 点击后轮询按钮翻转「继续沟通」，仍可点 → EXECUTION_UNKNOWN 语义（不要重试），绝不重点。
 * - 已是「继续沟通」→ 幂等返回（不点击，正常关闭详情）。
 * - 坐标全部 DOM 锚定动态计算（bounds 中心 + owner 偏移 − 滚动偏移），无绝对像素。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  accumulateOwnerOffset,
  boundsCenter,
  findNodesByString,
  isDescendantOf,
  lowestCommonAncestor,
  parentMapOf,
} from './domSnapshot.js'
import { GREET_TEXT, CONTINUE_TEXT, pairCardNameWithPoint, findButtonsByExactText } from './cardName.js'
import { viewportOf } from './FilterSetter.js'
import { locateResumeCanvas } from './ResumeReader.js'
import { CancelledError } from '../operations/types.js'

export class DetailGreetError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'DetailGreetError'
  }
}

export interface DetailGreetDeps {
  /** 采集 fresh DOMSnapshot（每次定位/校验前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（viewport 为页面截图尺寸，device px）。写动作按钮/点卡片开详情专用 */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** CDP dispatchKey Escape（关闭简历详情弹层） */
  pressEscape(): Promise<void>
  /** CDP mouseWheel 向下滚动（deltaY>0，device px）。open_detail 滚动找人用；缺省时当前屏找不到即 fail-loud */
  scroll?(x: number, y: number, deltaY: number): Promise<void>
  /** 协作式取消信号：各阶段入口/轮询中检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
  /** 性能埋点（可选，operation 层注入 perf.time 同款：step 名 detail:xxx） */
  step?<T>(name: string, fn: () => Promise<T>): Promise<T>
}

/** greet 结果 */
export interface DetailGreetOutcome {
  /** 是否真实点击了「打招呼」（already/dry-run 均为 false） */
  greeted: boolean
  /** 已打过招呼（详情按钮已是「继续沟通」）：未点击，幂等 */
  already?: boolean
  /** dry-run：只定位未点击，详情保持打开 */
  dryRun?: boolean
  /** dry-run 定位到的「打招呼」按钮点击点（视口 device px） */
  point?: ClickPoint
}

/** 操作列容器定位结果（document + 容器节点 + 容器 bounds 供几何回退） */
export interface ActionBar {
  documentIndex: number
  containerIndex: number
  /** 容器节点的文档绝对 bounds；容器无 layout bounds 时为 null */
  containerBounds: [number, number, number, number] | null
}

/** 详情头部姓名校验区高度（device px）：canvas 顶区内精确匹配预期姓名
 *  （真机探查 2026-09-28：候选人姓名 DOM 文本在 canvas 顶部 y≈canvas.y+89） */
const DETAIL_NAME_REGION_HEIGHT = 300

/** 点击「打招呼」后按钮翻转轮询：间隔与轮数（真机：按钮原地翻转「继续沟通」，秒级内完成） */
const FLIP_POLL_INTERVAL = 800
const FLIP_POLL_ROUNDS = 3

/** Escape 后轮询 canvas 消失（与 ResumeBatchReader CLOSE_* 同源，总超时 5s） */
const CLOSE_POLL_INTERVAL = 500
const CLOSE_POLL_TIMEOUT = 5000

/** 点卡片后轮询 canvas 出现（与 ResumeBatchReader OPEN_* 同源，总超时 5s） */
const OPEN_POLL_INTERVAL = 500
const OPEN_POLL_TIMEOUT = 5000
/** 打开轮询中第 N 次同点重击一次卡片（真机 2026-08-17 实证首击偶发被吞、重击即开；只重击一次防风控） */
const OPEN_RECLICK_AT_INDEX = 5

/** open_detail 滚动找人：单屏滚动距离与滚动上限屏数（与 GreetExecutor 定向模式同源） */
const SCROLL_DELTA = 800
const MAX_SCROLL_ROUNDS = 30

/** 付费墙弹层特征文案（与 GreetExecutor 同源：点「打招呼」后弹「该职位无开聊权益」购买弹层） */
const PAYWALL_MARKERS = ['该职位无开聊权益', '商品价格', '扫码支付']

/** 可见文本命中：节点 + 所在文档 + 视口坐标中心（device px）+ 文档绝对 bounds */
interface TextHit {
  documentIndex: number
  nodeIndex: number
  point: ClickPoint
  bounds: [number, number, number, number]
}

/**
 * 全文档找 trim 后精确等于 exact 的可见文本节点（w/h>0 且视口内）。
 * 同文案在 strings 表可能有多个下标（坑修复 2026-08-27），全部遍历。
 */
function findVisibleTexts(snap: DomSnapshot, exact: string): TextHit[] {
  const viewport = viewportOf(snap)
  const hits: TextHit[] = []
  snap.strings.forEach((s, stringIndex) => {
    if (s.trim() !== exact) return
    snap.documents.forEach((document, documentIndex) => {
      let offset: { x: number; y: number }
      try {
        offset = accumulateOwnerOffset(snap, documentIndex)
      } catch {
        return // 隐藏 iframe owner 无可见 bounds（后台标签页），跳过
      }
      for (const { nodeIndex, bounds } of findNodesByString(document, stringIndex)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        // bounds 是文档绝对坐标（滚动后不变），屏幕坐标 = owner 偏移 + bounds 中心 − 文档滚动偏移
        const c = boundsCenter(bounds)
        const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
        const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
        if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) continue
        hits.push({ documentIndex, nodeIndex, point: { x, y }, bounds })
      }
    })
  })
  return hits
}

/** 列表滚动位置：打招呼按钮所在文档的 scrollOffsetY；无按钮时取各文档最大值（列表是唯一滚动文档）。
 *  复制自 GreetExecutor（模块私有函数，语义原样）：boss_open_detail 滚动找人共用同一到底判定，
 *  不改动 GreetExecutor 既有行为。 */
function scrollOffsetOf(snap: DomSnapshot): number {
  const greetIndex = snap.strings.findIndex((s) => s.trim() === GREET_TEXT)
  if (greetIndex >= 0) {
    for (const document of snap.documents) {
      if (findNodesByString(document, greetIndex).length > 0) return document.scrollOffsetY ?? 0
    }
  }
  return Math.max(0, ...snap.documents.map((d) => d.scrollOffsetY ?? 0))
}

/** 当前屏可见候选人姓名清单（open_detail 找不到人时 fail-loud 报错用，帮用户/云端定位现场） */
function visibleNamesOf(snap: DomSnapshot): string {
  const viewport = viewportOf(snap)
  const names = new Set<string>()
  for (const btn of findButtonsByExactText(snap, GREET_TEXT)) {
    const { name } = pairCardNameWithPoint(snap, btn, viewport)
    if (name) names.add(name)
  }
  return names.size > 0 ? [...names].join('、') : '（当前屏无可识别的候选人姓名）'
}

export class DetailGreetExecutor {
  private readonly sleep: (ms: number) => Promise<void>
  private readonly step: <T>(name: string, fn: () => Promise<T>) => Promise<T>

  constructor(private readonly deps: DetailGreetDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
    this.step = deps.step ?? ((_name, fn) => fn())
  }

  /**
   * 定位详情操作列容器：「收藏/举报/不合适」三个文本的可见命中里，同 document ≥2 个 →
   * 该 document 内这些节点的 LCA 只是**图标行容器**；真机 DOM 链实证（2026-09-28）：
   * 「打招呼」按钮挂在图标行的**兄弟子树**（LCA(三图标)=4283，其父 4282 同时包含
   * 4283 与按钮 4306）——直接用 LCA 会把真按钮排除（容器内 0 命中）。故上爬一层到
   * LCA 的父节点作为操作列容器（同时包住图标行与按钮两个子树），并加宽度守卫
   * （操作列是 canvas 右侧窄列，爬过头说明结构异常）。
   * 0 个候选文档 / 多个候选文档 / LCA 计算失败 / 容器宽度越界 → DetailGreetError
   * （fail-loud，绝不盲点）。
   * 静态方法：boss_open_detail 打开校验与 greet 共用同一容器消歧规则。
   */
  static locateActionBar(snap: DomSnapshot): ActionBar {
    const barTexts = ['收藏', '举报', '不合适']
    const byDoc = new Map<number, number[]>()
    for (const text of barTexts) {
      for (const hit of findVisibleTexts(snap, text)) {
        const list = byDoc.get(hit.documentIndex) ?? []
        list.push(hit.nodeIndex)
        byDoc.set(hit.documentIndex, list)
      }
    }
    const candidates = [...byDoc.entries()].filter(([, nodes]) => nodes.length >= 2)
    if (candidates.length === 0) {
      throw new DetailGreetError(
        // 注意措辞：不得出现「未打开」等 POST_WRITE_VERIFY_MARKERS 子串——本错误在 greet 点击前
        // 抛出时必须是前置失败（errorMapping → UI_CHANGED），不能被误判 EXECUTION_UNKNOWN
        // （写动作已发出的假象）；点击后的翻转轮询定位失败由轮询兜底统一按 unknown 报
        '详情操作列未定位到：页面可见文本中「收藏/举报/不合适」同文档命中不足 2 个' +
          '（详情可能尚未渲染完成、已被关闭或页面结构已变化），请人工查看页面',
      )
    }
    if (candidates.length > 1) {
      throw new DetailGreetError(
        `详情操作列未定位到：「收藏/举报/不合适」在 ${candidates.length} 个文档中各有 ≥2 命中，无法唯一确定操作列，请人工查看页面`,
      )
    }
    const [documentIndex, nodes] = candidates[0]!
    const document = snap.documents[documentIndex]!
    const trioLca = lowestCommonAncestor(document, nodes)
    if (trioLca === null) {
      throw new DetailGreetError(
        '详情操作列未定位到：操作列节点最近公共祖先计算失败（快照缺 parentIndex 或节点不在树内），请人工查看页面',
      )
    }
    // 上爬一层：LCA(三图标)=图标行容器，按钮在其兄弟子树（见方法注释真机 DOM 链证据）；
    // 父节点不存在或已是根（0）时保持 LCA 不爬（极端结构，locateGreetState 自会 0 命中 fail-loud）
    let containerIndex = trioLca
    const parentOfLca = parentMapOf(document)?.get(trioLca)
    if (parentOfLca !== undefined && parentOfLca > 0) {
      containerIndex = parentOfLca
    }
    // 容器 bounds（locateGreetState 结构未知时的几何回退用；元素节点通常有 bounds，缺失为 null）
    let containerBounds: [number, number, number, number] | null = null
    const layoutIndex = document.layout.nodeIndex.indexOf(containerIndex)
    if (layoutIndex >= 0) {
      const b = document.layout.bounds[layoutIndex]
      if (b && b.length === 4) containerBounds = [b[0]!, b[1]!, b[2]!, b[3]!]
    }
    // 宽度/高度守卫：操作列是 canvas 右侧窄列（真机 ≈300×150 device px）；上爬后容器接近
    // 整页尺寸说明页面结构不符预期（爬到列表/弹层大容器），fail-loud 拒绝按该容器消歧
    if (containerBounds && (containerBounds[2] > 500 || containerBounds[3] > 600)) {
      throw new DetailGreetError(
        `详情操作列未定位到：容器几何异常（${Math.round(containerBounds[2])}x${Math.round(containerBounds[3])}，` +
          '操作列应为 canvas 右侧窄列），页面结构可能已变化，请人工查看页面',
      )
    }
    return { documentIndex, containerIndex, containerBounds }
  }

  /**
   * 定位操作列容器内的打招呼按钮状态（静态方法，open/greet 校验共用）：
   * 容器内（isDescendantOf===true；null 回退纯几何——中心点落在容器 bounds 内）找
   * 「打招呼」（GREET_TEXT 精确）与「继续沟通」（CONTINUE_TEXT 精确）。**容器外命中一律视为
   * 列表诱饵，不计入**（真机探查：列表按钮与详情按钮纯几何不可区分，必须结构级消歧）。
   * - 恰好 1 个 打招呼 且 0 个 继续沟通 → 可点，返回点击点；
   * - 0 个打招呼且恰好 1 个继续沟通 → {alreadyGreeted:true}；
   * - 其他（多命中/零命中）→ DetailGreetError，绝不盲点。
   */
  static locateGreetState(snap: DomSnapshot, bar: ActionBar): { alreadyGreeted: boolean; point?: ClickPoint } {
    const document = snap.documents[bar.documentIndex]!
    const inside = (hit: TextHit): boolean => {
      const structural = isDescendantOf(document, hit.nodeIndex, bar.containerIndex)
      if (structural !== null) return structural
      // 结构未知（缺 parentIndex 时 LCA 已失败，正常到不了这里；防御性几何回退）：
      // 文档绝对坐标下中心点落在容器 bounds 内视为容器内
      if (!bar.containerBounds) return false
      const c = boundsCenter(hit.bounds)
      const r = bar.containerBounds
      return c.x >= r[0] && c.x <= r[0] + r[2] && c.y >= r[1] && c.y <= r[1] + r[3]
    }
    const greets = findVisibleTexts(snap, GREET_TEXT).filter(
      (h) => h.documentIndex === bar.documentIndex && inside(h),
    )
    const continues = findVisibleTexts(snap, CONTINUE_TEXT).filter(
      (h) => h.documentIndex === bar.documentIndex && inside(h),
    )
    if (greets.length === 1 && continues.length === 0) {
      return { alreadyGreeted: false, point: greets[0]!.point }
    }
    if (greets.length === 0 && continues.length === 1) {
      return { alreadyGreeted: true }
    }
    throw new DetailGreetError(
      `详情操作列内打招呼按钮状态不明确：「打招呼」${greets.length} 个、「继续沟通」${continues.length} 个` +
        '（预期二选一恰好 1 个；操作列容器外的列表按钮不计入），已停止，请人工查看页面',
    )
  }

  /**
   * 校验详情页候选人姓名（静态方法，open/greet 共用）：locateResumeCanvas 必须命中
   * （否则 fail-loud「详情未打开」）；canvas 顶部区（y∈[canvas.y, canvas.y+300]，x∈canvas 列）
   * 内找与 name 精确相等的可见文本节点，必须恰好 1 个；找不到 → 防打错人 fail-loud。
   */
  static verifyDetailName(snap: DomSnapshot, name: string): void {
    const canvas = locateResumeCanvas(snap)
    if (!canvas) {
      throw new DetailGreetError(
        '详情未打开：页面未找到简历详情画布（大尺寸 CANVAS），请先执行 boss_open_detail 打开候选人详情',
      )
    }
    const regionBottom = canvas.y + DETAIL_NAME_REGION_HEIGHT
    const hits = findVisibleTexts(snap, name.trim()).filter(
      (h) =>
        h.point.x >= canvas.x &&
        h.point.x <= canvas.x + canvas.w &&
        h.point.y >= canvas.y &&
        h.point.y <= regionBottom,
    )
    if (hits.length === 1) return
    if (hits.length === 0) {
      throw new DetailGreetError(
        `详情页候选人姓名与预期不符（预期 ${name.trim()}），拒绝打招呼——防打错人：` +
          '详情画布顶部区未找到该姓名文本，请人工确认当前打开的是预期候选人的简历',
      )
    }
    throw new DetailGreetError(
      `详情页候选人姓名校验歧义：画布顶部区命中 ${hits.length} 个「${name.trim()}」（预期恰好 1 个），已停止，请人工查看页面`,
    )
  }

  /**
   * 在当前打开的简历详情页点「打招呼」：
   * snapshot → verifyDetailName（防打错人）→ locateActionBar + locateGreetState（容器消歧）→
   * Win32 click → 轮询按钮翻转「继续沟通」→ Escape 关闭并确认。
   * - alreadyGreeted → {greeted:false, already:true}（不点击，正常关闭，幂等）；
   * - dryRun → {greeted:false, dryRun:true, point}（不点击，详情保持打开）；
   * - 点击后未翻转 → DetailGreetError（unknown，不要重试）；关不掉详情 → DetailGreetError。
   */
  async greet(opts: { name: string; dryRun?: boolean }): Promise<DetailGreetOutcome> {
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：详情页打招呼尚未开始')
    const name = opts.name.trim()
    const snap = await this.deps.snapshot()
    DetailGreetExecutor.verifyDetailName(snap, name)
    const bar = DetailGreetExecutor.locateActionBar(snap)
    const state = DetailGreetExecutor.locateGreetState(snap, bar)
    if (state.alreadyGreeted) {
      // 幂等：按钮已是「继续沟通」，不点击，直接关闭详情
      await this.step('detail:close', () => this.closeDetailAfterGreet())
      return { greeted: false, already: true }
    }
    if (opts.dryRun) {
      return { greeted: false, dryRun: true, point: state.point }
    }
    await this.deps.click(state.point!, viewportOf(snap))
    // 点击后校验：轮询按钮翻转为「继续沟通」（与列表按钮同状态机）
    let flipped = false
    for (let i = 0; i < FLIP_POLL_ROUNDS; i++) {
      if (this.deps.signal?.aborted) {
        throw new CancelledError('已取消：点击「打招呼」后翻转校验阶段中止（点击是否生效未知，请人工查看页面）')
      }
      await this.sleep(FLIP_POLL_INTERVAL)
      const after = await this.step('detail:flip-poll', () => this.deps.snapshot())
      if (PAYWALL_MARKERS.some((m) => after.strings.some((s) => s.includes(m)))) {
        throw new DetailGreetError(
          '打招呼触发付费墙：当前职位无开聊权益（BOSS 弹出购买弹层），已停止。请关闭弹层并切换到有开聊权益的职位后重试',
        )
      }
      try {
        // 点击已发出：单轮定位失败/状态歧义（弹层重绘、按钮过渡态、详情被人工关闭）不定论，
        // 继续轮询；全部轮询完仍无翻转证据 → 统一按 unknown 报（下方 throw），绝不据此重点
        const afterState = DetailGreetExecutor.locateGreetState(after, DetailGreetExecutor.locateActionBar(after))
        if (afterState.alreadyGreeted) {
          flipped = true
          break
        }
      } catch (err) {
        if (!(err instanceof DetailGreetError)) throw err
      }
    }
    if (!flipped) {
      throw new DetailGreetError(
        `点击「打招呼」后按钮未翻转为「继续沟通」（轮询 ${FLIP_POLL_ROUNDS} 次未取得翻转证据）：` +
          '打招呼可能未生效（unknown，不要重试），请人工查看页面',
      )
    }
    await this.step('detail:close', () => this.closeDetailAfterGreet())
    return { greeted: true }
  }

  /**
   * 关闭当前打开的简历详情弹层（boss_close_detail 用，也供筛选流程清理）：
   * 详情本来就没开 → 幂等成功返回 {wasOpen:false}；Escape 后轮询 canvas 消失，关不掉 fail-loud。
   */
  async close(): Promise<{ wasOpen: boolean }> {
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：关闭简历详情尚未开始')
    const snap = await this.deps.snapshot()
    if (!locateResumeCanvas(snap)) return { wasOpen: false }
    const closed = await this.step('detail:close', () => this.tryCloseDetail())
    if (!closed) {
      throw new DetailGreetError('Escape 未能关闭当前打开的简历详情：请人工按 Escape 关闭后重试')
    }
    return { wasOpen: true }
  }

  /**
   * 推荐牛人页按姓名打开候选人简历详情（boss_open_detail 用）：
   * fresh snapshot → findButtonsByExactText + pairCardNameWithPoint 找姓名精确匹配的卡片 →
   * Win32 点姓名节点中心（ResumeBatchReader 同源：BOSS SDK 拦截 CDP 合成点击）→
   * 轮询 locateResumeCanvas 命中（≤5s，中途同点重击一次）→ verifyDetailName 校验==name。
   * 当前屏没有目标时滚动加载继续找（GreetExecutor 定向模式同源：30 屏上限 + 到底判定）；
   * 找不到人 fail-loud 并列出当前屏可见姓名。入口先关闭残留详情弹层（ResumeBatchReader 同源防错配）。
   * 成功后详情保持打开（供 boss_resume_detail 读取），本方法不读取不关闭。
   */
  async openDetail(opts: { name: string; onSearchProgress?: (screens: number) => void }): Promise<{ name: string }> {
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：打开简历详情尚未开始')
    const target = opts.name.trim()
    // 起点防残留（ResumeBatchReader 同源）：带着已打开的详情弹层点卡片，弹层挡住列表且打开
    // 轮询会立刻命中残留 canvas——可能把别人的简历当成目标打开。先 Escape 关闭残留，关不掉 fail-loud
    const initial = await this.deps.snapshot()
    if (locateResumeCanvas(initial)) {
      const closed = await this.step('detail:close-residual', () => this.tryCloseDetail())
      if (!closed) {
        throw new DetailGreetError('启动前检测到已打开的简历详情弹层，且 Escape 后未能关闭：请人工按 Escape 关闭详情后重试')
      }
    }
    let scrollRounds = 0
    for (;;) {
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：打开简历详情阶段中止')
      const snap = await this.deps.snapshot()
      const viewport = viewportOf(snap)
      const buttons = findButtonsByExactText(snap, GREET_TEXT)
      // 当前屏找姓名精确匹配的卡片（cardName 真机锚定配对；配对失败/不同名的按钮一律不点）
      let targetPoint: ClickPoint | null = null
      for (const btn of buttons) {
        const paired = pairCardNameWithPoint(snap, btn, viewport)
        if (paired.name === target && paired.point) {
          targetPoint = paired.point
          break
        }
      }
      if (targetPoint) {
        await this.deps.click(targetPoint, viewport)
        // 轮询 canvas 出现（真机 ~600ms；首击偶发被吞 → 第 5 次轮询时同点重击一次）
        const attempts = Math.max(1, Math.ceil(OPEN_POLL_TIMEOUT / OPEN_POLL_INTERVAL))
        let rect: ReturnType<typeof locateResumeCanvas> = null
        let lastSnap: DomSnapshot | null = null
        for (let i = 0; i < attempts && !rect; i++) {
          if (i === OPEN_RECLICK_AT_INDEX) await this.deps.click(targetPoint, viewport)
          await this.sleep(OPEN_POLL_INTERVAL)
          if (this.deps.signal?.aborted) {
            throw new CancelledError('已取消：等待简历详情打开阶段中止（点击是否生效请人工查看页面）')
          }
          lastSnap = await this.step('detail:open-poll', () => this.deps.snapshot())
          rect = locateResumeCanvas(lastSnap)
        }
        if (!rect || !lastSnap) {
          throw new DetailGreetError(
            `点击「${target}」的卡片后 ${OPEN_POLL_TIMEOUT}ms 内未检出简历画布：` +
              '可能点击被吞或该候选人无在线简历（附件简历型），请人工查看后重试',
          )
        }
        // 打开的详情必须属于目标本人（canvas 顶部区姓名强校验，防打错人）
        DetailGreetExecutor.verifyDetailName(lastSnap, target)
        return { name: target }
      }
      // 当前屏没有目标 → 滚动加载继续找（滚动/到底判定与 GreetExecutor 定向模式同源）
      if (!this.deps.scroll) {
        throw new DetailGreetError(
          `在推荐牛人列表当前屏未找到「${target}」（且未注入滚动能力）：当前屏可见姓名：${visibleNamesOf(snap)}`,
        )
      }
      if (scrollRounds >= MAX_SCROLL_ROUNDS) {
        throw new DetailGreetError(
          `滚动查找 ${MAX_SCROLL_ROUNDS} 屏后仍未找到「${target}」：目标可能不在当前筛选结果中，建议调整筛选或换职位后重试。` +
            `最后可见姓名：${visibleNamesOf(snap)}`,
        )
      }
      for (let attempt = 0; attempt < 2; attempt++) {
        const before = await this.deps.snapshot()
        // 滚动点取实际视口中心（不能硬编码：窗口矮时落点出视口，滚轮不生效会误判到底）
        const vp = viewportOf(before)
        const delta = Math.min(SCROLL_DELTA, Math.floor(vp.height / 2))
        await this.deps.scroll(Math.floor(vp.width / 2), Math.floor(vp.height / 2), delta)
        await this.sleep(attempt === 0 ? 1200 : 1800)
        const after = await this.deps.snapshot()
        if (scrollOffsetOf(before) !== scrollOffsetOf(after)) break
        if (attempt === 1) {
          throw new DetailGreetError(
            `已滚动到列表底部仍未找到「${target}」：当前屏可见姓名：${visibleNamesOf(snap)}；目标可能不在当前筛选结果中，建议调整筛选条件后重试`,
          )
        }
      }
      scrollRounds++
      opts.onSearchProgress?.(scrollRounds)
    }
  }

  /** 打招呼生效后的关闭：失败时报错必须注明打招呼已生效（按钮已翻转），避免被当成打招呼未生效 */
  private async closeDetailAfterGreet(): Promise<void> {
    const closed = await this.tryCloseDetail()
    if (!closed) {
      throw new DetailGreetError(
        '打招呼已生效（按钮已翻转为「继续沟通」），但 Escape 未能关闭简历详情：请人工按 Escape 关闭详情后再继续',
      )
    }
  }

  /** Escape 关闭详情并轮询 canvas 消失（范式同 ResumeBatchReader.tryCloseDetail）；超时未消失返回 false */
  private async tryCloseDetail(): Promise<boolean> {
    await this.deps.pressEscape()
    const attempts = Math.max(1, Math.ceil(CLOSE_POLL_TIMEOUT / CLOSE_POLL_INTERVAL))
    for (let i = 0; i < attempts; i++) {
      await this.sleep(CLOSE_POLL_INTERVAL)
      if (this.deps.signal?.aborted) throw new CancelledError('已取消：关闭简历详情阶段中止')
      const s = await this.deps.snapshot()
      if (!locateResumeCanvas(s)) return true
    }
    return false
  }
}
