/**
 * session_observer_v1 契约（端侧会话任务 C0 冻结，设计 §6 / 计划 §3）。
 *
 * 这是常驻会话观察器的**结果/请求 schema 与错误码冻结**，不是实现：M2 的
 * weixin_unread_list 仍是逐次 spawn Python + 每次构造 RapidOCR 的未读列表接口，
 * 不满足本契约（无常驻水位、无连续窗口对齐、无稳定消息 ID）。C2 才实现
 * 常驻 OCR/observer；实现必须通过 tests/session-observer-contract.test.ts。
 *
 * 冻结语义（与设计 §6 一致，实现不得私自放宽）：
 * - coverage 只描述「与上次水位连续对齐的窗口」，不承诺全账号全历史完整。
 * - 已建立的空基线用 {last_local_message_id: null, window_fingerprint} 表示；
 *   它是增量对齐请求，不是重建基线（2026-09-12 评审修订 P2-1）。
 * - coverage=complete_window 才允许携带 ordered_messages 与 window_fingerprint；
 *   gap/unavailable 必须 messages=[] 且 fingerprint=null，绝不返回部分猜测。
 * - sender 无法判定时降级为 gap（gap_reason=sender_ambiguous），complete_window
 *   的消息里不允许出现 sender=unknown——schema 层保留该枚举仅用于证据记录。
 * - OCR 引擎初始化失败 → coverage=unavailable + reason，不返回空列表冒充无人回复。
 * - ordered_messages 语义（2026-09-12 评审修订 P2-3）：水位非 null 时只含**新于水位**
 *   的连续消息（空=无新消息）；watermark=null 建基线时含整个对齐基线窗口。
 * - 观察是只读动作（effect=none）；打开会话可能清未读角标，但不产生写副作用。
 */
import { z } from 'zod'
import type { ErrorCode } from '../operations/types.js'

/** Provider 能力名（manifest 协商用；未协商不得调用 observer） */
export const SESSION_OBSERVER_CAPABILITY = 'session_observer_v1'

/**
 * 观察操作失败允许复用的既有 ErrorCode 子集（C0 冻结；新增值需先改设计再改这里）。
 * 引擎/环境类可恢复失败按设计 §6 走 coverage=unavailable 结果，不走错误码。
 */
export const SESSION_OBSERVER_ERROR_CODES = [
  'WINDOWS_REQUIRED',
  'INTERACTIVE_SESSION_REQUIRED',
  'WEIXIN_NOT_FOUND',
  'NOT_LOGGED_IN',
  'WINDOW_AMBIGUOUS',
  'FOREGROUND_LOST',
  'WINDOW_UNTRUSTED',
  'UI_CHANGED',
  'RESULT_TIMEOUT',
  'TARGET_NOT_FOUND',
  'TARGET_AMBIGUOUS',
  'TARGET_REF_STALE',
  'BLOCKED',
  'RISK_CONTROL',
  'BUSY',
  'CANCELLED',
  'INTERNAL_ERROR',
] as const satisfies readonly ErrorCode[]

/** local_message_id：本地分配的稳定消息 ID，格式 m-<uuid>；去重靠连续对齐，不靠正文 hash */
const LOCAL_MESSAGE_ID = /^m-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

/** window_fingerprint：连续窗口指纹（对齐后的消息 ID 与边界上下文派生） */
const WINDOW_FINGERPRINT = /^fp_[A-Za-z0-9_-]{8,128}$/

/** coverage=gap 的原因（对齐失败的具体形态；新值需要升能力版本，不得临时加） */
export const SESSION_GAP_REASONS = [
  'sender_ambiguous',
  'alignment_broken',
  'scroll_discontinuity',
  'duplicate_unalignable',
  'layout_changed',
  'viewport_out_of_range',
] as const

/** coverage=unavailable 的原因（环境/引擎/身份不可用，不是对齐失败） */
export const SESSION_UNAVAILABLE_REASONS = [
  'engine_unavailable',
  'ocr_failed',
  'window_missing',
  'viewport_unreachable',
  'account_identity_changed',
] as const

/** 观察请求：携带上次水位做连续对齐。watermark=null 建立基线；非 null 增量对齐。 */
export const sessionObserverWatermarkSchema = z.object({
  /**
   * 已对齐窗口的最后一条消息 ID；null = **已建立的空基线**（基线时刻窗口内无消息）。
   * 空基线不是「未建立」：后续请求携带 {last_local_message_id: null, window_fingerprint}
   * 表示增量对齐，首条入站消息按新消息接纳，不会被当历史跳过。
   */
  last_local_message_id: z.string().regex(LOCAL_MESSAGE_ID).nullable(),
  window_fingerprint: z.string().regex(WINDOW_FINGERPRINT),
})

export type SessionObserverWatermark = z.infer<typeof sessionObserverWatermarkSchema>

export const sessionObserverRequestSchema = z.object({
  conversation_binding_id: z.string().min(1).max(128),
  binding_version: z.number().int().min(0),
  /** 期望的账号身份版本；观察到的实际版本不符 → identity_drift（调用方须阻断） */
  account_identity_version: z.number().int().min(0),
  /** null = 建立基线（不回复旧消息）；非 null = 增量连续窗口对齐 */
  watermark: sessionObserverWatermarkSchema.nullable(),
})

export type SessionObserverRequest = z.infer<typeof sessionObserverRequestSchema>

export const sessionObserverMessageSchema = z.object({
  sender: z.enum(['peer', 'self', 'system', 'unknown']),
  /**
   * OCR 拆行重建后的完整气泡**原文**（含首尾空白）：对齐、正文比较与完成证据引用
   * 都用它，校验不得变换文本；仅拒绝纯空白（不可构成有效气泡）。
   */
  text: z
    .string()
    .min(1)
    .max(20_000)
    .refine((t) => t.trim().length > 0, { message: '纯空白文本无效' }),
  local_message_id: z.string().regex(LOCAL_MESSAGE_ID),
  /** 端侧受控证据引用（截图/裁剪块的 DPAPI 受控资源 ID），云端只见 opaque ID */
  source_evidence_ref: z.string().min(1).max(256),
})

export type SessionObserverMessage = z.infer<typeof sessionObserverMessageSchema>

export const sessionObservationSchema = z.object({
  observation_id: z.string().uuid(),
  /** 观察到的账号身份版本（与请求期望不符时调用方按身份漂移处理） */
  account_identity_version: z.number().int().min(0),
  conversation_binding_id: z.string().min(1).max(128),
  binding_version: z.number().int().min(0),
  observed_at: z.string().datetime({ offset: true }),
  coverage: z.enum(['complete_window', 'gap', 'unavailable']),
  ordered_messages: z.array(sessionObserverMessageSchema),
  /** 仅 complete_window 必填；gap/unavailable 必须为 null */
  window_fingerprint: z.string().regex(WINDOW_FINGERPRINT).nullable(),
  /** 仅 gap/unavailable 必填；complete_window 必须为 null */
  gap_reason: z.enum([...SESSION_GAP_REASONS, ...SESSION_UNAVAILABLE_REASONS]).nullable(),
})

export type SessionObservation = z.infer<typeof sessionObservationSchema>

/** 契约校验失败码（观察结果本身不合法；实现 bug 或协议漂移） */
export type SessionObserverContractErrorCode =
  | 'SCHEMA_INVALID'
  | 'MESSAGES_ON_NON_COMPLETE' // gap/unavailable 却带消息
  | 'FINGERPRINT_MISMATCH' // fingerprint 与 coverage 不配套
  | 'GAP_REASON_MISMATCH' // gap_reason 与 coverage 不配套
  | 'GAP_REASON_SET_MISMATCH' // 原因值不属于该 coverage 的枚举集
  | 'UNKNOWN_SENDER_IN_WINDOW' // complete_window 出现 sender=unknown，必须降级 gap
  | 'DUPLICATE_MESSAGE_ID' // 同一观察内 local_message_id 重复（对齐失败却没报 gap）

export interface SessionObserverContractError {
  ok: false
  code: SessionObserverContractErrorCode
  message: string
}

/**
 * 观察结果的语义校验（schema 之上的不变量）。Runtime 侧接纳观察前必须调用；
 * 测试用它冻结 fake 语料的合法/非法形态。
 */
export function validateSessionObservation(
  payload: unknown,
): { ok: true; value: SessionObservation } | SessionObserverContractError {
  const parsed = sessionObservationSchema.safeParse(payload)
  if (!parsed.success) {
    return { ok: false, code: 'SCHEMA_INVALID', message: parsed.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; ') }
  }
  const v = parsed.data

  if (v.coverage !== 'complete_window' && v.ordered_messages.length > 0) {
    return { ok: false, code: 'MESSAGES_ON_NON_COMPLETE', message: `coverage=${v.coverage} 不允许携带消息（got ${v.ordered_messages.length} 条）` }
  }
  if (v.coverage === 'complete_window' && v.window_fingerprint === null) {
    return { ok: false, code: 'FINGERPRINT_MISMATCH', message: 'complete_window 必须携带 window_fingerprint' }
  }
  if (v.coverage !== 'complete_window' && v.window_fingerprint !== null) {
    return { ok: false, code: 'FINGERPRINT_MISMATCH', message: `coverage=${v.coverage} 的 window_fingerprint 必须为 null` }
  }
  if (v.coverage === 'complete_window' && v.gap_reason !== null) {
    return { ok: false, code: 'GAP_REASON_MISMATCH', message: 'complete_window 不允许携带 gap_reason' }
  }
  if (v.coverage !== 'complete_window') {
    if (v.gap_reason === null) {
      return { ok: false, code: 'GAP_REASON_MISMATCH', message: `coverage=${v.coverage} 必须携带 gap_reason` }
    }
    const allowed: readonly string[] = v.coverage === 'gap' ? SESSION_GAP_REASONS : SESSION_UNAVAILABLE_REASONS
    if (!allowed.includes(v.gap_reason)) {
      return { ok: false, code: 'GAP_REASON_SET_MISMATCH', message: `coverage=${v.coverage} 不接受 gap_reason=${v.gap_reason}` }
    }
  }
  if (v.coverage === 'complete_window') {
    if (v.ordered_messages.some((m) => m.sender === 'unknown')) {
      return { ok: false, code: 'UNKNOWN_SENDER_IN_WINDOW', message: 'sender 无法判定的消息必须整体降级为 gap，不得混入 complete_window' }
    }
    const ids = new Set(v.ordered_messages.map((m) => m.local_message_id))
    if (ids.size !== v.ordered_messages.length) {
      return { ok: false, code: 'DUPLICATE_MESSAGE_ID', message: '同一观察内 local_message_id 重复（对齐失败应报 gap）' }
    }
  }
  return { ok: true, value: v }
}

/**
 * 协议级结果分类（C0 评审修订）：给定已校验的请求与观察，输出调用方（Runtime）
 * 必须采取的**协议层**处置。它是 fake 重放语料的预期判定基准；C2 调度器在此
 * 之上实现等待/唤醒/blocked 状态机，C0 不实现调度。
 *
 * 优先级：三项身份字段（conversation_binding_id / binding_version /
 * account_identity_version）任一不匹配 > coverage。不匹配时窗口内容一律不可信
 * （跨会话串扰、绑定重建或账号漂移；设计 §4/§6/§13.3，任务 blocked、重新核验），
 * 不推进水位，即使 coverage=complete_window。
 */
export type SessionObservationOutcome =
  | { kind: 'binding_mismatch'; field: 'conversation_binding_id' | 'binding_version'; expected: string | number; observed: string | number }
  | { kind: 'identity_drift'; expected_version: number; observed_version: number }
  | { kind: 'baseline'; historical_count: number; watermark: SessionObserverWatermark }
  | { kind: 'new_messages'; count: number; watermark: SessionObserverWatermark }
  | { kind: 'no_change'; watermark: SessionObserverWatermark }
  | { kind: 'gap'; reason: (typeof SESSION_GAP_REASONS)[number] }
  | { kind: 'unavailable'; reason: (typeof SESSION_UNAVAILABLE_REASONS)[number] }

export function classifySessionObservation(
  request: SessionObserverRequest,
  observation: SessionObservation,
): SessionObservationOutcome {
  if (observation.conversation_binding_id !== request.conversation_binding_id) {
    return { kind: 'binding_mismatch', field: 'conversation_binding_id', expected: request.conversation_binding_id, observed: observation.conversation_binding_id }
  }
  if (observation.binding_version !== request.binding_version) {
    return { kind: 'binding_mismatch', field: 'binding_version', expected: request.binding_version, observed: observation.binding_version }
  }
  if (observation.account_identity_version !== request.account_identity_version) {
    return { kind: 'identity_drift', expected_version: request.account_identity_version, observed_version: observation.account_identity_version }
  }
  if (observation.coverage === 'gap') {
    return { kind: 'gap', reason: observation.gap_reason as (typeof SESSION_GAP_REASONS)[number] }
  }
  if (observation.coverage === 'unavailable') {
    return { kind: 'unavailable', reason: observation.gap_reason as (typeof SESSION_UNAVAILABLE_REASONS)[number] }
  }
  // 建基线请求（watermark=null）：窗口消息是历史锚点，不触发自动回复（设计 §6
  // 「新启用只建基线，不回复旧消息」）；空窗口同样构成可恢复的空基线。
  if (request.watermark === null) {
    return {
      kind: 'baseline',
      historical_count: observation.ordered_messages.length,
      watermark: {
        last_local_message_id: observation.ordered_messages[observation.ordered_messages.length - 1]?.local_message_id ?? null,
        window_fingerprint: observation.window_fingerprint as string,
      },
    }
  }
  const last = observation.ordered_messages[observation.ordered_messages.length - 1]
  if (last !== undefined) {
    return {
      kind: 'new_messages',
      count: observation.ordered_messages.length,
      watermark: {
        last_local_message_id: last.local_message_id,
        window_fingerprint: observation.window_fingerprint as string,
      },
    }
  }
  // 空的 complete_window：水位锚点不变（沿用请求锚点；空基线保持 null），
  // 指纹刷新为本轮观察值。重启后按同锚点重放不得重复接纳。
  return {
    kind: 'no_change',
    watermark: {
      last_local_message_id: request.watermark?.last_local_message_id ?? null,
      window_fingerprint: observation.window_fingerprint as string,
    },
  }
}
