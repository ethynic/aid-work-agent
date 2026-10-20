import { DetailGreetExecutor } from './DetailGreetExecutor.js'
import { mapExecutorError } from '../operations/errorMapping.js'
import type { PageExecution } from '../operations/pageOperation.js'
import { WinClickError } from '../input/WinMouseClicker.js'
/**
 * 推荐牛人页批量简历读取执行器（CLI resume-batch 命令 / boss_resume_batch tool，设计 §10.8 延伸）。
 *
 * 链路（模仿 GreetExecutor 的逐个模式）：逐个点击推荐列表的牛人卡片行 → 打开简历详情（canvas）
 * → 复用 ResumeReader 读取管线（Win32 滚轮回顶 → 分段截图 → 拼接）→ Escape 关闭 → 下一张。
 * 文本识别在云端（boss_resume_batch 工具层逐份调多模态模型，2026-09-17 去 OCR 化）。
 *
 * 真机实证（2026-08-17，视口 1249x1277）：
 * - 卡片行结构：每行右侧「打招呼」按钮（x≈1162）视口内 7 个、y 间隔 184px；行左上「姓名 + 活跃状态」
 *   同行（刘草威@342,138 / 刚刚活跃@400,138 / 按钮 y=146）——姓名配对规则抽到共享模块 cardName.ts
 *   （与 GreetExecutor 定向打招呼共用同一套锚定，保证读到的人和打招呼的人是同一个人）。
 * - 卡片点击走 Win32 真实鼠标点击（session.click）：2026-09-11 客户机实证 BOSS 反作弊 SDK
 *   会选择性拦截 CDP 合成点击（同账号 greet 的 Win32 点击一直正常），被拦时点击静默失效；
 *   Win32 为操作系统级真人输入，SDK 无法区分（与写动作同一通道，见 bossContext.ts 说明）。
 * - 详情 ~600ms 后大 CANVAS 出现（locateResumeCanvas 命中）；Escape（CDP dispatchKey）关闭后
 *   canvas 消失、可点下一张。
 *
 * 点击点策略（2026-09-10 客户机错位事故整改）：点击**配对姓名节点的中心点**——坐标跟 DOM 走，
 * 任何分辨率/缩放/窗口尺寸下恒在卡片行内。废除 2026-08-17 按开发机校准的绝对像素主体列点
 * （x=600/按钮 y+70）：客户笔记本布局不同，该点会落进行间空隙（点击无反应）或命中错误行
 * （开错人详情，0.2.9 实证把王亦菲的简历存到了任玮鹤名下）。姓名配对失败的卡片不点击直接
 * 记 failure 跳过——反正不入库，盲点白读还占 ~30 秒真实鼠标滚动。
 *
 * 为什么逐张 re-snapshot：打开/关闭详情会触发列表重排，卡片坐标不复用；且已处理的卡按姓名去重
 * （key = name ?? `row@${按钮y}`），同名牛人会被跳过（推荐流很少出现，出现时不重复读同一人）。
 *
 * 姓名策略（P0 防错名）：卡片 DOM 配对是唯一来源。配对失败 → 该份记 failures 跳过；
 * 姓名与识别文本的交叉校验在云端做（resume_name_matches），云端不命中同样不入库不扣费——
 * 绝不猜名入库（错名简历会导致打招呼打错人，宁跳过不错存）。
 *
 * 页面阻挡返回结构化recovery及已有成果：两次打开仍无完整详情或关闭失败即停止盲点；
 * 单人读取失败但已安全收尾可继续下一份，失败逐项保留。
 */
import path from 'node:path'
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
  indexedValues,
} from './domSnapshot.js'
import { GREET_TEXT, pairCardNameWithPoint, findButtonsByExactText } from './cardName.js'
import { excludedRecommendationName, inspectRecommendationResults } from './RecommendationResultBoundary.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'
import {
  ResumeReader,
  ResumeObstructedError,
  locateResumeCanvas,
  canvasCandidates,
  canvasMinSize,
  type DeviceRect,
  type ResumeReadResult,
} from './ResumeReader.js'

/**
 * 批量诊断日志（stderr）：经 providerManager 的 stderr 转发落 runtime.log。
 * [boss-mcp] 行只有 tool/code/effect/时长（脱敏约束），逐卡失败原因与画布/点击现场只有这里
 * 有——2026-09-10 客户现场「5 张卡片全部失败但日志无线索」事故的整改：不查云端 DB 原始
 * result_json 也能定位。简历正文绝不记；姓名/失败文案/画布尺寸/点击序列坐标为排障证据记录。
 */
function batchLog(msg: string): void {
  process.stderr.write(`[boss-batch] ${msg}\n`)
}

export class ResumeBatchError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ResumeBatchError'
  }
}

export interface BatchCard {
  /** DOM 配对出的候选人姓名（唯一来源）；配对失败为 null → 不点击直接记 failure（不入库，读了白读） */
  name: string | null
  /** 配对姓名节点的中心点（Snapshot bounds 口径，由 Win32 适配器映射屏幕）。坐标跟 DOM 走，任何分辨率/
   *  缩放/窗口尺寸下恒在卡片行内——2026-09-10 客户机错位事故的修复：废除 2026-08-17 按开发机
   *  校准的绝对像素主体列点（x=600/y+70），该点在非参考布局落进行间空隙（点击无反应）或
   *  命中错误行（开错人详情）。name=null 时为 null（该卡不点击）。 */
  namePoint: ClickPoint | null
  /** 该行「打招呼」按钮的 y（device px，卡片去重 key 与排序用） */
  greetButtonY: number
}

export interface BatchResumeResult {
  /** 候选人姓名（卡片 DOM 配对唯一来源；与识别文本的交叉校验在云端做） */
  name: string
  readResult: ResumeReadResult
}

export interface ResumeBatchDeps {
  /** 采集 fresh DOMSnapshot（每次点击前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** Win32 真实鼠标点击（打开卡片详情）。2026-09-11 从 CDP 合成点击（clickBrowse）切换：
   *  客户机实证 BOSS 反作弊 SDK 会选择性拦截 CDP 合成点击（同账号 greet 的 Win32 点击一直
   *  正常），被拦时点击静默失效「页面无反应」；Win32 为操作系统级真人输入，SDK 无法区分。 */
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** CDP dispatchKey Escape（关闭简历详情弹层） */
  pressEscape(): Promise<void>
  /** 无 clip 整页截图（device px，与 DOMSnapshot bounds 同坐标系） */
  captureFullpage(): Promise<Buffer>
  /** Win32 滚轮（真实实现调 scripts/cv-wheel.ps1；deltaY<0 向下） */
  wheel(
    rect: DeviceRect,
    viewport: { width: number; height: number },
    deltaY: number,
    notches: number,
  ): Promise<void>
  /** 像素级同画面确认（真实实现调 scripts/cv-segdiff.ps1）：P1 到底判定加固，透传给内部 ResumeReader */
  sameView(a: string, b: string, rect: DeviceRect): Promise<boolean>
  /** 裁剪 + 重叠对齐 + 垂直拼接（真实实现调 scripts/cv-stitch.ps1）；透传给内部 ResumeReader */
  stitch(
    parts: string[],
    rect: DeviceRect,
    outFile: string,
  ): Promise<{ width: number; height: number; overlaps: number[]; seamMis: number[] }>
  /** 协作式取消信号：每张卡循环顶部检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 每成功读完 1 份简历回调一次（done 为累计成功数） */
  onProgress?(done: number, total: number): void
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
  /** 推荐列表 CDP 滚动，参数为 CSS px；详情读取仍使用独立的 wheel 管线。 */
  scroll?(x: number, y: number, deltaY: number): Promise<void>
}

/** 点卡片后轮询等详情 canvas 出现的总超时（ms）。真机 ~600ms 出现，轮询容错 */
export const OPEN_POLL_TIMEOUT = 8000
/** 点卡片后轮询间隔（ms） */
export const OPEN_POLL_INTERVAL = 600
/** 打开轮询中每隔多少次重点一次卡片（真机 2026-08-17 实证：首击偶发被吞，重击即开） */
export const OPEN_RECLICK_AFTER = 3
/** 打开轮询中最多点击次数（防风控：不无限重点） */
export const OPEN_CLICK_ATTEMPTS = 2
/** 相邻卡片之间的节奏间隔（ms）：拟人降速，降低风控事件节流风险 */
export const CARD_PACE_DELAY = 2000
/** Escape 后轮询等详情 canvas 消失的总超时（ms） */
export const CLOSE_POLL_TIMEOUT = 5000
/** Escape 后轮询间隔（ms） */
export const CLOSE_POLL_INTERVAL = 500

export class ResumeBatchReader {
  private readonly sleep: (ms: number) => Promise<void>
  private execution: PageExecution = { attemptsUsed: 1, ordinaryRetriesUsed: 0, stage: 'prepared',
    inputStatus: 'not_started', verification: { status: 'inconclusive', evidence: [] } }

  /** Terminal current-item facts; prior failed items retain their own execution records. */
  get batchExecution(): PageExecution { return structuredClone(this.execution) }

  private async input<T>(fn: () => Promise<T>): Promise<T> {
    const previous = this.execution.inputStatus
    this.execution.stage = 'input'; this.execution.inputStatus = 'unknown'
    try {
      const value = await fn()
      this.execution.inputStatus = 'attempted'
      return value
    } catch (error) {
      if (error instanceof WinClickError && error.exitCode === 2 && !error.message.includes('[BOSS_INPUT_UNKNOWN]')) this.execution.inputStatus = previous
      throw error
    }
  }

  constructor(private readonly deps: ResumeBatchDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 只桥接续筛列表输入；点击与截图保持原 bounds 口径。不能根据系统 DPI 猜倍率。 */
  private listScrollEvent(snap: DomSnapshot): { x: number; y: number; deltaY: number } {
    const root = snap.documents[0]
    const invalid = (): never => { throw new ResumeBatchError('续筛列表缺少有效的根 HTML CSS 几何：未发送滚动') }
    const validRect = (rect: number[] | undefined): rect is number[] => !!rect && rect.length === 4
      && rect.every(Number.isFinite) && rect[2]! > 0 && rect[3]! > 0
    // viewportOf 已拒绝缺失/非正尺寸；输入前补齐有限性与四元矩形校验。
    if (!validRect(root?.layout.bounds[0])) return invalid()
    const viewport = viewportOf(snap)
    if (!Number.isFinite(viewport.width) || !Number.isFinite(viewport.height)
      || viewport.width <= 0 || viewport.height <= 0) return invalid()
    if (!root?.nodes.nodeName || !root.layout.clientRects) return invalid()
    let htmlNodes: Array<[number, number]>
    try {
      htmlNodes = indexedValues(root.nodes.nodeName, 'nodeName')
        .filter(([, value]) => snap.strings[value] === 'HTML')
    } catch { return invalid() }
    if (htmlNodes.length !== 1) return invalid()
    const index = root.layout.nodeIndex.indexOf(htmlNodes[0]![0])
    const bounds = root.layout.bounds[index]
    const css = root.layout.clientRects[index]
    if (!validRect(bounds) || !validRect(css)
      || Math.abs(bounds[0]!) > 0.01 || Math.abs(bounds[1]!) > 0.01
      || Math.abs(css[0]!) > 0.01 || Math.abs(css[1]!) > 0.01
      || Math.abs(bounds[2]! - viewport.width) > 0.01 || Math.abs(bounds[3]! - viewport.height) > 0.01
      || (root.scrollOffsetX ?? 0) !== 0 || (root.scrollOffsetY ?? 0) !== 0) return invalid()
    const sx = bounds[2]! / css[2]!
    const sy = bounds[3]! / css[3]!
    if (!Number.isFinite(sx) || !Number.isFinite(sy) || sx <= 0 || sy <= 0
      || Math.abs(sx - sy) > Math.max(sx, sy) * 0.001) return invalid()
    return {
      x: (viewport.width / 2) / sx,
      y: (viewport.height / 2) / sy,
      deltaY: Math.min(800, viewport.height / 2) / sy,
    }
  }

  /**
   * 定位视口内的牛人卡片行：GreetExecutor.findGreetButtons 同款范式找「打招呼」按钮（按 y 排序），
   * 每个按钮配对候选人姓名与其节点中心点（姓名点=点击点，坐标跟 DOM 走布局自适应）。
   * 配对失败的按钮也在列（name/namePoint 为 null）：readBatch 会记 failure 跳过，绝不盲点。
   */
  locateCards(snap: DomSnapshot): BatchCard[] {
    const viewport = viewportOf(snap)
    const buttons = findButtonsByExactText(snap, GREET_TEXT)
    return buttons.map((btn) => {
      const paired = pairCardNameWithPoint(snap, btn, viewport)
      return {
        name: paired.name,
        namePoint: paired.point,
        greetButtonY: btn.point.y,
      }
    })
  }

  /**
   * 批量读取：逐个点开当前视口牛人卡片 → 复用 ResumeReader 读取拼接图 → Escape 关闭 → 下一张。
   * 入口先关闭残留的简历详情弹层（boss_resume_detail 读完不关，详见方法体注释），关不掉 fail-loud。
   * 已安全收尾的单人读取失败可继续；打开重试耗尽、直接输入异常或无法收尾则保留成果并返回recovery
   * （弹层挡住列表没法点下一张）。signal 取消抛 CancelledError（已读的份数不返回，由调用方按 CANCELLED 处理）。
   */
  async readBatch(opts: { limit: number; saveDir?: string; excludeNames?: string[]; scanMore?: boolean }): Promise<{
    resumes: BatchResumeResult[]
    failures: Array<{ name: string | null; error: string; execution?: PageExecution }>
    attempted: number
    processed_names: string[]
    stop_reason: string
    recovery?: { code: string; summary: string; execution: PageExecution }
  }> {
    const limit = opts.limit
    const resumes: BatchResumeResult[] = []
    const failures: Array<{ name: string | null; error: string; execution?: PageExecution }> = []
    const processedNames = new Set<string>()
    const excluded = new Set(opts.excludeNames ?? [])
    const anonymousRows = new Set<number>()
    let scrollRounds = 0
    let stopReason = 'viewport_complete'
    let attempted = 0
    let firstRound = true
    let currentName: string | null = null
    let recovery: { code: string; summary: string; execution: PageExecution } | undefined
    this.execution = { attemptsUsed: 1, ordinaryRetriesUsed: 0, stage: 'prepared', inputStatus: 'not_started', verification: { status: 'inconclusive', evidence: [] } }
    const needsRecovery = (summary: string, status: 'unmet' | 'inconclusive', code = 'UI_CHANGED'): void => {
      this.execution.stage = 'postcheck'
      this.execution.verification = { status, evidence: [summary] }
      recovery = { code, summary, execution: structuredClone(this.execution) }
    }
    /** failures.push + 同步落一条诊断日志（logDetail 只进日志不进 error，云端契约文本不变） */
    const pushFailure = (name: string | null, error: string, logDetail?: string): void => {
      failures.push({ name, error, ...(name && this.execution.stage !== 'prepared' ? { execution: structuredClone(this.execution) } : {}) })
      batchLog(`卡片[${name ?? '无名卡'}] 失败：${error}${logDetail ? `（${logDetail}）` : ''}`)
    }

    // 起点防残留：boss_resume_detail 等操作读完不关详情，若带着已打开的简历详情弹层直接点卡片，
    // 弹层挡住列表且打开轮询会立刻命中「残留 canvas」——把上一个人的简历误记到首张卡片的姓名下（错配入库）。
    // 先 Escape 关闭残留弹层；关不掉 fail-loud，绝不带弹层盲点。
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：批量读取尚未开始')
    batchLog(`批量开始 limit=${limit}`)
    try {
    const initial = await this.deps.snapshot()
    const initialState = inspectRecommendationResults(initial)
    if (initialState.stopReason === 'no_matching_candidates') return { resumes, failures, attempted, processed_names: [], stop_reason: initialState.stopReason }
    if (locateResumeCanvas(initial)) {
      const closed = await this.tryCloseDetail()
      if (!closed) {
        throw new ResumeBatchError(
          '启动前检测到已打开的简历详情弹层，且 Escape 后未能关闭：请人工按 Escape 关闭详情后重试',
        )
      }
    }

    batchLoop: for (;;) {
      if (this.deps.signal?.aborted) throw new CancelledError(`已取消：已读取 ${resumes.length} 份简历后中止`)
      if (excluded.size + processedNames.size >= 1000) { stopReason = 'history_limit'; break }
      // 拟人节奏：相邻卡片间留间隔（含失败重试路径），降低风控事件节流风险
      if (attempted > 0) await this.sleep(CARD_PACE_DELAY)
      const snap = await this.deps.snapshot()
      const viewport = viewportOf(snap)
      const cards = this.locateCards(snap)
      const resultState = inspectRecommendationResults(snap)
      if (resultState.stopReason === 'no_matching_candidates') { stopReason = attempted === 0 ? resultState.stopReason : 'recommendations_boundary'; break }
      if (firstRound) {
        // 布局现场（2026-09-10 客户机错位排查）：视口尺寸 + 按钮行距与参考机（184px）的漂移
        // 一眼可见——行距变了，绝对像素兜底点（y+70）就可能出行。
        const ys = cards.map((c) => c.greetButtonY)
        batchLog(
          `视口 ${viewport.width}x${viewport.height}，卡片 ${cards.length} 行，按钮 y=[${ys.join(',')}]` +
            (ys.length > 1 ? `，行距≈${ys[1]! - ys[0]!}px（参考机 184px）` : ''),
        )
      }
      if (firstRound && cards.length === 0 && !opts.scanMore && !resultState.stopReason) {
        throw new ResumeBatchError(
          '当前视口未找到任何牛人卡片（无「打招呼」按钮）：请确认已打开推荐牛人列表页且列表已加载，必要时先滚动让卡片进入视口',
        )
      }
      firstRound = false
      // key = name ?? `row@${按钮y}`：无名卡按行去重；同名牛人跳过（推荐流极少重复，重复时不重读同一人）。
      // 取卡即标记 key 已处理（含失败路径）——否则失败的卡会被下一轮重复选中，死循环
      const card = cards.find((c) => c.name
        ? !processedNames.has(c.name) && !excluded.has(c.name)
        : !anonymousRows.has(c.greetButtonY))
      if (!card) {
        if (resultState.stopReason) { stopReason = resultState.stopReason; break }
        if (!opts.scanMore) break
        if (!this.deps.scroll) throw new ResumeBatchError('续筛缺少列表滚动能力')
        if (scrollRounds >= 30) { stopReason = 'search_limit'; break }
        // 两次延迟确认没有推进才停止；不能把加载迟缓或滚动失败宣称为读完全部。
        const signature = (s: DomSnapshot): string => JSON.stringify([
          this.locateCards(s).map((c) => [c.name, c.greetButtonY]),
          s.documents.map((d) => [d.scrollOffsetX ?? 0, d.scrollOffsetY ?? 0]),
        ])
        let moved = false
        let scrollSnap = snap
        for (let retry = 0; retry < 2; retry++) {
          if (this.deps.signal?.aborted) throw new CancelledError('已取消：列表推进中止')
          const event = this.listScrollEvent(scrollSnap)
          if (retry > 0) { this.execution.ordinaryRetriesUsed = 1; this.execution.attemptsUsed = 2 }
          await this.input(() => this.deps.scroll!(event.x, event.y, event.deltaY))
          await this.sleep(retry === 0 ? 1200 : 1800)
          if (this.deps.signal?.aborted) throw new CancelledError('已取消：列表推进中止')
          scrollSnap = await this.deps.snapshot()
          if (signature(scrollSnap) !== signature(snap)) { moved = true; break }
        }
        if (!moved) { stopReason = 'no_progress'; needsRecovery('列表两次正常滚动后仍未推进，未确认已到末尾', 'unmet'); break }
        scrollRounds++
        anonymousRows.clear()
        continue
      }
      if (card.name) processedNames.add(card.name)
      else anonymousRows.add(card.greetButtonY)

      // 0. 姓名配对前置闸（2026-09-10）：点击点=姓名节点中心，配不出姓名=没有可点点位——
      //    直接记 failure 跳过，绝不盲点（反正不入库，盲点白读还占 ~30 秒真实鼠标滚动）。
      if (!card.name || !card.namePoint) {
        pushFailure(
          card.name,
          '未能确定候选人姓名（卡片 DOM 配对失败）：已跳过不入库；可人工确认姓名后用 boss_resume_detail 显式传名读取',
        )
        continue
      }
      attempted++
      currentName = card.name
      this.execution = { attemptsUsed: 1, ordinaryRetriesUsed: 0, stage: 'prepared', inputStatus: 'not_started', verification: { status: 'inconclusive', evidence: [] } }

      // 1. 点击姓名节点打开详情，轮询等大 canvas 出现（真机 ~600ms）。
      //    首击偶发被吞（真机 2026-08-17 实证重击即开）→ 整个 OPEN_POLL_TIMEOUT 窗口内
      //    最多 OPEN_CLICK_ATTEMPTS 次（防风控：不无限重点）。
      let rect: DeviceRect | null = null
      let lastSnap: DomSnapshot | null = null
      const clickSequence: string[] = []
      const openAttempts = Math.max(1, Math.ceil(OPEN_POLL_TIMEOUT / OPEN_POLL_INTERVAL))
      for (let i = 0; i < openAttempts && !rect; i++) {
        if (i % OPEN_RECLICK_AFTER === 0 && i / OPEN_RECLICK_AFTER < OPEN_CLICK_ATTEMPTS) {
          let pt = card.namePoint
          let clickViewport = viewport
          if (i > 0) {
            let retrySnap = await this.deps.snapshot()
            if (inspectRecommendationResults(retrySnap).stopReason === 'no_matching_candidates' || excludedRecommendationName(retrySnap, card.name)) {
              stopReason = 'recommendations_boundary'; break batchLoop
            }
            lastSnap = retrySnap; rect = DetailGreetExecutor.detailReady(retrySnap) ? locateResumeCanvas(retrySnap) : null
            if (rect) break
            if (locateResumeCanvas(retrySnap)) {
              if (!await this.tryCloseDetail()) {
                stopReason = 'detail_not_closed'; needsRecovery('重试前残留详情未关闭，未点击背景候选人', 'unmet'); break batchLoop
              }
              retrySnap = await this.deps.snapshot()
            }
            const freshCard = this.locateCards(retrySnap).find((candidate) => candidate.name === card.name)
            if (!freshCard?.namePoint) throw new ResumeBatchError('重击前无法重新确认原候选人卡片及来源，已停止，禁止使用旧坐标')
            pt = freshCard.namePoint
            clickViewport = viewportOf(retrySnap)
          }
          if (i > 0) { this.execution.ordinaryRetriesUsed = 1; this.execution.attemptsUsed = 2 }
          clickSequence.push(`(${pt.x},${pt.y})姓名点`)
          await this.input(() => this.deps.click(pt, clickViewport))
        }
        await this.sleep(OPEN_POLL_INTERVAL)
        if (this.deps.signal?.aborted) throw new CancelledError(`已取消：已读取 ${resumes.length} 份简历后中止`)
        const s = await this.deps.snapshot()
        lastSnap = s
        rect = DetailGreetExecutor.detailReady(s) ? locateResumeCanvas(s) : null
      }
      if (!rect) {
        // 画布诊断（只记尺寸不记坐标性内容）：接近阈值的大画布=弹层实际已开只是判定没过；
        // 只有几十像素的图标 canvas=详情真没开（附件简历型候选人常见）。点击序列落日志：
        // 点错位/落空从「点了哪里+页面有什么」直接可判。
        const canvases = lastSnap ? canvasCandidates(lastSnap) : []
        const canvasNote = canvases.length > 0
          ? `页面 CANVAS 尺寸：${canvases.map((c) => `${c.w}x${c.h}`).join('、')}`
          : '页面无 CANVAS 节点'
        const nearMiss = canvases.some((c) => c.w >= 250 && c.h >= 250)
        const { minW, minH } = canvasMinSize(viewport)
        pushFailure(
          card.name,
          '点击卡片后简历详情未打开（未出现简历画布）',
          `点击序列 ${clickSequence.join('→')}；${OPEN_POLL_TIMEOUT}ms 内未检出 >${minW}x${minH} 画布；` +
            `${canvasNote}${nearMiss ? '；存在接近阈值的大画布，疑似详情实际已打开' : ''}`,
        )
        // 详情可能实际已打开但画布判定未命中（真机 2026-08-18：572 高画布被阈值卡掉）——
        // 失败路径也必须尝试关闭，否则弹层挡住列表导致后续卡片连环点空
        const closed = await this.tryCloseDetail()
        stopReason = closed ? 'detail_not_opened' : 'detail_not_closed'
        needsRecovery(closed ? '两次正常点击后仍未检出完整详情结构' : '打开失败且详情收尾未完成', 'unmet')
        break
      }

      if (lastSnap && (inspectRecommendationResults(lastSnap).stopReason === 'no_matching_candidates' || excludedRecommendationName(lastSnap, card.name))) {
        stopReason = 'recommendations_boundary'; break
      }

      // 2. 复用单份读取管线（滚动点 = canvas 中心，viewport 固定取点卡片前那份）
      const readViewport = lastSnap ? viewportOf(lastSnap) : viewport
      const reader = new ResumeReader({
        snapshot: this.deps.snapshot,
        captureFullpage: this.deps.captureFullpage,
        wheel: (deltaY, notches) => this.input(() => this.deps.wheel(rect!, readViewport, deltaY, notches)),
        sameView: (a, b) => this.deps.sameView(a, b, rect!),
        stitch: this.deps.stitch,
        signal: this.deps.signal,
        sleep: this.sleep,
      })
      let result: ResumeReadResult
      try {
        result = await reader.readResume({
          saveImageTo: opts.saveDir ? path.join(opts.saveDir, `${card.name ?? '无名'}.png`) : undefined,
        })
      } catch (err) {
        if (err instanceof CancelledError || err instanceof ResumeObstructedError) throw err
        // 读取失败也必须先尝试关闭详情，否则弹层挡住列表没法点下一张
        const closed = await this.tryCloseDetail()
        const readErrMsg = err instanceof Error ? err.message : String(err)
        if (!closed) {
          stopReason = 'detail_not_closed'
          needsRecovery('读取失败后详情仍未关闭，不能向背景卡片输入', 'unmet')
          pushFailure(
            card.name,
            `读取简历失败：${readErrMsg}；且 Escape 后简历详情未关闭，无法继续处理后续卡片`,
          )
          break
        }
        pushFailure(card.name, `读取简历失败：${readErrMsg}`, `读取管线异常，Escape 关闭成功`)
        continue
      }

      // 3. 关闭详情；关不掉时当前份仍收进 resumes（内容有效）但必须停止。
      //    姓名与识别文本的交叉校验在云端做（resume_name_matches，不入库不扣费）
      const name = card.name
      batchLog(
        `卡片[${name}] 已读取拼接图（${result.segments} 段` +
          `${result.bottomReached ? '，已到底' : '，未到底'}；${result.width}x${result.height}）`,
      )
      resumes.push({ name, readResult: result })
      const closed = await this.tryCloseDetail()
      processedNames.add(name)
      if (!closed) {
        stopReason = 'detail_not_closed'
        needsRecovery('资料已读取，但详情收尾后画布仍存在', 'unmet')
        pushFailure(name, 'Escape 后简历详情未关闭，无法继续处理后续卡片')
        break
      }
      this.deps.onProgress?.(resumes.length, limit)
      if (resumes.length >= limit) { stopReason = 'limit_reached'; break }
    }
    } catch (error) {
      if (error instanceof CancelledError && resumes.length === 0) throw error
      const mapped = mapExecutorError(error)
      pushFailure(currentName, mapped.message)
      stopReason = mapped.code === 'CANCELLED' ? 'cancelled' : 'page_operation_failed'
      recovery = { code: mapped.code, summary: mapped.message, execution: structuredClone(this.execution) }
    }
    batchLog(`批量结束：尝试 ${attempted} 张，成功 ${resumes.length} 份，失败 ${failures.length} 个`)
    return { resumes, failures, attempted, processed_names: [...processedNames], stop_reason: stopReason, ...(recovery ? { recovery } : {}) }
  }

  /** Escape 关闭详情并轮询 canvas 消失；超时未消失返回 false（弹层挡住列表，调用方必须停止） */
  private async tryCloseDetail(): Promise<boolean> {
    for (let retry = 0; retry < 2; retry++) {
      const before = await this.deps.snapshot()
      if (!locateResumeCanvas(before)) return true
      if (retry > 0) {
        if (this.execution.ordinaryRetriesUsed > 0) return false
        this.execution.ordinaryRetriesUsed = 1; this.execution.attemptsUsed = 2
      }
      await this.input(() => this.deps.pressEscape())
      const attempts = Math.max(1, Math.ceil(CLOSE_POLL_TIMEOUT / CLOSE_POLL_INTERVAL))
      for (let i = 0; i < attempts; i++) {
        await this.sleep(CLOSE_POLL_INTERVAL)
        if (this.deps.signal?.aborted) throw new CancelledError('已取消：关闭简历详情阶段中止')
        const s = await this.deps.snapshot()
        if (!locateResumeCanvas(s)) return true
      }
    }
    return false
  }
}
