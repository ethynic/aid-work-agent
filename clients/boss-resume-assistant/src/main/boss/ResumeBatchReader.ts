/**
 * 推荐牛人页批量简历读取执行器（CLI resume-batch 命令 / boss_resume_batch tool，设计 §10.8 延伸）。
 *
 * 链路（模仿 GreetExecutor 的逐个模式）：逐个点击推荐列表的牛人卡片行 → 打开简历详情（canvas）
 * → 复用 ResumeReader 读取管线（Win32 滚轮回顶 → 分段截图 → 拼接 → OCR）→ Escape 关闭 → 下一张。
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
 * 记 failure 跳过——P0 反正不入库，盲点白读还占 ~30 秒真实鼠标滚动。
 *
 * 为什么逐张 re-snapshot：打开/关闭详情会触发列表重排，卡片坐标不复用；且已处理的卡按姓名去重
 * （key = name ?? `row@${按钮y}`），同名牛人会被跳过（推荐流很少出现，出现时不重复读同一人）。
 *
 * 姓名策略（P0 防错名）：卡片 DOM 配对是唯一来源 + OCR 文本头部交叉校验（ocrNameMatches，容忍
 * 1 字 OCR 误差）。配对失败或交叉校验不过（疑似点开详情与卡片不符）→ 该份记 failures 跳过，
 * 绝不 OCR 猜名入库（错名简历会导致打招呼打错人，宁跳过不错存）。
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
} from './domSnapshot.js'
import { GREET_TEXT, pairCardNameWithPoint } from './cardName.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'
import {
  ResumeReader,
  ocrNameMatches,
  locateResumeCanvas,
  canvasCandidates,
  canvasMinSize,
  type DeviceRect,
  type OcrEngine,
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
  /** DOM 配对出的候选人姓名（唯一来源）；配对失败为 null → 不点击直接记 failure（P0 不入库，读了白读） */
  name: string | null
  /** 配对姓名节点的中心点（视口 CSS px，与 CDP 点击同坐标系）。坐标跟 DOM 走，任何分辨率/
   *  缩放/窗口尺寸下恒在卡片行内——2026-09-10 客户机错位事故的修复：废除 2026-08-17 按开发机
   *  校准的绝对像素主体列点（x=600/y+70），该点在非参考布局落进行间空隙（点击无反应）或
   *  命中错误行（开错人详情）。name=null 时为 null（该卡不点击）。 */
  namePoint: ClickPoint | null
  /** 该行「打招呼」按钮的 y（device px，卡片去重 key 与排序用） */
  greetButtonY: number
}

export interface BatchResumeResult {
  /** 候选人姓名（卡片 DOM 配对唯一来源 + 已通过 OCR 文本头部交叉校验，见 ocrNameMatches） */
  name: string
  readResult: ResumeReadResult
}

export interface ResumeBatchDeps {
  /** 采集 fresh DOMSnapshot（每次点击前重新采集，禁止复用旧坐标） */
  snapshot(): Promise<DomSnapshot>
  /** CDP 浏览类点击（点卡片打开详情；真机实证有效，不占真实鼠标） */
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
  /** 裁剪 + 重叠对齐 + 垂直拼接（真实实现调 scripts/cv-stitch.ps1）；cropDir 逐段落盘供逐段 OCR */
  stitch(
    parts: string[],
    rect: DeviceRect,
    outFile: string,
    cropDir?: string,
  ): Promise<{ width: number; height: number; overlaps: number[]; seamMis: number[] }>
  /** 批量 OCR（真实实现 = operations/bossResumeDetail.ts 的 ocrBatch：RapidOCR 主 + WinRT 兜底）；
   *  透传给内部 ResumeReader（P2 起一次调用处理一份简历的全部段） */
  ocrBatch(files: string[]): Promise<{ texts: string[]; engine: OcrEngine }>
  /** 协作式取消信号：每张卡循环顶部检查，触发即抛 CancelledError */
  signal?: AbortSignal
  /** 每成功读完 1 份简历回调一次（done 为累计成功数） */
  onProgress?(done: number, total: number): void
  /** 可注入 sleep（测试） */
  sleep?(ms: number): Promise<void>
}

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

export class ResumeBatchReader {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: ResumeBatchDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 定位视口内的牛人卡片行：GreetExecutor.findGreetButtons 同款范式找「打招呼」按钮（按 y 排序），
   * 每个按钮配对候选人姓名与其节点中心点（姓名点=点击点，坐标跟 DOM 走布局自适应）。
   * 配对失败的按钮也在列（name/namePoint 为 null）：readBatch 会记 failure 跳过，绝不盲点。
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
   * 批量读取：逐个点开当前视口牛人卡片 → 复用 ResumeReader 读取 → Escape 关闭 → 下一张。
   * 入口先关闭残留的简历详情弹层（boss_resume_detail 读完不关，详见方法体注释），关不掉 fail-loud。
   * 单张失败（打开超时/读取失败/姓名无法确定/姓名交叉校验不过）记 failures 后继续；Escape 关不掉详情时 break
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
    /** failures.push + 同步落一条诊断日志（logDetail 只进日志不进 error，云端契约文本不变） */
    const pushFailure = (name: string | null, error: string, logDetail?: string): void => {
      failures.push({ name, error })
      batchLog(`卡片[${name ?? '无名卡'}] 失败：${error}${logDetail ? `（${logDetail}）` : ''}`)
    }

    // 起点防残留：boss_resume_detail 等操作读完不关详情，若带着已打开的简历详情弹层直接点卡片，
    // 弹层挡住列表且打开轮询会立刻命中「残留 canvas」——把上一个人的简历误记到首张卡片的姓名下（错配入库）。
    // 先 Escape 关闭残留弹层；关不掉 fail-loud，绝不带弹层盲点。
    if (this.deps.signal?.aborted) throw new CancelledError('已取消：批量读取尚未开始')
    batchLog(`批量开始 limit=${limit}`)
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
      // 拟人节奏：相邻卡片间留间隔（含失败重试路径），降低风控事件节流风险
      if (attempted > 0) await this.sleep(CARD_PACE_DELAY)
      const snap = await this.deps.snapshot()
      const viewport = viewportOf(snap)
      const cards = this.locateCards(snap)
      if (firstRound) {
        // 布局现场（2026-09-10 客户机错位排查）：视口尺寸 + 按钮行距与参考机（184px）的漂移
        // 一眼可见——行距变了，绝对像素兜底点（y+70）就可能出行。
        const ys = cards.map((c) => c.greetButtonY)
        batchLog(
          `视口 ${viewport.width}x${viewport.height}，卡片 ${cards.length} 行，按钮 y=[${ys.join(',')}]` +
            (ys.length > 1 ? `，行距≈${ys[1]! - ys[0]!}px（参考机 184px）` : ''),
        )
      }
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

      // 0. 姓名配对前置闸（2026-09-10）：点击点=姓名节点中心，配不出姓名=没有可点点位——
      //    直接记 failure 跳过，绝不盲点（P0 反正不入库，盲点白读还占 ~30 秒真实鼠标滚动）。
      if (!card.name || !card.namePoint) {
        pushFailure(
          card.name,
          '未能确定候选人姓名（卡片 DOM 配对失败）：已跳过不入库；可人工确认姓名后用 boss_resume_detail 显式传名读取',
        )
        continue
      }
      attempted++

      // 1. 点击姓名节点打开详情，轮询等大 canvas 出现（真机 ~600ms）。
      //    首击偶发被吞（真机 2026-08-17 实证重击即开）→ 整个 OPEN_POLL_TIMEOUT 窗口内
      //    最多 OPEN_CLICK_ATTEMPTS 次（防风控：不无限重点）。
      let rect: DeviceRect | null = null
      let lastSnap: DomSnapshot | null = null
      const clickSequence: string[] = []
      const openAttempts = Math.max(1, Math.ceil(OPEN_POLL_TIMEOUT / OPEN_POLL_INTERVAL))
      for (let i = 0; i < openAttempts && !rect; i++) {
        if (i % OPEN_RECLICK_AFTER === 0 && i / OPEN_RECLICK_AFTER < OPEN_CLICK_ATTEMPTS) {
          const pt = card.namePoint
          clickSequence.push(`(${pt.x},${pt.y})姓名点`)
          await this.deps.click(pt, viewport)
        }
        await this.sleep(OPEN_POLL_INTERVAL)
        if (this.deps.signal?.aborted) throw new CancelledError(`已取消：已读取 ${resumes.length} 份简历后中止`)
        const s = await this.deps.snapshot()
        lastSnap = s
        rect = locateResumeCanvas(s)
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
        await this.tryCloseDetail().catch(() => false)
        continue
      }

      // 2. 复用单份读取管线（滚动点 = canvas 中心，viewport 固定取点卡片前那份）
      const reader = new ResumeReader({
        snapshot: this.deps.snapshot,
        captureFullpage: this.deps.captureFullpage,
        wheel: (deltaY, notches) => this.deps.wheel(rect!, viewport, deltaY, notches),
        sameView: (a, b) => this.deps.sameView(a, b, rect!),
        stitch: this.deps.stitch,
        ocrBatch: this.deps.ocrBatch,
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
        const readErrMsg = err instanceof Error ? err.message : String(err)
        if (!closed) {
          pushFailure(
            card.name,
            `读取简历失败：${readErrMsg}；且 Escape 后简历详情未关闭，无法继续处理后续卡片`,
          )
          break
        }
        pushFailure(card.name, `读取简历失败：${readErrMsg}`, `引擎判定前的读取管线异常，Escape 关闭成功`)
        continue
      }

      // 3. 张冠李戴防护交叉校验：卡片姓名必须在 OCR 文本头部模糊命中（容忍 1 字 OCR 误差）。
      //    未命中 = 疑似点开的详情与卡片不符（弹层残留/点击错位）→ 该份记 failure 跳过，宁跳过不错存
      if (!ocrNameMatches(card.name, result.text)) {
        pushFailure(
          card.name,
          `姓名交叉校验未通过（卡片配对「${card.name}」未在简历 OCR 文本头部命中，疑似点开详情与卡片不符）：已跳过不入库`,
          `OCR 头部未见姓名（ocr_chars=${result.chars}，引擎=${result.ocrEngine}` +
            `${result.ocrEngine === 'winrt' ? '；winrt 识别质量低于 RapidOCR，需怀疑识别差或点开的是他人详情' : ''}）`,
        )
        const closed = await this.tryCloseDetail()
        if (!closed) {
          pushFailure(card.name, 'Escape 后简历详情未关闭，无法继续处理后续卡片')
          break
        }
        continue
      }
      const name = card.name

      // 4. 关闭详情；关不掉时当前份仍收进 resumes（内容有效）但必须停止
      batchLog(
        `卡片[${name}] 已读取（ocr_chars=${result.chars}，引擎=${result.ocrEngine}，${result.segments} 段` +
          `${result.bottomReached ? '，已到底' : '，未到底'}）`,
      )
      const closed = await this.tryCloseDetail()
      resumes.push({ name, readResult: result })
      processedNames.add(name)
      if (!closed) {
        pushFailure(name, 'Escape 后简历详情未关闭，无法继续处理后续卡片')
        break
      }
      this.deps.onProgress?.(resumes.length, limit)
      if (resumes.length >= limit) break
    }
    batchLog(`批量结束：尝试 ${attempted} 张，成功 ${resumes.length} 份，失败 ${failures.length} 个`)
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
