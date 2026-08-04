/**
 * 页面动作执行器（设计文档 §10.2 / §11）。
 * 全流程：前置校验 → PLANNED → 原生 Input 点击 → SENT → 结果确认 → CONFIRMED / UNKNOWN / FAILED。
 *
 * 硬约束：
 * - 只用 CdpGateway 类型化白名单方法（Input.dispatchMouseEvent / DOMSnapshot / Page.captureScreenshot），
 *   禁止 Runtime.*；本模块不持有通用 send。
 * - 每次点击前重新 captureDomSnapshot 定位，不复用旧坐标（设计 §11：恢复时不复用旧坐标）。
 * - UNKNOWN 永不自动重试：同 unique_key 已有 SENT/CONFIRMED/UNKNOWN 记录 → IDEMPOTENT_SKIP。
 * - 崩溃残留的 PLANNED 记录重新执行时，走与首次完全相同的完整前置校验。
 * - 不做任何"猜坐标"补救：按钮歧义、原因弹层不唯一 → FAILED/UNKNOWN，进人工处理。
 *
 * 结果确认启发式（确定性，不依赖 Network 响应体）：
 * - GREET 成功：fresh snapshot 中不再出现「打招呼」文案（按钮变为「继续沟通」等）
 * - REJECT 成功：fresh snapshot 中不再出现「不合适/不感兴趣」及原因选项文案
 * 无法确认 → UNKNOWN。
 */
import type { CdpGateway } from '../cdp/CdpGateway.js'
import type { Conclusion } from '../screening/ScreeningEngine.js'
import type { DomSnapshot } from '../boss/domSnapshot.js'
import { ButtonLocator, snapshotContainsText } from '../boss/ButtonLocator.js'
import { makeFingerprint } from '../boss/ListSnapshotParser.js'
import {
  ActionPlanner,
  GREET_BUTTON_TEXTS,
  REJECT_BUTTON_TEXTS,
  type ActionLimits,
  type ActionPlan,
} from './ActionPlanner.js'
import { ActionStore, NON_RETRYABLE_STATUSES, type ActionRecord } from './ActionStore.js'

/** 结构子集，便于测试 mock；CdpGateway 天然满足 */
export type ActionCdpGateway = Pick<
  CdpGateway,
  'captureScreenshot' | 'captureDomSnapshot' | 'dispatchMouse'
>

export interface ExecuteInput {
  sessionId: number
  candidateId: number | null
  /** 当前详情候选人姓名（用于 fresh snapshot 中确认详情仍是该候选人） */
  candidateName: string
  /** HMAC 指纹密钥（与列表阶段一致） */
  fingerprintKey: string
  fingerprintContext?: string
  /** 评估记录中的候选人指纹 */
  evalCandidateFingerprint: string
  conclusion: Conclusion
  evalReason: string
  viewport: { width: number; height: number }
  limits?: Partial<ActionLimits>
}

export type ExecuteStatus = 'CONFIRMED' | 'UNKNOWN' | 'FAILED' | 'NO_ACTION' | 'IDEMPOTENT_SKIP'

export interface ExecuteOutcome {
  status: ExecuteStatus
  plan?: ActionPlan
  recordId?: number
  /** FAILED/UNKNOWN 的错误说明 */
  error?: string
  /** NO_ACTION / IDEMPOTENT_SKIP 的拦截原因 */
  blockedReason?: string
}

export interface ExecutorOptions {
  store: ActionStore
  gateway: ActionCdpGateway
  /** 点击后等待页面响应的时间（ms），默认 800 */
  confirmWaitMs?: number
  /** 截图存证回调：返回落盘路径；不提供则不存证 */
  saveScreenshot?: (data: Buffer, tag: 'before' | 'after') => string
}

export class PageActionExecutor {
  private readonly planner = new ActionPlanner()
  private readonly locator = new ButtonLocator()
  private readonly confirmWaitMs: number

  constructor(private readonly opts: ExecutorOptions) {
    this.confirmWaitMs = opts.confirmWaitMs ?? 800
  }

  async execute(input: ExecuteInput): Promise<ExecuteOutcome> {
    const { store, gateway } = this.opts

    // ---- 快速短路：不需要任何 CDP 调用 ----
    if (input.conclusion === 'UNCERTAIN') {
      return {
        status: 'NO_ACTION',
        plan: { kind: 'NO_ACTION', blockedReason: '结论 UNCERTAIN，进人工复核' },
        blockedReason: '结论 UNCERTAIN，进人工复核',
      }
    }
    const actionType = input.conclusion === 'QUALIFIED' ? 'GREET' : 'REJECT'
    // 当前候选人指纹：指纹只依赖 密钥|上下文|姓名，不依赖页面坐标
    const currentFingerprint = makeFingerprint(
      input.fingerprintKey,
      input.fingerprintContext ?? '',
      input.candidateName,
    )
    const uniqueKey = ActionStore.makeUniqueKey(currentFingerprint, actionType)

    // 幂等拦截：已有不可重试记录 → 跳过（UNKNOWN 也在内，永不自动重试）
    const existing = store.findByUniqueKey(uniqueKey)
    if (existing && NON_RETRYABLE_STATUSES.includes(existing.status)) {
      return {
        status: 'IDEMPOTENT_SKIP',
        recordId: existing.id,
        blockedReason: `该动作已记录（${existing.status}），幂等拦截`,
      }
    }

    // ---- fresh snapshot：确认详情仍是该候选人 + 按钮唯一性 ----
    let snap: DomSnapshot
    try {
      snap = (await gateway.captureDomSnapshot()) as DomSnapshot
    } catch (e) {
      return { status: 'FAILED', error: `DOMSnapshot 获取失败: ${errMsg(e)}` }
    }
    if (!snapshotContainsText(snap, [input.candidateName])) {
      return { status: 'FAILED', error: '当前详情中未找到候选人姓名，详情可能已切换' }
    }
    if (currentFingerprint !== input.evalCandidateFingerprint) {
      return { status: 'FAILED', error: '当前详情候选人指纹与评估记录不一致' }
    }

    const plan = this.planner.plan({
      conclusion: input.conclusion,
      evalReason: input.evalReason,
      evalCandidateFingerprint: input.evalCandidateFingerprint,
      currentCandidateFingerprint: currentFingerprint,
      snapshot: snap,
      viewport: input.viewport,
      sessionAttemptCount: store.countAttemptsBySession(input.sessionId),
      dailyAttemptCount: store.countDailyAttempts(),
      alreadyRecorded: false, // 上面已处理不可重试记录；此处一定为 false
      limits: input.limits,
    })
    if (plan.kind === 'NO_ACTION') {
      return { status: 'NO_ACTION', plan, blockedReason: plan.blockedReason }
    }

    // ---- 落 PLANNED（幂等插入；崩溃残留 PLANNED / FAILED 记录重置后复用） ----
    const { record, inserted } = store.getOrInsertPlanned({
      candidateId: input.candidateId,
      sessionId: input.sessionId,
      action: actionType,
      reason: input.evalReason,
      uniqueKey,
    })
    if (!inserted) {
      if (NON_RETRYABLE_STATUSES.includes(record.status)) {
        return {
          status: 'IDEMPOTENT_SKIP',
          plan,
          recordId: record.id,
          blockedReason: `该动作已记录（${record.status}），幂等拦截`,
        }
      }
      // PLANNED（崩溃残留）/ FAILED：重置后重新走完整流程（不读旧坐标，下面重新定位）
      store.resetToPlanned(record.id)
    }

    return this.executePlanned(record, plan, input)
  }

  /** 已 PLANNED 的记录：重新定位 → 点击 → 确认。任何异常按 sent 与否分流 FAILED/UNKNOWN。 */
  private async executePlanned(
    record: ActionRecord,
    plan: Exclude<ActionPlan, { kind: 'NO_ACTION' }>,
    input: ExecuteInput,
  ): Promise<ExecuteOutcome> {
    const { store, gateway } = this.opts
    const buttonTexts = plan.kind === 'GREET' ? GREET_BUTTON_TEXTS : REJECT_BUTTON_TEXTS
    let sent = false

    try {
      // 1. 点击前再次 fresh snapshot 定位（不复用 plan 阶段的坐标）
      const snap = (await gateway.captureDomSnapshot()) as DomSnapshot
      if (!snapshotContainsText(snap, [input.candidateName])) {
        store.markFailed(record.id, '执行前详情中未找到候选人姓名')
        return { status: 'FAILED', plan, recordId: record.id, error: '执行前详情中未找到候选人姓名' }
      }
      const located = this.locator.locateUnique(snap, buttonTexts, input.viewport)
      if (located.status !== 'LOCATED') {
        store.markFailed(record.id, `按钮定位失败: ${located.reason}`)
        return { status: 'FAILED', plan, recordId: record.id, error: `按钮定位失败: ${located.reason}` }
      }

      // 2. 存证（before）
      const beforePath = await this.saveShot('before')
      if (beforePath) store.setBeforeScreenshot(record.id, beforePath)

      // 3. 原生 Input 点击
      await gateway.dispatchMouse({
        type: 'mousePressed',
        x: located.point.x,
        y: located.point.y,
        button: 'left',
        clickCount: 1,
      })
      // mousePressed 已发出即视为输入可能已送达页面：此后任何失败必须 UNKNOWN，
      // 绝不能标 FAILED 触发重试造成重复点击
      sent = true
      await gateway.dispatchMouse({
        type: 'mouseReleased',
        x: located.point.x,
        y: located.point.y,
        button: 'left',
        clickCount: 1,
      })
      store.markSent(record.id)
      await this.wait(this.confirmWaitMs)

      // 4. REJECT：原因弹层选择映射原因
      if (plan.kind === 'REJECT') {
        const dialogSnap = (await gateway.captureDomSnapshot()) as DomSnapshot
        const reasonLocated = this.locator.locateUnique(dialogSnap, [plan.reasonOption], input.viewport)
        if (reasonLocated.status !== 'LOCATED') {
          // 点击已发出但原因弹层无法唯一确认 → UNKNOWN，不重试
          store.markUnknown(record.id, `原因弹层定位失败: ${reasonLocated.reason}`)
          return {
            status: 'UNKNOWN',
            plan,
            recordId: record.id,
            error: `原因弹层定位失败: ${reasonLocated.reason}`,
          }
        }
        await gateway.dispatchMouse({
          type: 'mousePressed',
          x: reasonLocated.point.x,
          y: reasonLocated.point.y,
          button: 'left',
          clickCount: 1,
        })
        await gateway.dispatchMouse({
          type: 'mouseReleased',
          x: reasonLocated.point.x,
          y: reasonLocated.point.y,
          button: 'left',
          clickCount: 1,
        })
        await this.wait(this.confirmWaitMs)
      }

      // 5. 结果确认：按钮文案从 fresh snapshot 中消失 → CONFIRMED，否则 UNKNOWN
      const confirmSnap = (await gateway.captureDomSnapshot()) as DomSnapshot
      const buttonGone = !snapshotContainsText(confirmSnap, buttonTexts)
      const reasonGone =
        plan.kind === 'GREET' || !snapshotContainsText(confirmSnap, [plan.reasonOption])
      if (buttonGone && reasonGone) {
        const afterPath = await this.saveShot('after')
        store.markConfirmed(record.id, afterPath)
        return { status: 'CONFIRMED', plan, recordId: record.id }
      }
      const afterPathUnknown = await this.saveShot('after')
      if (afterPathUnknown) store.setAfterScreenshot(record.id, afterPathUnknown)
      store.markUnknown(record.id, '点击后按钮仍存在，结果无法确认')
      return { status: 'UNKNOWN', plan, recordId: record.id, error: '点击后按钮仍存在，结果无法确认' }
    } catch (e) {
      const msg = errMsg(e)
      if (sent) {
        // 已发出输入后出错：结果不可知 → UNKNOWN（永不自动重试）
        store.markUnknown(record.id, msg)
        return { status: 'UNKNOWN', plan, recordId: record.id, error: msg }
      }
      store.markFailed(record.id, msg)
      return { status: 'FAILED', plan, recordId: record.id, error: msg }
    }
  }

  /** 截图存证：返回落盘路径；失败返回 null（存证是辅助证据，不阻断流程） */
  private async saveShot(tag: 'before' | 'after'): Promise<string | null> {
    if (!this.opts.saveScreenshot) return null
    try {
      const base64 = await this.opts.gateway.captureScreenshot({ format: 'png' })
      return this.opts.saveScreenshot(Buffer.from(base64, 'base64'), tag)
    } catch {
      return null // 存证失败忽略，结果确认仍走 DOMSnapshot
    }
  }

  private wait(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms))
  }
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
