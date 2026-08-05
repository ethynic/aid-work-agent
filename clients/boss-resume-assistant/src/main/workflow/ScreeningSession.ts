/**
 * 筛选会话编排器（设计文档 §11 状态机与恢复，Phase 8 核心）。
 *
 * 状态机：
 *   IDLE → WAITING_MANUAL_LOGIN → CONNECTING_CDP → READING_LIST
 *     → OPENING_DETAIL → CAPTURING_DETAIL → OCR_AND_NORMALIZE → SCREENING
 *     → WAITING_REVIEW | EXECUTING_ACTION → CLOSING_DETAIL → CHECKPOINT → READING_LIST（循环）
 *
 * 硬规则（设计 §11/§15）：
 * - 任一阶段异常（URL 异常 / DOMSnapshot 无法唯一解析 / 拼接失败 / CDP 方法被拒 /
 *   动作结果未知 / Chrome 断开）→ 立即 PAUSED，事件推送渲染层，人工恢复
 * - 恢复时重新读列表、按指纹对齐，不复用旧坐标（本实现天然满足：所有定位都是 fresh snapshot）
 * - UNKNOWN/SENT 动作不重发（由 PageActionExecutor 幂等保证，本层只负责不绕过它）
 * - 候选人指纹去重：已评估过的候选人不再打开详情
 * - 用户暂停/停止在候选人边界生效（绝不在动作执行中途打断，避免半点击状态）
 *
 * 所有 CDP 交互通过注入的 SessionGateway（生产为 CdpGateway，测试为 stub）。
 * 本类不依赖具体入口形态，便于 node:test 单测。
 */
import type { Database as BetterSqliteDatabase } from 'better-sqlite3'
import type { CdpGateway } from '../cdp/CdpGateway.js'
import type { DomSnapshot } from '../boss/domSnapshot.js'
import { ListSnapshotParser, makeFingerprint } from '../boss/ListSnapshotParser.js'
import { snapshotContainsText } from '../boss/ButtonLocator.js'
import { DetailCapture } from '../boss/DetailCapture.js'
import { sendEscapeClose } from '../boss/DetailCloser.js'
import { LongScreenshotStitcher } from '../image/LongScreenshotStitcher.js'
import { runOcrOrFail, type OcrProvider } from '../ocr/OcrProvider.js'
import { normalize } from '../normalize/ResumeNormalizer.js'
import { ScreeningEngine, type Conclusion, type LlmProvider } from '../screening/ScreeningEngine.js'
import { PageActionExecutor } from '../actions/PageActionExecutor.js'
import { ActionStore } from '../actions/ActionStore.js'
import { SessionStore } from '../storage/sessionStore.js'
import { enumerateCandidates } from './candidateEnumerator.js'
import { buildHardRules, parseHardRuleConfig } from './hardRules.js'

export type SessionState =
  | 'IDLE'
  | 'WAITING_MANUAL_LOGIN'
  | 'CONNECTING_CDP'
  | 'READING_LIST'
  | 'OPENING_DETAIL'
  | 'CAPTURING_DETAIL'
  | 'OCR_AND_NORMALIZE'
  | 'SCREENING'
  | 'WAITING_REVIEW'
  | 'EXECUTING_ACTION'
  | 'CLOSING_DETAIL'
  | 'CHECKPOINT'
  | 'PAUSED'
  | 'STOPPED'
  | 'COMPLETED'

/** 付费简历解锁弹层标记词（真机语料 2026-08-04）：详情打开后 snapshot 命中任一词即判定付费锁定 */
const PAYWALL_MARKERS = ['直豆', '首充', '道具解锁', '付费解锁', '解锁简历', '开通会员'] as const

/** 编排层用到的 CDP 能力子集（CdpGateway 天然满足，测试可 stub） */
export type SessionGateway = Pick<
  CdpGateway,
  'captureDomSnapshot' | 'captureScreenshot' | 'dispatchMouse' | 'dispatchKey'
>

export interface SessionEvent {
  type: 'state' | 'log' | 'candidate'
  state?: SessionState
  level?: 'info' | 'warn' | 'error'
  message?: string
  candidateName?: string
  conclusion?: Conclusion
  at: string
}

export interface SessionStats {
  viewed: number
  qualified: number
  rejected: number
  uncertain: number
  actionsAttempted: number
}

export interface SessionStatus {
  state: SessionState
  connected: boolean
  jobId: number | null
  sessionRowId: number | null
  pauseReason: string | null
  stats: SessionStats
}

export interface ScreeningSessionDeps {
  db: BetterSqliteDatabase
  /** CONNECTING_CDP 阶段调用：建立 CDP 连接 + attach + Page.enable，返回可用 gateway */
  connect: () => Promise<SessionGateway>
  ocrProvider: OcrProvider | null
  llmProvider?: LlmProvider
  viewport: { width: number; height: number }
  /** HMAC 指纹密钥（本地去重用） */
  fingerprintKey: string
  /** 截图/长图落盘，返回路径 */
  saveCapture: (kind: string, data: Buffer) => string
  /** 事件出口（runtime 转发到渲染层） */
  emit: (event: SessionEvent) => void
  /** 点击/滚动后等待页面响应 ms（默认 800；测试调 0） */
  settleMs?: number
  /** 无新候选人时列表滚动重试轮次上限（默认 3） */
  maxListScrollRounds?: number
}

/** 岗位配置（编排层需要的子集，从 jobs 表读出后传入） */
export interface SessionJobConfig {
  id: number
  name: string
  hardRules: string | null
  knowledgeVersion: string | null
  ruleVersion: string | null
  actionLimitSession: number | null
  actionLimitDay: number | null
}

export class ScreeningSession {
  private state: SessionState = 'IDLE'
  private gateway: SessionGateway | null = null
  private job: SessionJobConfig | null = null
  private sessionRowId: number | null = null
  private pauseReason: string | null = null
  private pauseRequested = false
  private stopRequested = false
  private running = false
  private readonly settleMs: number
  private readonly maxListScrollRounds: number
  private readonly parser = new ListSnapshotParser()
  private readonly stitcher = new LongScreenshotStitcher()
  private readonly actionStore: ActionStore
  private readonly sessionStore: SessionStore

  constructor(private readonly deps: ScreeningSessionDeps) {
    this.settleMs = deps.settleMs ?? 800
    this.maxListScrollRounds = deps.maxListScrollRounds ?? 3
    this.actionStore = new ActionStore(deps.db)
    this.sessionStore = new SessionStore(deps.db)
  }

  getStatus(): SessionStatus {
    return {
      state: this.state,
      connected: this.gateway !== null,
      jobId: this.job?.id ?? null,
      sessionRowId: this.sessionRowId,
      pauseReason: this.pauseReason,
      stats: this.computeStats(),
    }
  }

  /** Chrome 已启动（用户可见窗口、扫码登录中）：IDLE → WAITING_MANUAL_LOGIN */
  markChromeLaunched(): void {
    if (this.state !== 'IDLE') {
      throw new Error(`当前状态 ${this.state} 不能重复进入登录等待`)
    }
    this.transition('WAITING_MANUAL_LOGIN')
  }

  /**
   * 用户点击「登录完成，开始工作」：WAITING_MANUAL_LOGIN → CONNECTING_CDP。
   * 只有此时才建立 CDP WebSocket（设计 §5.2 手动登录门禁）。
   * 连接失败 → PAUSED（可修正后重试本方法）。
   */
  async confirmLoginReady(): Promise<SessionStatus> {
    if (this.state !== 'WAITING_MANUAL_LOGIN' && !(this.state === 'PAUSED' && this.gateway === null)) {
      throw new Error(`当前状态 ${this.state} 不能确认登录`)
    }
    this.transition('CONNECTING_CDP')
    try {
      this.gateway = await this.deps.connect()
      this.pauseReason = null
      this.log('info', 'CDP 已连接，可选择岗位开始任务')
    } catch (e) {
      this.gateway = null
      this.enterPaused(errMsg(e))
      return this.getStatus()
    }
    return this.getStatus()
  }

  /** Chrome/CDP 断开（由 runtime 订阅 socket disconnect 后调用） */
  handleDisconnect(reason: string): void {
    this.gateway = null
    if (this.running || this.state === 'CONNECTING_CDP') {
      this.enterPaused(`Chrome 断开连接: ${reason}`)
      if (this.sessionRowId !== null) this.sessionStore.end(this.sessionRowId, 'INTERRUPTED')
      // sessionRowId 保留用于统计展示；resume 需要重新连接后才能进行
    }
  }

  /** 开始任务（后台跑循环，立即返回当前状态）。CDP 保持连接时可反复开始（COMPLETED/STOPPED 后换岗位再来）。 */
  start(job: SessionJobConfig): SessionStatus {
    if (!this.gateway) throw new Error('CDP 未连接，请先完成登录确认')
    if (this.running) throw new Error('任务已在运行')
    if (this.state !== 'CONNECTING_CDP' && this.state !== 'COMPLETED' && this.state !== 'STOPPED') {
      throw new Error(`当前状态 ${this.state} 不能开始任务`)
    }
    this.job = job
    this.pauseRequested = false
    this.stopRequested = false
    this.sessionRowId = this.sessionStore.create({})
    this.transition('CONNECTING_CDP')
    this.running = true
    // 后台循环：异常统一收敛为 PAUSED，绝不向上抛
    void this.runLoop().catch((e) => {
      this.enterPaused(errMsg(e))
    })
    return this.getStatus()
  }

  /** 用户暂停：在候选人边界生效 */
  pause(): SessionStatus {
    if (!this.running) throw new Error('任务未在运行')
    this.pauseRequested = true
    this.log('info', '已请求暂停，将在当前候选人处理完后暂停')
    return this.getStatus()
  }

  /** 从 PAUSED 恢复：重新读列表、指纹对齐（不旧坐标，天然满足） */
  resume(): SessionStatus {
    if (this.state !== 'PAUSED') throw new Error(`当前状态 ${this.state} 不能恢复`)
    if (!this.gateway) throw new Error('CDP 已断开，请重新启动 Chrome 并确认登录')
    if (!this.job) throw new Error('无任务上下文，无法恢复')
    this.pauseRequested = false
    this.stopRequested = false
    this.pauseReason = null
    if (this.sessionRowId === null) {
      this.sessionRowId = this.sessionStore.create({})
    } else {
      this.sessionStore.setStatus(this.sessionRowId, 'RUNNING')
    }
    this.running = true
    void this.runLoop().catch((e) => {
      this.enterPaused(errMsg(e))
    })
    return this.getStatus()
  }

  /** 用户停止：在候选人边界生效，会话行记 STOPPED */
  stop(): SessionStatus {
    if (!this.running) {
      // 允许在未运行时复位到 IDLE（如 PAUSED 后放弃任务）
      if (this.state === 'PAUSED' || this.state === 'CONNECTING_CDP') {
        this.finishRun('STOPPED')
        return this.getStatus()
      }
      throw new Error('任务未在运行')
    }
    this.stopRequested = true
    this.log('info', '已请求停止，将在当前候选人处理完后停止')
    return this.getStatus()
  }

  // ===== 内部：主循环 =====

  private async runLoop(): Promise<void> {
    if (!this.gateway || !this.job) throw new Error('runLoop 缺少 gateway/job')
    let scrollRounds = 0

    for (;;) {
      this.checkControlFlags()
      if (!this.running) return

      // READING_LIST：fresh snapshot 枚举候选人
      this.transition('READING_LIST')
      const snapshot = await this.freshSnapshot()
      const enumerated = enumerateCandidates(snapshot, { viewport: this.deps.viewport })
      const pending = enumerated.filter((c) => !this.alreadyEvaluated(this.fingerprintFor(c.name)))

      if (pending.length === 0) {
        scrollRounds += 1
        if (scrollRounds > this.maxListScrollRounds) {
          this.log('info', '列表已无新候选人，任务完成')
          this.finishRun('COMPLETED')
          return
        }
        this.log('info', `本屏无新候选人，滚动列表（第 ${scrollRounds}/${this.maxListScrollRounds} 轮）`)
        await this.gateway.dispatchMouse({ type: 'mouseWheel', x: 400, y: 400, deltaY: 600 })
        await this.wait(this.settleMs)
        continue
      }
      scrollRounds = 0

      for (const candidate of pending) {
        this.checkControlFlags()
        if (!this.running) return
        await this.processCandidate(candidate.name)
        this.transition('CHECKPOINT')
      }
    }
  }

  /** 单个候选人全流程。任何异常上抛，由 runLoop 的 catch 统一转 PAUSED。 */
  private async processCandidate(name: string): Promise<void> {
    const gateway = this.gateway
    const job = this.job
    if (!gateway || !job) throw new Error('缺少 gateway/job')

    const fingerprint = this.fingerprintFor(name)
    const fingerprintContext = this.fingerprintContext()

    // ---- OPENING_DETAIL ----
    this.transition('OPENING_DETAIL', `打开详情: ${name}`)
    const snap = await this.freshSnapshot()
    const located = this.parser.locateUniqueCandidate(snap, name, {
      viewport: this.deps.viewport,
      fingerprintKey: this.deps.fingerprintKey,
      fingerprintContext,
    })
    if (located.status !== 'LOCATED') {
      throw new Error(`候选人 ${name} 无法唯一定位: ${located.reason}`)
    }
    await gateway.dispatchMouse({ type: 'mousePressed', x: located.point.x, y: located.point.y, button: 'left', clickCount: 1 })
    await gateway.dispatchMouse({ type: 'mouseReleased', x: located.point.x, y: located.point.y, button: 'left', clickCount: 1 })
    await this.wait(this.settleMs)
    const detailSnap = await this.freshSnapshot()
    if (!snapshotContainsText(detailSnap, [name])) {
      throw new Error(`详情打开失败: snapshot 中未找到候选人 ${name}`)
    }

    // 付费简历（道具/直豆解锁弹层）看不了正文：落库标记后跳过，不截图/OCR/筛选。
    // 标记词来自真机语料（2026-08-04 probe-snapshot-strings）：首充/直豆/道具/解锁。
    if (snapshotContainsText(detailSnap, PAYWALL_MARKERS)) {
      const payCandidateId = this.upsertCandidate(fingerprint, name)
      this.insertResumeView(payCandidateId, 'PAYWALL_LOCKED', 'NO_SUMMARY', null)
      this.log('info', `${name} 为付费简历（需道具/直豆解锁），已跳过`)
      this.transition('CLOSING_DETAIL', `关闭详情: ${name}`)
      await sendEscapeClose(gateway)
      await this.wait(this.settleMs)
      return
    }

    // 候选人落库（指纹去重）+ resume_views 先插最小行（设计 §15：每次成功打开都有记录）
    const candidateId = this.upsertCandidate(fingerprint, name)
    const resumeViewId = this.insertResumeView(candidateId, 'LIST_DOM_FALLBACK', 'PARTIAL_SUMMARY', null)

    try {
      // ---- CAPTURING_DETAIL ----
      this.transition('CAPTURING_DETAIL', `分段截图: ${name}`)
      const capture = await new DetailCapture(gateway).captureScrolling({ viewportHeight: this.deps.viewport.height })

      // ---- OCR_AND_NORMALIZE ----
      this.transition('OCR_AND_NORMALIZE', `拼接+OCR: ${name}`)
      const stitch = this.stitcher.stitch(capture.shards)
      if (!stitch.ok || !stitch.png) {
        // 拼接失败：保留分片证据后暂停（设计 §8.2）
        capture.shards.forEach((shard, i) => {
          const p = this.deps.saveCapture(`shard-${i}`, shard)
          this.insertCapture(resumeViewId, `shard-${i}`, p, 'STITCH_FAILED')
        })
        throw new Error(`长图拼接失败: ${stitch.reason ?? stitch.integrity}`)
      }
      const longPath = this.deps.saveCapture('long', stitch.png)
      this.insertCapture(resumeViewId, 'long', longPath, stitch.integrity, stitch.width, stitch.height)

      const ocr = await runOcrOrFail(this.deps.ocrProvider, { image: stitch.png })
      const ocrText = ocr.blocks
        .slice()
        .sort((a, b) => a.pageIndex - b.pageIndex || a.box[1] - b.box[1] || a.box[0] - b.box[0])
        .map((b) => b.text)
        .join('\n')

      const doc = normalize({}, { fields: {} }, { candidate: fingerprint, job: this.jobFingerprint() })
      // 归一化 markdown + OCR 原文：筛选输入，同时落库（复核报告要展示 OCR markdown）
      const markdown = `${doc.markdown}\n\n## OCR 原文\n${ocrText}`
      this.updateResumeView(resumeViewId, 'OCR_NORMALIZED', doc.summary.completeness, JSON.stringify(doc.summary), markdown)

      // ---- SCREENING ----
      this.transition('SCREENING', `筛选: ${name}`)
      const engine = new ScreeningEngine(
        buildHardRules(parseHardRuleConfig(job.hardRules)),
        this.deps.llmProvider,
      )
      const result = await engine.screen({
        markdown,
        fields: {},
        knowledgeVersion: job.knowledgeVersion ?? undefined,
        ruleVersion: job.ruleVersion ?? undefined,
      })
      this.insertEvaluation(candidateId, result.conclusion, result.reason, result)

      this.emitEvent({
        type: 'candidate',
        candidateName: name,
        conclusion: result.conclusion,
        message: `${name} → ${result.conclusion}（${result.reason}）`,
        at: nowIso(),
      })

      // ---- WAITING_REVIEW | EXECUTING_ACTION ----
      if (result.conclusion === 'UNCERTAIN') {
        this.transition('WAITING_REVIEW', `${name} 结论 UNCERTAIN，进人工复核`)
      } else {
        this.transition('EXECUTING_ACTION', `执行动作: ${name} (${result.conclusion})`)
        const executor = new PageActionExecutor({
          store: this.actionStore,
          gateway,
          saveScreenshot: (data, tag) => this.deps.saveCapture(`action-${tag}`, data),
        })
        const outcome = await executor.execute({
          sessionId: this.sessionRowId ?? 0,
          candidateId,
          candidateName: name,
          fingerprintKey: this.deps.fingerprintKey,
          fingerprintContext,
          evalCandidateFingerprint: fingerprint,
          conclusion: result.conclusion,
          evalReason: result.reason,
          viewport: this.deps.viewport,
          limits: {
            ...(job.actionLimitSession ? { perSession: job.actionLimitSession } : {}),
            ...(job.actionLimitDay ? { perDay: job.actionLimitDay } : {}),
          },
        })
        if (outcome.status === 'UNKNOWN') {
          throw new Error(`动作结果未知（${name}）: ${outcome.error ?? 'UNKNOWN'}，按设计暂停等待人工处理`)
        }
        if (outcome.status === 'FAILED') {
          throw new Error(`动作执行失败（${name}）: ${outcome.error ?? 'FAILED'}`)
        }
        this.log('info', `${name} 动作结果: ${outcome.status}${outcome.blockedReason ? `（${outcome.blockedReason}）` : ''}`)
      }
    } finally {
      // ---- CLOSING_DETAIL：异常路径也要尝试关闭详情，失败同样暂停 ----
      this.transition('CLOSING_DETAIL', `关闭详情: ${name}`)
      await sendEscapeClose(gateway)
      await this.wait(this.settleMs)
    }
  }

  // ===== 内部：DB =====

  private fingerprintFor(name: string): string {
    return makeFingerprint(this.deps.fingerprintKey, this.fingerprintContext(), name)
  }

  private fingerprintContext(): string {
    return `job:${this.job?.id ?? 0}`
  }

  private jobFingerprint(): string {
    return makeFingerprint(this.deps.fingerprintKey, '', this.fingerprintContext())
  }

  private alreadyEvaluated(fingerprint: string): boolean {
    const row = this.deps.db
      .prepare(
        `SELECT COUNT(*) AS c FROM evaluations e
         JOIN candidates c2 ON c2.id = e.candidate_id
         WHERE c2.fingerprint = ?`,
      )
      .get(fingerprint) as { c: number }
    if (row.c > 0) return true
    // 付费简历跳过也视为已处理，避免每次运行重复打开解锁弹层
    const paywalled = this.deps.db
      .prepare(
        `SELECT COUNT(*) AS c FROM resume_views rv
         JOIN candidates c2 ON c2.id = rv.candidate_id
         WHERE c2.fingerprint = ? AND rv.source = 'PAYWALL_LOCKED'`,
      )
      .get(fingerprint) as { c: number }
    return paywalled.c > 0
  }

  private upsertCandidate(fingerprint: string, name: string): number {
    const db = this.deps.db
    const existing = db.prepare('SELECT id FROM candidates WHERE fingerprint = ?').get(fingerprint) as
      | { id: number }
      | undefined
    const listSummary = JSON.stringify({ name })
    if (existing) {
      db.prepare('UPDATE candidates SET last_seen_at = CURRENT_TIMESTAMP, list_summary = ? WHERE id = ?').run(
        listSummary,
        existing.id,
      )
      return existing.id
    }
    const info = db
      .prepare('INSERT INTO candidates (job_id, fingerprint, list_summary) VALUES (?, ?, ?)')
      .run(this.job?.id ?? null, fingerprint, listSummary)
    return Number(info.lastInsertRowid)
  }

  private insertResumeView(candidateId: number, source: string, completeness: string, summaryJson: string | null): number {
    const info = this.deps.db
      .prepare(
        `INSERT INTO resume_views (job_id, candidate_id, session_id, source, completeness, summary_json)
         VALUES (?, ?, ?, ?, ?, ?)`,
      )
      .run(this.job?.id ?? null, candidateId, this.sessionRowId, source, completeness, summaryJson)
    return Number(info.lastInsertRowid)
  }

  private updateResumeView(id: number, source: string, completeness: string, summaryJson: string, ocrMarkdown?: string): void {
    if (ocrMarkdown !== undefined) {
      this.deps.db
        .prepare('UPDATE resume_views SET source = ?, completeness = ?, summary_json = ?, ocr_markdown = ? WHERE id = ?')
        .run(source, completeness, summaryJson, ocrMarkdown, id)
      return
    }
    this.deps.db
      .prepare('UPDATE resume_views SET source = ?, completeness = ?, summary_json = ? WHERE id = ?')
      .run(source, completeness, summaryJson, id)
  }

  private insertCapture(resumeViewId: number, kind: string, path: string, integrity: string, width?: number, height?: number): void {
    this.deps.db
      .prepare(
        `INSERT INTO captures (resume_view_id, kind, path, width, height, integrity_status)
         VALUES (?, ?, ?, ?, ?, ?)`,
      )
      .run(resumeViewId, kind, path, width ?? null, height ?? null, integrity)
  }

  private insertEvaluation(candidateId: number, conclusion: Conclusion, reason: string, result: { evidence: unknown; model?: string; promptVersion?: string; inputHash?: string; durationMs?: number }): void {
    this.deps.db
      .prepare(
        `INSERT INTO evaluations (candidate_id, session_id, conclusion, reason, evidence, model, prompt_version, input_hash, duration_ms)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        candidateId,
        this.sessionRowId,
        conclusion,
        reason,
        JSON.stringify(result.evidence),
        result.model ?? null,
        result.promptVersion ?? null,
        result.inputHash ?? null,
        result.durationMs ?? null,
      )
  }

  private computeStats(): SessionStats {
    const sid = this.sessionRowId
    if (sid === null) {
      return { viewed: 0, qualified: 0, rejected: 0, uncertain: 0, actionsAttempted: 0 }
    }
    const db = this.deps.db
    const viewed = (db.prepare('SELECT COUNT(*) AS c FROM resume_views WHERE session_id = ?').get(sid) as { c: number }).c
    const byConclusion = db
      .prepare('SELECT conclusion, COUNT(*) AS c FROM evaluations WHERE session_id = ? GROUP BY conclusion')
      .all(sid) as Array<{ conclusion: string; c: number }>
    return {
      viewed,
      qualified: byConclusion.find((r) => r.conclusion === 'QUALIFIED')?.c ?? 0,
      rejected: byConclusion.find((r) => r.conclusion === 'REJECTED')?.c ?? 0,
      uncertain: byConclusion.find((r) => r.conclusion === 'UNCERTAIN')?.c ?? 0,
      actionsAttempted: this.actionStore.countAttemptsBySession(sid),
    }
  }

  // ===== 内部：状态/控制 =====

  private async freshSnapshot(): Promise<DomSnapshot> {
    if (!this.gateway) throw new Error('CDP 未连接')
    return (await this.gateway.captureDomSnapshot()) as DomSnapshot
  }

  /** 用户暂停/停止在候选人边界生效 */
  private checkControlFlags(): void {
    if (this.stopRequested) {
      this.finishRun('STOPPED')
      return
    }
    if (this.pauseRequested) {
      this.pauseRequested = false
      this.enterPaused('用户请求暂停')
    }
  }

  private enterPaused(reason: string): void {
    this.pauseReason = reason
    this.running = false
    if (this.sessionRowId !== null) this.sessionStore.setStatus(this.sessionRowId, 'PAUSED')
    this.transition('PAUSED', reason)
  }

  private finishRun(status: 'STOPPED' | 'COMPLETED'): void {
    this.running = false
    this.stopRequested = false
    if (this.sessionRowId !== null) this.sessionStore.end(this.sessionRowId, status)
    // sessionRowId 保留：终态下 UI 仍要展示本次会话统计；下次 start() 会换新行
    this.transition(status)
  }

  /** 复位到 IDLE（Chrome 已关闭、准备重新启动时用）。仅允许从终态/登录等待/无连接的 PAUSED 复位。 */
  reset(): SessionStatus {
    if (this.running) throw new Error('任务运行中，不能复位')
    if (
      this.state !== 'STOPPED' &&
      this.state !== 'COMPLETED' &&
      this.state !== 'WAITING_MANUAL_LOGIN' &&
      !(this.state === 'PAUSED' && this.gateway === null)
    ) {
      throw new Error(`当前状态 ${this.state} 不能复位`)
    }
    this.job = null
    this.pauseReason = null
    this.pauseRequested = false
    this.stopRequested = false
    this.transition('IDLE')
    return this.getStatus()
  }

  private transition(state: SessionState, message?: string): void {
    this.state = state
    this.emitEvent({ type: 'state', state, message, at: nowIso() })
  }

  private log(level: 'info' | 'warn' | 'error', message: string): void {
    this.emitEvent({ type: 'log', level, message, at: nowIso() })
  }

  private emitEvent(event: SessionEvent): void {
    try {
      this.deps.emit(event)
    } catch {
      // 事件出口异常不影响编排（渲染层断开不应暂停任务）
    }
  }

  private wait(ms: number): Promise<void> {
    if (ms <= 0) return Promise.resolve()
    return new Promise((r) => setTimeout(r, ms))
  }
}

function nowIso(): string {
  return new Date().toISOString()
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
