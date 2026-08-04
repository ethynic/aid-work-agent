/**
 * 动作规划器（设计文档 §10.1）。
 * 三态结论 → GREET / REJECT / NO_ACTION，全部前置条件满足才产出可执行计划：
 * 1. 结论不是 UNCERTAIN（UNCERTAIN → NO_ACTION，进人工复核）
 * 2. 当前详情候选人指纹与评估记录一致
 * 3. 按钮在 DOMSnapshot 中只匹配到唯一目标区域（歧义 → 不执行）
 * 4. 未超会话/每日动作上限（可配置，默认会话 50 / 日 100）
 * 5. 该候选人该动作未在本地成功记录过（幂等：SENT/CONFIRMED/UNKNOWN 拦截）
 * 6. REJECT 额外要求：评估理由能显式映射到 BOSS 不合适原因选项
 *
 * 纯决策模块：不接触 CDP / DB，所有事实由调用方传入，便于单测。
 */
import type { Conclusion } from '../screening/ScreeningEngine.js'
import type { DomSnapshot } from '../boss/domSnapshot.js'
import { ButtonLocator } from '../boss/ButtonLocator.js'
import { mapRejectReason, type RejectReasonOption } from './reasonMapping.js'
import { ActionStore, type ExecutableActionType } from './ActionStore.js'

/** 「打招呼」按钮候选文案 */
export const GREET_BUTTON_TEXTS = ['打招呼'] as const
/** 「不合适」按钮候选文案 */
export const REJECT_BUTTON_TEXTS = ['不合适', '不感兴趣'] as const

/** 动作上限默认值（设计文档：用户可配置） */
export const DEFAULT_ACTION_LIMITS = { perSession: 50, perDay: 100 } as const

export interface ActionLimits {
  perSession: number
  perDay: number
}

export type ActionPlan =
  | { kind: 'GREET'; uniqueKey: string }
  | { kind: 'REJECT'; uniqueKey: string; reasonOption: RejectReasonOption }
  | { kind: 'NO_ACTION'; blockedReason: string }

export interface PlanInput {
  conclusion: Conclusion
  /** 评估理由文本（REJECT 时用于原因映射） */
  evalReason: string
  /** 评估记录中的候选人指纹 */
  evalCandidateFingerprint: string
  /** 当前详情重新计算出的候选人指纹 */
  currentCandidateFingerprint: string
  /** fresh DOMSnapshot（按钮唯一性检查） */
  snapshot: DomSnapshot
  viewport: { width: number; height: number }
  /** 会话内已发出动作数（SENT/CONFIRMED/UNKNOWN） */
  sessionAttemptCount: number
  /** 当日已发出动作数 */
  dailyAttemptCount: number
  /** 同 unique_key 是否已有不可重试记录 */
  alreadyRecorded: boolean
  limits?: Partial<ActionLimits>
}

export class ActionPlanner {
  private readonly locator = new ButtonLocator()

  plan(input: PlanInput): ActionPlan {
    const limits: ActionLimits = {
      perSession: input.limits?.perSession ?? DEFAULT_ACTION_LIMITS.perSession,
      perDay: input.limits?.perDay ?? DEFAULT_ACTION_LIMITS.perDay,
    }

    // 1. 三态映射
    if (input.conclusion === 'UNCERTAIN') {
      return { kind: 'NO_ACTION', blockedReason: '结论 UNCERTAIN，进人工复核' }
    }
    const actionType: ExecutableActionType = input.conclusion === 'QUALIFIED' ? 'GREET' : 'REJECT'
    const uniqueKey = ActionStore.makeUniqueKey(input.currentCandidateFingerprint, actionType)

    // 2. 指纹一致性
    if (input.currentCandidateFingerprint !== input.evalCandidateFingerprint) {
      return { kind: 'NO_ACTION', blockedReason: '当前详情候选人指纹与评估记录不一致' }
    }

    // 3. 幂等：已发出/结果未知的动作不再执行
    if (input.alreadyRecorded) {
      return { kind: 'NO_ACTION', blockedReason: '该候选人该动作已在本地记录，幂等拦截' }
    }

    // 4. 动作上限
    if (input.sessionAttemptCount >= limits.perSession) {
      return { kind: 'NO_ACTION', blockedReason: `已达会话动作上限 ${limits.perSession}` }
    }
    if (input.dailyAttemptCount >= limits.perDay) {
      return { kind: 'NO_ACTION', blockedReason: `已达每日动作上限 ${limits.perDay}` }
    }

    // 5. 按钮唯一性（文本 + 几何联合定位，歧义不执行）
    const buttonTexts = actionType === 'GREET' ? GREET_BUTTON_TEXTS : REJECT_BUTTON_TEXTS
    const located = this.locator.locateUnique(input.snapshot, buttonTexts, input.viewport)
    if (located.status !== 'LOCATED') {
      return { kind: 'NO_ACTION', blockedReason: `按钮定位失败: ${located.reason}` }
    }

    // 6. REJECT 原因必须显式映射
    if (actionType === 'REJECT') {
      const reasonOption = mapRejectReason(input.evalReason)
      if (!reasonOption) {
        return { kind: 'NO_ACTION', blockedReason: '评估理由无法映射到不合适原因选项，进人工复核' }
      }
      return { kind: 'REJECT', uniqueKey, reasonOption }
    }

    return { kind: 'GREET', uniqueKey }
  }
}
