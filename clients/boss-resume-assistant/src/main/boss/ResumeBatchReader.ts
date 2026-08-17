/**
 * 推荐牛人页批量简历读取执行器（CLI resume-batch 命令 / boss_resume_batch tool，设计 §10.8 延伸）。
 *
 * 链路（模仿 GreetExecutor 的逐个模式）：逐个点击推荐列表的牛人卡片行 → 打开简历详情（canvas）
 * → 复用 ResumeReader 读取管线（Win32 滚轮回顶 → 分段截图 → 拼接 → OCR）→ Escape 关闭 → 下一张。
 *
 * 真机实证（2026-08-17，视口 1249x1277）：
 * - 卡片行结构：每行右侧「打招呼」按钮（x≈1162）视口内 7 个、y 间隔 184px；行左上「姓名 + 活跃状态」
 *   同行（刘草威@342,138 / 刚刚活跃@400,138 / 按钮 y=146）——姓名配对 = 找含「活跃」短文本节点
 *   （trim≤6 字、|y−按钮y|<40、x<按钮x），姓名 = 它左侧最近（同行 y±8、dx<120）的 2-4 字中文节点。
 * - 卡片点击走 CDP 浏览类点击（clickBrowse，点卡片主体列 x=600、按钮 y+70）即有效打开详情；
 *   不需要 Win32（卡片是浏览动作，BOSS 风控不拦 CDP 合成点击；写动作/筛选类仍必须 Win32）。
 *   两次真机验证：按钮 y=146 → 点击 (600,216)、y=330 → (600,400) 均成功打开。
 * - 详情 ~600ms 后大 CANVAS 出现（locateResumeCanvas 命中）；Escape（CDP dispatchKey）关闭后
 *   canvas 消失、可点下一张。
 *
 * 为什么逐张 re-snapshot：打开/关闭详情会触发列表重排，卡片坐标不复用；且已处理的卡按姓名去重
 * （key = name ?? `row@${按钮y}`），同名牛人会被跳过（推荐流很少出现，出现时不重复读同一人）。
 *
 * fail-loud：首屏无卡片抛 ResumeBatchError；打开超时/读取失败记 failures 后继续下一张（尽量多收简历）；
 * 但 Escape 关不掉详情时必须 break——弹层挡住列表没法点下一张，绝不盲点。
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
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'
import {
  ResumeReader,
  extractCandidateNameFromOcr,
  locateResumeCanvas,
  type DeviceRect,
  type ResumeReadResult,
} from './ResumeReader.js'

export class ResumeBatchError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ResumeBatchError'
  }
}

export interface BatchCard {
  /** DOM 配对出的候选人姓名；配对失败为 null（后续用 OCR 首行启发式兜底） */
  name: string | null
  /** 该行「打招呼」按钮的 y（device px，卡片去重 key 与排序用） */
  greetButtonY: number
  /** 卡片主体点击点（device px） */
  clickPoint: ClickPoint
}

export interface BatchResumeResult {
  /** 最终确定的候选人姓名（DOM 配对优先，OCR 启发式兜底） */
  name: string
  readResult: ResumeReadResult
}

export interface ResumeBatchDeps {
  /** 采集 fresh DOMSnapshot（每次点击前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** CDP 浏览类点击（点卡片打开详情；真机实证有效，不占真实鼠标） */
  clickBrowse(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
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
  /** 裁剪 + 重叠对齐 + 垂直拼接（真实实现调 scripts/cv-stitch.ps1） */
  stitch(
    parts: string[],
    rect: DeviceRect,
    outFile: string,
  ): Promise<{ width: number; height: number; overlaps: number[] }>
  /** OCR 识别（真实实现调 scripts/cv-ocr.ps1） */
  ocr(imgFile: string): Promise<string>
  /** 协作式取消信号：每张卡循环顶部检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 每成功读完 1 份简历回调一次（done 为累计成功数） */
  onProgress?(done: number, total: number): void
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

/** 卡片主体安全点击列（device px）。真机验证 x=600 命中卡片主体；「打招呼」按钮在 x≈1162，避开 */
export const CARD_CLICK_X = 600
/** 点击点 = 按钮 y + 70（真机两次验证：146→216、330→400 行均成功打开详情） */
export const CARD_CLICK_DY = 70
/** 点卡片后轮询等详情 canvas 出现的总超时（ms）。真机 ~600ms 出现，轮询容错 */
export const OPEN_POLL_TIMEOUT = 8000
/** 点卡片后轮询间隔（ms） */
export const OPEN_POLL_INTERVAL = 600
/** 打开轮询中每隔多少次重点一次卡片（真机 2026-08-17 实证：首击偶发被吞，重击即开） */
export const OPEN_RECLICK_AFTER = 3
/** 打开轮询中最多点击次数（防风控：不无限重点） */
export const OPEN_CLICK_ATTEMPTS = 3
/** 相邻卡片之间的节奏间隔（ms）：拟人降速，降低风控事件节流风险 */
export const CARD_PACE_DELAY = 2000
/** Escape 后轮询等详情 canvas 消失的总超时（ms） */
export const CLOSE_POLL_TIMEOUT = 5000
/** Escape 后轮询间隔（ms） */
export const CLOSE_POLL_INTERVAL = 500

const GREET_TEXT = '打招呼'
/** 活跃状态文本特征：含「活跃」（OCR/DOM 均稳定），如「刚刚活跃」「今日活跃」 */
const NAME_STATUS_PATTERN = /活跃/
/** 活跃状态文本最大长度（trim 后字数）：真机「刚刚活跃」4 字，≤6 排除混入的长文本 */
const STATUS_MAX_LEN = 6
/** 活跃状态节点与「打招呼」按钮同行的判定带宽（|dy|<40；真机 dy≈8） */
const STATUS_MAX_DY = 40
/** 姓名节点与活跃状态节点同行的 y 容差（真机同行 dy≈0） */
const NAME_SAME_LINE_BAND = 8
/** 姓名节点在活跃状态节点左侧的最大 x 距离（真机 dx≈58） */
const NAME_MAX_DX = 120
/** 姓名文本模式：2-4 字中文（含·），排除 本科/上海/期望 等干扰词以外的杂文本 */
const NAME_PATTERN = /^[\u4e00-\u9fa5·]{2,4}$/

export class ResumeBatchReader {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: ResumeBatchDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 定位视口内的牛人卡片行：GreetExecutor.findGreetButtons 同款范式找「打招呼」按钮（按 y 排序），
   * 每个按钮配对候选人姓名（含「活跃」短文本左侧最近的 2-4 字中文节点），clickPoint = 卡片主体
   * 安全点击列（超视口的丢弃——视口外的卡片 CDP 点不到）。
   */
  locateCards(snap: DomSnapshot): BatchCard[] {
    const viewport = viewportOf(snap)
    const stringIndex = snap.strings.findIndex((s) => s.trim() === GREET_TEXT)
    if (stringIndex < 0) return []
    const buttons: Array<{ point: ClickPoint; documentIndex: number }> = []
    snap.documents.forEach((document, documentIndex) => {
      let offset: { x: number; y: number }
      try {
        offset = accumulateOwnerOffset(snap, documentIndex)
      } catch {
        return // 隐藏 iframe owner 无可见 bounds（后台标签页），跳过
      }
      for (const { bounds } of findNodesByString(document, stringIndex)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        // bounds 是文档绝对坐标，屏幕坐标 = owner 偏移 + bounds 中心 − 文档滚动偏移；只收视口内的
        const c = boundsCenter(bounds)
        const x = offset.x + c.x - (document.scrollOffsetX ?? 0)
        const y = offset.y + c.y - (document.scrollOffsetY ?? 0)
        if (x < 0 || y < 0 || x > viewport.width || y > viewport.height) continue
        buttons.push({ point: { x: Math.round(x), y: Math.round(y) }, documentIndex })
      }
    })
    buttons.sort((a, b) => a.point.y - b.point.y)
    return buttons
      .map((btn) => ({
        name: this.pairCardName(snap, btn, viewport),
        greetButtonY: btn.point.y,
        clickPoint: { x: CARD_CLICK_X, y: btn.point.y + CARD_CLICK_DY },
      }))
      .filter((card) => card.clickPoint.y >= 0 && card.clickPoint.y <= viewport.height)
  }

  /**
   * 姓名配对（真机锚定规则）：按钮同行上方带（|dy|<40、x<按钮x）内找含「活跃」的短文本节点（trim≤6 字），
   * 姓名 = 它左侧最近（同行 y±8、dx<120）的 2-4 字中文节点。配对失败返回 null（OCR 启发式兜底）。
   */
  private pairCardName(
    snap: DomSnapshot,
    btn: { point: ClickPoint; documentIndex: number },
    viewport: { width: number; height: number },
  ): string | null {
    const doc = snap.documents[btn.documentIndex]!
    let offset: { x: number; y: number }
    try {
      offset = accumulateOwnerOffset(snap, btn.documentIndex)
    } catch {
      return null
    }
    // 该 document 内有 bounds 且视口可见的文本节点（屏幕坐标，取中心）
    const texts: Array<{ t: string; x: number; y: number }> = []
    for (const [nodeIndex, valueIndex] of indexedValues(doc.nodes.nodeValue, 'nodeValue')) {
      const t = snap.strings[valueIndex]
      if (typeof t !== 'string' || !t.trim()) continue
      const layoutIndex = doc.layout.nodeIndex.indexOf(nodeIndex)
      if (layoutIndex < 0) continue
      const b = doc.layout.bounds[layoutIndex]!
      if (!b || b[2]! <= 0 || b[3]! <= 0) continue
      const c = boundsCenter([b[0]!, b[1]!, b[2]!, b[3]!])
      const x = offset.x + c.x - (doc.scrollOffsetX ?? 0)
      const y = offset.y + c.y - (doc.scrollOffsetY ?? 0)
      if (x <= 0 || x > viewport.width || y <= 0 || y > viewport.height) continue
      texts.push({ t: t.trim(), x: Math.round(x), y: Math.round(y) })
    }
    // 活跃状态节点：离按钮最近者优先（真机一行一个，防相邻行串扰）
    const statuses = texts
      .filter((n) => n.t.length <= STATUS_MAX_LEN && NAME_STATUS_PATTERN.test(n.t))
      .filter((n) => Math.abs(n.y - btn.point.y) < STATUS_MAX_DY && n.x < btn.point.x)
      .sort((a, b) => Math.abs(a.y - btn.point.y) - Math.abs(b.y - btn.point.y) || a.x - b.x)
    const status = statuses[0]
    if (!status) return null
    // 姓名：状态左侧最近（x 最大）的合格中文节点
    let best: { t: string; x: number } | null = null
    for (const n of texts) {
      if (!NAME_PATTERN.test(n.t)) continue
      if (Math.abs(n.y - status.y) > NAME_SAME_LINE_BAND) continue
      if (!(n.x < status.x) || status.x - n.x >= NAME_MAX_DX) continue
      if (!best || n.x > best.x) best = { t: n.t, x: n.x }
    }
    return best?.t ?? null
  }

  /**
   * 批量读取：逐个点开当前视口牛人卡片 → 复用 ResumeReader 读取 → Escape 关闭 → 下一张。
   * 入口先关闭残留的简历详情弹层（boss_resume_detail 读完不关，详见方法体注释），关不掉 fail-loud。
   * 单张失败（打开超时/读取失败/姓名无法确定）记 failures 后继续；Escape 关不掉详情时 break
   * （弹层挡住列表没法点下一张）。signal 取消抛 CancelledError（已读的份数不返回，由调用方按 CANCELLED 处理）。
   */
  async readBatch(opts: { limit: number; saveDir?: string }): Promise<{
    resumes: BatchResumeResult[]
    failures: Array<{ name: string | null; error: string }>
    attempted: number
  }> {
    const limit = opts.limit
    const resumes: BatchResumeResult[] = []
    const failures: Array<{ name: string | null; error: string }> = []
    const processedNames = new Set<string>()
    let attempted = 0
    let firstRound = true

    // 起点防残留：boss_resume_detail 等操作读完不关详情，若带着已打开的简历详情弹层直接点卡片，
    // 弹层挡住列表且打开轮询会立刻命中「残留 canvas」——把上一个人的简历误记到首张卡片的姓名下（错配入库）。
    // 先 Escape 关闭残留弹层；关不掉 fail-loud，绝不带弹层盲点。
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：批量读取尚未开始')
    if (locateResumeCanvas(await this.deps.snapshot())) {
      const closed = await this.tryCloseDetail()
      if (!closed) {
        throw new ResumeBatchError(
          '启动前检测到已打开的简历详情弹层，且 Escape 后未能关闭：请人工按 Escape 关闭详情后重试',
        )
      }
    }

    for (;;) {
      if (this.deps.signal?.aborted) throw new CancelledError(`已取消：已读取 ${resumes.length} 份简历后中止`)
      // 拟人节奏：相邻卡片间留间隔（含失败重试路径），降低风控对高频合成事件的节流风险
      if (attempted > 0) await this.sleep(CARD_PACE_DELAY)
      const snap = await this.deps.snapshot()
      const viewport = viewportOf(snap)
      const cards = this.locateCards(snap)
      if (firstRound && cards.length === 0) {
        throw new ResumeBatchError(
          '当前视口未找到任何牛人卡片（无「打招呼」按钮）：请确认已打开推荐牛人列表页且列表已加载，必要时先滚动让卡片进入视口',
        )
      }
      firstRound = false
      // key = name ?? `row@${按钮y}`：无名卡按行去重；同名牛人跳过（推荐流极少重复，重复时不重读同一人）。
      // 取卡即标记 key 已处理（含失败路径）——否则失败的卡会被下一轮重复选中，死循环
      const card = cards.find((c) => !processedNames.has(c.name ?? `row@${c.greetButtonY}`))
      if (!card) break
      processedNames.add(card.name ?? `row@${card.greetButtonY}`)
      attempted++

      // 1. 点卡片主体打开详情，轮询等大 canvas 出现（真机 ~600ms）。
      //    首击可能被吞（真机 2026-08-17 实证：偶发单次点击无效果，重击即开）——
      //    整个 OPEN_POLL_TIMEOUT 窗口内每 OPEN_RECLICK_AFTER 次轮询重点一次，最多 CLICK_ATTEMPTS 次
      let rect: DeviceRect | null = null
      const openAttempts = Math.max(1, Math.ceil(OPEN_POLL_TIMEOUT / OPEN_POLL_INTERVAL))
      for (let i = 0; i < openAttempts && !rect; i++) {
        if (i % OPEN_RECLICK_AFTER === 0 && i / OPEN_RECLICK_AFTER < OPEN_CLICK_ATTEMPTS) {
          await this.deps.clickBrowse(card.clickPoint, viewport)
        }
        await this.sleep(OPEN_POLL_INTERVAL)
        if (this.deps.signal?.aborted) throw new CancelledError(`已取消：已读取 ${resumes.length} 份简历后中止`)
        const s = await this.deps.snapshot()
        rect = locateResumeCanvas(s)
      }
      if (!rect) {
        failures.push({ name: card.name, error: '点击卡片后简历详情未打开（未出现简历画布）' })
        continue
      }

      // 2. 复用单份读取管线（滚动点 = canvas 中心，viewport 固定取点卡片前那份）
      const reader = new ResumeReader({
        snapshot: this.deps.snapshot,
        captureFullpage: this.deps.captureFullpage,
        wheel: (deltaY, notches) => this.deps.wheel(rect!, viewport, deltaY, notches),
        stitch: this.deps.stitch,
        ocr: this.deps.ocr,
        signal: this.deps.signal,
        sleep: this.sleep,
      })
      let result: ResumeReadResult
      try {
        result = await reader.readResume({
          saveImageTo: opts.saveDir ? path.join(opts.saveDir, `${card.name ?? '无名'}.png`) : undefined,
        })
      } catch (err) {
        if (err instanceof CancelledError) throw err
        // 读取失败也必须先尝试关闭详情，否则弹层挡住列表没法点下一张
        const closed = await this.tryCloseDetail()
        if (!closed) {
          failures.push({
            name: card.name,
            error: `读取简历失败：${err instanceof Error ? err.message : String(err)}；且 Escape 后简历详情未关闭，无法继续处理后续卡片`,
          })
          break
        }
        failures.push({ name: card.name, error: `读取简历失败：${err instanceof Error ? err.message : String(err)}` })
        continue
      }

      // 3. 姓名：DOM 配对优先，OCR 首行启发式兜底；都无 → 不入 resumes（绝不瞎猜入库）
      const name = card.name || extractCandidateNameFromOcr(result.text) || null
      if (!name) {
        failures.push({ name: null, error: '未能确定候选人姓名（DOM 配对与 OCR 启发式均失败）' })
        const closed = await this.tryCloseDetail()
        if (!closed) {
          failures.push({ name: null, error: 'Escape 后简历详情未关闭，无法继续处理后续卡片' })
          break
        }
        continue
      }

      // 4. 关闭详情；关不掉时当前份仍收进 resumes（内容有效）但必须停止
      const closed = await this.tryCloseDetail()
      resumes.push({ name, readResult: result })
      processedNames.add(name)
      if (!closed) {
        failures.push({ name, error: 'Escape 后简历详情未关闭，无法继续处理后续卡片' })
        break
      }
      this.deps.onProgress?.(resumes.length, limit)
      if (resumes.length >= limit) break
    }
    return { resumes, failures, attempted }
  }

  /** Escape 关闭详情并轮询 canvas 消失；超时未消失返回 false（弹层挡住列表，调用方必须停止） */
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
