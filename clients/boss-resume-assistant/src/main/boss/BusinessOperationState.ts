/** Execution facts shared by external actions; this class never retries or recovers. */
import { operationRecoveryFacts, operationRecoveryFailure, PagePostconditionError, type PageExecution, type RecoveryIntent } from '../operations/pageOperation.js'
import { WinClickError } from '../input/WinMouseClicker.js'
import { createHash } from 'node:crypto'
import { classOf, indexedValues, isDescendantOf, type DomSnapshot } from './domSnapshot.js'
import { headerContactOf } from './ChatReadExecutor.js'
import { CodedOperationError } from '../operations/types.js'
import { mapExecutorError } from '../operations/errorMapping.js'

export class BusinessOperationState {
  execution: PageExecution = { attemptsUsed: 1, ordinaryRetriesUsed: 0, inputStatus: 'not_started', stage: 'prepared',
    verification: { status: 'inconclusive', evidence: ['最终业务操作尚未确认'] } }
  businessEffect: { status: 'not_started' | 'confirmed' | 'unknown'; completed_count: number } = { status: 'not_started', completed_count: 0 }
  readonly confirmedItems: Record<string, unknown>[] = []
  contactRef?: string
  contactName?: string

  startItem(): void {
    this.contactRef = undefined
    this.contactName = undefined
    this.businessEffect.status = this.businessEffect.completed_count > 0 ? 'confirmed' : 'not_started'
    this.execution.verification = { status: 'inconclusive', evidence: ['下一目标业务操作尚未确认'] }
  }

  bindContact(snapshot: DomSnapshot, expected?: string): void {
    const ref = chatContactRef(snapshot)
    if (!ref || expected && expected !== ref || this.contactRef && this.contactRef !== ref) {
      throw new CodedOperationError('UI_CHANGED', '当前会话身份无法确认或与原操作绑定不一致，未继续业务提交')
    }
    this.contactRef = ref
    this.contactName = headerContactOf(snapshot)
  }

  facts(intent: RecoveryIntent): Record<string, unknown> {
    return { ...operationRecoveryFacts({ ...intent, target: intent.target ?? this.contactName }, this.execution),
      ...(intent.operationClass === 'external_effect' ? { business_effect: { ...this.businessEffect } } : {}),
      ...(this.contactRef ? { recovery_contact_ref: this.contactRef } : {}) }
  }

  failure(error: unknown, intent: RecoveryIntent, data: Record<string, unknown> = {}): never {
    if (error instanceof PagePostconditionError) {
      this.execution.stage = 'postcheck'
      this.execution.verification = error.verification
    }
    if (this.businessEffect.status === 'unknown' && !['CANCELLED', 'PAYWALL'].includes(mapExecutorError(error).code)) {
      error = new CodedOperationError('EXECUTION_UNKNOWN', error instanceof Error ? error.message : '最终业务操作结果未知')
    }
    const mapped = mapExecutorError(error)
    if (this.confirmedItems.length && ['CANCELLED', 'PAYWALL'].includes(mapped.code)) {
      throw new CodedOperationError(mapped.code, mapped.message, { ...data,
        operation_recovery: this.facts(intent), confirmed_items: [...this.confirmedItems] })
    }
    return operationRecoveryFailure(error, this.facts(intent), { ...data, confirmed_items: [...this.confirmedItems] })
  }

  async input<T>(action: () => Promise<T>, final = false): Promise<T> {
    const priorBusinessStatus = this.businessEffect.status
    this.execution.stage = 'input'
    this.execution.inputStatus = 'attempted'
    if (final) {
      this.businessEffect.status = 'unknown'
      this.execution.verification = { status: 'inconclusive', evidence: ['最终业务输入已尝试，尚未取得当前目标后验'] }
    }
    try {
      const value = await action()
      if (final) this.execution.stage = 'postcheck'
      return value
    } catch (error) {
      // Only the native pre-input guard can certify that no physical final input was issued.
      const refused = error instanceof WinClickError && !error.message.includes('[BOSS_INPUT_UNKNOWN]')
        && (error.exitCode === 2 || /\[BOSS_POWERSHELL_(?:PREPARE|LAUNCH)\]/.test(error.message))
      if (final && refused) this.businessEffect.status = priorBusinessStatus
      if (final && !refused) this.execution.inputStatus = 'unknown'
      throw error
    }
  }

  confirmed(item: Record<string, unknown>, evidence: string): void {
    this.businessEffect.status = 'confirmed'
    this.businessEffect.completed_count++
    this.confirmedItems.push(item)
    this.execution.stage = 'postcheck'
    this.execution.verification = { status: 'met', evidence: [evidence] }
  }

  unconfirmed(evidence: string): void {
    this.execution.stage = 'postcheck'
    this.execution.verification = { status: 'inconclusive', evidence: [evidence] }
  }

  retry(): boolean {
    if (this.execution.ordinaryRetriesUsed > 0) return false
    this.execution.ordinaryRetriesUsed++
    this.execution.attemptsUsed++
    return true
  }
}

/** Same parser and visible header subtree as chat reading; indices/coordinates are not identities. */
export function chatContactRef(snapshot: DomSnapshot): string | undefined {
  const document = snapshot.documents[0]
  const contact = headerContactOf(snapshot)
  if (!document?.nodes.attributes || !document.nodes.parentIndex || !contact) return undefined
  const roots = indexedValues(document.nodes.attributes, 'attributes').filter(([ni]) =>
    (classOf(snapshot, 0, ni) ?? '').split(/\s+/).includes('base-info-single-container')).map(([ni]) => ni)
  if (roots.length !== 1) return undefined
  const texts = indexedValues(document.nodes.nodeValue, 'nodeValue').filter(([ni, si]) => si >= 0
    && isDescendantOf(document, ni, roots[0]!) === true).sort(([a], [b]) => a - b)
    .map(([, si]) => snapshot.strings[si]?.trim()).filter(Boolean)
  // Activity, consent and rejection labels change during the operation. Only demographic identity
  // fields take part in the fallback binding, with the same name parser as chat reading.
  const identity = texts.filter(t => /^\d+岁$|^\d+(?:-\d+)?年(?:经验)?$|^(?:应届生|本科|硕士|博士|大专|高中|中专|男|女)$/.test(t!))
  const ids: string[] = []
  for (const [ni, attrs] of indexedValues(document.nodes.attributes, 'attributes')) {
    if (isDescendantOf(document, ni, roots[0]!) !== true) continue
    for (let i = 0; i + 1 < attrs.length; i += 2) {
      if (/^data-(?:geek-id|uid|user-id|encrypt-geek-id)$/.test(snapshot.strings[attrs[i]!] ?? '')) ids.push(snapshot.strings[attrs[i + 1]!] ?? '')
    }
  }
  return createHash('sha256').update(JSON.stringify([contact, identity, ids])).digest('hex')
}

export async function withBusinessRecovery<T extends { data?: Record<string, unknown> }>(state: BusinessOperationState, intent: RecoveryIntent, body: () => Promise<T>): Promise<T> {
  try {
    const result = await body()
    return { ...result, data: { ...result.data, operation_recovery: state.facts(intent), confirmed_items: [...state.confirmedItems] } }
  } catch (error) { return state.failure(error, intent) }
}
