/**
 * 结构化 operation 统一契约（与 weixin-cli 同源，设计见
 * docs/research/wecom-cli/probe-and-design-20260829.md §3.1）。
 *
 * - operation 永不 reject：参数错误也返回 success=false + INVALID_ARGUMENT；
 *   只有编程错误才允许抛出。
 * - CLI renderer 与 MCP tool handler 共用同一 operation，operation 不读 stdin、
 *   不调 process.exit、不依赖 console 文本作为结果。
 */

/** 写动作副作用语义 */
export type Effect = 'none' | 'applied' | 'partial' | 'unknown'

/** 稳定错误码（Host 不得解析自然语言） */
export type ErrorCode =
  // 输入
  | 'INVALID_ARGUMENT'
  // 配置（OCR 环境缺失等）
  | 'CONFIG_MISSING'
  // 环境
  | 'WINDOWS_REQUIRED'
  | 'INTERACTIVE_SESSION_REQUIRED'
  // 企业微信客户端/账号状态
  | 'WECOM_NOT_FOUND'
  | 'NOT_LOGGED_IN'
  | 'WINDOW_AMBIGUOUS'
  // 前台/窗口安全
  | 'FOREGROUND_LOST'
  // 页面/内容
  | 'UI_CHANGED'
  | 'RESULT_TIMEOUT'
  | 'CONTENT_UNAVAILABLE'
  // 客户检索
  | 'CUSTOMER_NOT_FOUND'
  // 目标定位（M2 search/send）
  | 'TARGET_NOT_FOUND'
  | 'TARGET_AMBIGUOUS'
  | 'TARGET_REF_STALE'
  // 生命周期
  | 'BUSY'
  | 'CANCELLED'
  // 写动作
  | 'EXECUTION_UNKNOWN'
  // 兜底
  | 'INTERNAL_ERROR'

export type ResultCode = 'OK' | ErrorCode

export interface OperationResult {
  success: boolean
  code: ResultCode
  /** 面向人的简短中文结果 */
  message: string
  effect: Effect
  data: Record<string, unknown>
  retryable: boolean
  /** operation 入口生成（crypto.randomUUID），贯穿日志/审计 */
  run_id: string
}

export interface ProgressEvent {
  /** check / execute / cleanup / done 等阶段名 */
  stage: string
  current?: number
  total?: number
  message: string
}

export interface OpContext {
  signal: AbortSignal
  progress: (p: ProgressEvent) => void
}

export interface WecomOperation<Args> {
  /** wecom_probe 等稳定 snake_case 名 */
  name: string
  execute(args: Args, ctx: OpContext): Promise<OperationResult>
}

/** 协作式取消：executor 检查点 / 入口检查 signal 后抛出，由 errorMapping 映射为 CANCELLED */
export class CancelledError extends Error {
  constructor(message = '操作已取消') {
    super(message)
    this.name = 'CancelledError'
  }
}

/** operation 内部主动判定失败的错误（如非 win32 → WINDOWS_REQUIRED），errorMapping 直接采用其 code */
export class CodedOperationError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    /** 写动作失败时的 effect 显式覆盖（如取消发生在动作发出后且无法确认 → CANCELLED/unknown） */
    readonly effectOverride?: Effect,
  ) {
    super(message)
    this.name = 'CodedOperationError'
  }
}

/**
 * 可重试错误码（retryable 只对写动作开始前的环境型失败开放；
 * 写动作 unknown/partial 一律 false）。
 */
const RETRYABLE_CODES: ReadonlySet<ResultCode> = new Set(['BUSY', 'WECOM_NOT_FOUND', 'NOT_LOGGED_IN'])

export function okResult(runId: string, message: string, effect: Effect, data: Record<string, unknown> = {}): OperationResult {
  return { success: true, code: 'OK', message, effect, data, retryable: false, run_id: runId }
}

export function failResult(
  runId: string,
  code: ErrorCode,
  message: string,
  effect: Effect,
  data: Record<string, unknown> = {},
): OperationResult {
  return { success: false, code, message, effect, data, retryable: RETRYABLE_CODES.has(code), run_id: runId }
}

/**
 * 写动作 effect 计算：
 * - 成功：有完成量=applied，无完成量=none
 * - EXECUTION_UNKNOWN：写动作发出但校验失败=unknown
 * - INTERNAL_ERROR：未预期异常，写动作是否落地不可知=unknown（兜底，不可误报 none）
 * - CANCELLED：按已确认完成量 none/partial（动作发出后取消且无法确认时，operation 应显式覆盖为 unknown）
 * - 其余失败：none
 */
export function writeEffect(success: boolean, code: ResultCode, completed: number): Effect {
  if (success) return completed > 0 ? 'applied' : 'none'
  if (code === 'EXECUTION_UNKNOWN' || code === 'INTERNAL_ERROR') return 'unknown'
  if (code === 'CANCELLED' && completed > 0) return 'partial'
  return 'none'
}
