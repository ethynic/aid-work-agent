/** Shared domain facts; the CLI recovery wrapper owns repair and replay policy. */
import { WinClickError } from '../input/WinMouseClicker.js'
import { mapExecutorError } from './errorMapping.js'
import { CodedOperationError } from './types.js'

export type PageVerification = { status: 'met' | 'unmet' | 'inconclusive'; evidence: string[] }
export type PageExecution = {
  attemptsUsed: number; ordinaryRetriesUsed: number
  inputStatus: 'not_started' | 'attempted' | 'unknown'
  stage: 'prepared' | 'input' | 'postcheck'
  verification: PageVerification
}

export interface RecoveryIntent {
  name: string
  goal: string
  expected: string
  operationClass: 'page_state' | 'readonly' | 'external_effect'
  resultKind?: 'page_state' | 'business_result'
  target?: string
}

/** A completed input with a readable negative postcondition, never an arbitrary input error. */
export class PagePostconditionError extends CodedOperationError {
  constructor(message: string, readonly verification: PageVerification) {
    super('UI_CHANGED', message)
  }
}

export interface PageAttempt {
  readonly execution: PageExecution
  input<T>(action: () => Promise<T>): Promise<T>
  verified(verification: PageVerification): void
  /** Share the one retry with established executor fallback paths. */
  retry(): boolean
}

/** Domain commands supply their own signals. Only a typed definite negative spends one retry. */
export async function runVerifiedPageOperation<T>(
  intent: RecoveryIntent,
  body: (attempt: PageAttempt) => Promise<T>,
  previousExecution?: PageExecution,
): Promise<{ value: T; facts: Record<string, unknown> }> {
  const execution: PageExecution = { attemptsUsed: previousExecution?.attemptsUsed ?? 1,
    ordinaryRetriesUsed: previousExecution?.ordinaryRetriesUsed ?? 0,
    inputStatus: previousExecution?.inputStatus ?? 'not_started', stage: 'prepared', verification: { status: 'inconclusive', evidence: [] } }
  const attempt: PageAttempt = {
    execution,
    async input(action) {
      const previous = execution.inputStatus
      execution.stage = 'input'
      execution.inputStatus = 'unknown'
      try {
        const result = await action()
        execution.inputStatus = 'attempted'
        return result
      } catch (error) {
        if (error instanceof WinClickError && error.exitCode === 2 && !error.message.includes('[BOSS_INPUT_UNKNOWN]')) {
          execution.inputStatus = previous
        }
        throw error
      }
    },
    verified(verification) {
      execution.stage = 'postcheck'
      execution.verification = verification
      if (verification.status !== 'met') throw new PagePostconditionError('页面操作后验未满足或无法确认', verification)
    },
    retry() {
      if (execution.ordinaryRetriesUsed !== 0 || intent.operationClass === 'external_effect') return false
      execution.attemptsUsed++
      execution.ordinaryRetriesUsed++
      return true
    },
  }
  for (;;) {
    try {
      const value = await body(attempt)
      if (execution.verification.status !== 'met') {
        throw new PagePostconditionError('操作返回但未提供可靠成功证据', execution.verification)
      }
      return { value, facts: operationRecoveryFacts(intent, execution) }
    } catch (error) {
      if (error instanceof PagePostconditionError) {
        execution.stage = 'postcheck'
        execution.verification = error.verification
        if (error.verification.status === 'unmet' && execution.ordinaryRetriesUsed === 0
          && intent.operationClass !== 'external_effect') {
          execution.attemptsUsed++
          execution.ordinaryRetriesUsed++
          execution.stage = 'prepared'
          execution.verification = { status: 'inconclusive', evidence: [] }
          continue
        }
      }
      return operationRecoveryFailure(error, operationRecoveryFacts(intent, execution))
    }
  }
}

/** A read result survives cleanup failure; local recovery repairs only the page without repeating the read. */
export function retainedReadCleanupFailure(error: unknown, intent: RecoveryIntent, execution: PageExecution) {
  const mapped = mapExecutorError(error)
  const saved = error instanceof CodedOperationError ? error.data?.operation_recovery : undefined
  const facts = saved && typeof saved === 'object' ? saved as Record<string, unknown>
    : operationRecoveryFacts(intent, { ...execution, verification: { status: 'inconclusive', evidence: ['已取得读取结果，收尾状态尚未确认'] } })
  return { facts: { ...facts, operation: { ...facts.operation as Record<string, unknown>, result_kind: 'business_result' },
    error: { code: mapped.code, source: (facts.execution as Record<string, unknown>).stage, summary: mapped.message.slice(0, 1000) } },
  cleanupError: { code: mapped.code, message: mapped.message } }
}

export function pageFacts(name: string, goal: string, expected: string, execution: PageExecution, target?: string): Record<string, unknown> {
  return operationRecoveryFacts({ name, goal, expected, operationClass: 'page_state', target }, execution)
}

export function operationRecoveryFacts(intent: RecoveryIntent, execution: PageExecution): Record<string, unknown> {
  return {
    schema_version: 1,
    operation_class: intent.operationClass,
    operation: { name: intent.name, goal: intent.goal, expected: intent.expected,
      result_kind: intent.resultKind ?? (intent.operationClass === 'external_effect' ? 'business_result' : 'page_state'),
      ...(intent.target ? { target_label: intent.target } : {}) },
    execution: { stage: execution.stage, input_status: execution.inputStatus,
      attempts_used: Math.max(1, execution.attemptsUsed), ordinary_retries_used: execution.ordinaryRetriesUsed },
    verification: { status: ({ met: 'satisfied', unmet: 'not_satisfied', inconclusive: 'inconclusive' } as const)[execution.verification.status],
      evidence: execution.verification.evidence },
  }
}

export function pageFailure(error: unknown, facts: Record<string, unknown>): never {
  return operationRecoveryFailure(error, facts)
}

export function operationRecoveryFailure(error: unknown, facts: Record<string, unknown>, data: Record<string, unknown> = {}): never {
  const mapped = mapExecutorError(error)
  // Invalid intent, cancellation, unavailable transport and business preconditions cannot be fixed by a page click.
  if (!['UI_CHANGED', 'EXECUTION_UNKNOWN', 'BUSY', 'INTERNAL_ERROR', 'CHROME_UNAVAILABLE'].includes(mapped.code)) throw error
  if (mapped.code === 'INTERNAL_ERROR' && !(error instanceof WinClickError)) throw error
  const execution = facts.execution as Record<string, unknown>
  const business = facts.business_effect as { status?: unknown; completed_count?: unknown } | undefined
  const externalUnknown = facts.operation_class === 'external_effect' && (business
    ? business.status === 'unknown' : execution.input_status === 'attempted')
  const code = execution.input_status === 'unknown' || externalUnknown
    ? 'EXECUTION_UNKNOWN' : mapped.code === 'CHROME_UNAVAILABLE' ? 'CHROME_UNAVAILABLE' : 'UI_CHANGED'
  // Typed execution facts replace legacy blanket instructions to stop and ask a human.
  const message = error instanceof Error ? error.message : mapped.message
  const recovery = { ...facts, error: { code, source: execution.stage, summary: message.slice(0, 1000) } }
  throw new CodedOperationError(code, message, {
    ...data, operation_recovery: recovery,
    ...(facts.operation_class === 'page_state' ? { page_operation: recovery } : {}),
  })
}
