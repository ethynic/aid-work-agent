/**
 * 结构化 operation 统一契约（实施规格 m02 §2 / 设计 §10.3）。
 *
 * - operation 永不 reject：参数错误也返回 success=false + INVALID_ARGUMENT；
 *   只有编程错误才允许抛出。
 * - 参数校验在 connect Chrome 之前完成（fail fast）。
 * - CLI renderer 与 MCP tool handler 共用同一 operation，operation 不读 stdin、
 *   不调 process.exit、不依赖 console 文本作为结果。
 */

/** 写动作副作用语义 */
export type Effect = 'none' | 'applied' | 'partial' | 'unknown'

/** 稳定错误码（Host 不得解析自然语言） */
export type ErrorCode =
  | 'INVALID_ARGUMENT'
  | 'CHROME_UNAVAILABLE'
  | 'NOT_LOGGED_IN'
  | 'WRONG_PAGE'
  | 'BUSY'
  | 'PAYWALL'
  | 'UI_CHANGED'
  | 'CANCELLED'
  | 'EXECUTION_UNKNOWN'
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
  /** connect / attach / navigate / execute / done */
  stage: string
  current?: number
  total?: number
  message: string
}

export interface OpContext {
  signal: AbortSignal
  progress: (p: ProgressEvent) => void
  cdpPort?: number
}

export interface BossOperation<Args> {
  /** boss_greet 等稳定 snake_case 名 */
  name: string
  execute(args: Args, ctx: OpContext): Promise<OperationResult>
}

/** 协作式取消：executor 循环顶部 / 入口检查 signal 后抛出，由 errorMapping 映射为 CANCELLED */
export class CancelledError extends Error {
  constructor(message = '操作已取消') {
    super(message)
    this.name = 'CancelledError'
  }
}

/** operation 内部主动判定失败的错误（如 greet 前置校验非推荐页 → WRONG_PAGE），errorMapping 直接采用其 code */
export class CodedOperationError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
  ) {
    super(message)
    this.name = 'CodedOperationError'
  }
}

/** 可重试的错误码（规格 §2：CHROME_UNAVAILABLE/WRONG_PAGE/BUSY 可重试；写动作 unknown/partial 一律 false） */
const RETRYABLE_CODES: ReadonlySet<ResultCode> = new Set(['CHROME_UNAVAILABLE', 'WRONG_PAGE', 'BUSY'])

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
 * 写动作 effect 计算（规格 §2/§3）：
 * - 成功：有完成量=applied，无完成量（如已到底 0 人）=none
 * - EXECUTION_UNKNOWN：写动作发出但校验失败=unknown
 * - INTERNAL_ERROR：未预期异常，写动作是否落地不可知=unknown（规格 §3 兜底行）
 * - PAYWALL/CANCELLED：按已完成量 none/partial
 * - 其余失败：none
 */
export function writeEffect(success: boolean, code: ResultCode, completed: number): Effect {
  if (success) return completed > 0 ? 'applied' : 'none'
  if (code === 'EXECUTION_UNKNOWN' || code === 'INTERNAL_ERROR') return 'unknown'
  if ((code === 'PAYWALL' || code === 'CANCELLED') && completed > 0) return 'partial'
  return 'none'
}
