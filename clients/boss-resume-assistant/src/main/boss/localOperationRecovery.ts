/** CLI/MCP share this local recovery owner; backend only diagnoses and bills vision. */
import { randomUUID } from 'node:crypto'
import { createBossOverlayInspectOperation } from '../operations/bossOverlayInspect.js'
import { createBossOverlayDismissOperation } from '../operations/bossOverlayDismiss.js'
import { failResult, type BossOperation, type OpContext, type OperationResult } from '../operations/types.js'
import { validateVisualArgs, type VisualInspectArgs, type VisualContext } from './visualRecovery.js'

type Dict = Record<string, unknown>
type Policy = 'page_state' | 'readonly' | 'observe_only'
export const LOCAL_RECOVERY_POLICIES: Readonly<Record<string, Policy>> = Object.freeze({
  boss_filter: 'page_state', boss_clear_filter: 'page_state', boss_goto: 'page_state',
  boss_open_chat: 'page_state', boss_select_job: 'page_state', boss_open_detail: 'page_state',
  boss_close_detail: 'page_state', boss_interview_demo: 'page_state',
  boss_filter_options: 'readonly', boss_lookup_candidates: 'readonly', boss_read_chat: 'readonly',
  boss_list_jobs: 'readonly', boss_resume_detail: 'readonly', boss_resume_batch: 'readonly', boss_resume_next: 'readonly',
  boss_greet: 'observe_only', boss_greet_selected: 'observe_only', boss_greet_detail: 'observe_only',
  boss_accept_resume: 'observe_only', boss_reject_current: 'observe_only', boss_send_to: 'observe_only', boss_send_current: 'observe_only',
})
export interface LocalRecoveryDeps {
  inspect?: BossOperation<VisualInspectArgs>
  dismiss?: BossOperation<{ recovery_ref?: string; text?: string }>
  env?: NodeJS.ProcessEnv
  requestId?: () => string
}
const object = (v: unknown): v is Dict => !!v && typeof v === 'object' && !Array.isArray(v)
const dict = (v: unknown): Dict => object(v) ? v : {}
const CODES = new Set(['UI_CHANGED', 'BUSY', 'EXECUTION_UNKNOWN', 'CHROME_UNAVAILABLE'])
const CONTACT_TOOLS = new Set(['boss_send_current', 'boss_reject_current', 'boss_accept_resume'])
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/
interface Admitted { facts: Dict; context: VisualContext; policy: Policy | 'preserve_result'; target: string }

function admit(name: string, result: OperationResult): Admitted | undefined {
  const policy = LOCAL_RECOVERY_POLICIES[name]
  const f = dict(result.data.operation_recovery), op = dict(f.operation), ex = dict(f.execution), verify = dict(f.verification)
  const expectedClass = policy === 'observe_only' ? 'external_effect' : policy
  const target = result.data.browser_target_ref
  if (!policy || f.schema_version !== 1 || f.operation_class !== expectedClass || op.name !== name
    || typeof target !== 'string' || !UUID.test(target) || f.browser_target_ref !== undefined && f.browser_target_ref !== target) return
  if (!['prepared', 'input', 'postcheck'].includes(String(ex.stage)) || !['not_started', 'attempted', 'unknown'].includes(String(ex.input_status))
    || !Number.isInteger(ex.attempts_used) || Number(ex.attempts_used) < 1 || !Number.isInteger(ex.ordinary_retries_used)
    || Number(ex.ordinary_retries_used) < 0 || Number(ex.ordinary_retries_used) >= Number(ex.attempts_used)
    || !['satisfied', 'not_satisfied', 'inconclusive'].includes(String(verify.status))
    || !Array.isArray(verify.evidence) || verify.evidence.length > 10
    || verify.evidence.some(v => typeof v !== 'string' || !v || v.length > 500)
    || verify.status !== 'inconclusive' && !verify.evidence.length) return
  if (['goal', 'expected'].some(k => typeof op[k] !== 'string' || !op[k] || String(op[k]).length > 1000)
    || op.target_label !== undefined && (typeof op.target_label !== 'string' || !op.target_label || op.target_label.length > 200)) return
  if (policy === 'page_state' && op.result_kind !== 'page_state' || policy === 'observe_only' && op.result_kind !== 'business_result'
    || policy === 'readonly' && !['page_state', 'business_result'].includes(String(op.result_kind))) return
  const retained = policy === 'readonly' && op.result_kind === 'business_result'
  if (retained && !result.success) return
  const code = result.success ? dict(f.error).code : result.code
  if (!CODES.has(String(code)) || result.success && !retained && policy !== 'observe_only') return
  const context: VisualContext = { operation: structuredClone(op), execution: structuredClone(ex), verification: structuredClone(verify) }
  if (object(f.error)) context.error = structuredClone(f.error)
  if (retained) dict(context.operation).result_kind = 'page_state'
  // Apply the same API context validation used by the actual observation primitive before any paid request.
  if (validateVisualArgs({ request_id: '00000000-0000-4000-8000-000000000000',
    visual_context: { ...context, purpose: 'diagnose', available_actions: ['dismiss_click'] } })) return
  return { facts: f, context, policy: retained ? 'preserve_result' : policy, target }
}

/** Domain executors already own their one ordinary retry. This wrapper never adds another. */
export function withLocalRecovery<Args>(operation: BossOperation<Args>, deps: LocalRecoveryDeps = {}): BossOperation<Args> {
  if (!LOCAL_RECOVERY_POLICIES[operation.name]) return operation
  return { name: operation.name, async execute(args, ctx) {
    let original: OperationResult
    try { original = await operation.execute(args, ctx) }
    catch { original = failResult(randomUUID(), 'INTERNAL_ERROR', '操作未取得结构化结果，执行结果未知', 'unknown') }
    const receipts: unknown[] = [], observations: Dict[] = [], steps: Dict[] = []
    const seen = new Set<string>()
    let policy: string = LOCAL_RECOVERY_POLICIES[operation.name]!
    const finish = (result: OperationResult, state: string, extra: Dict = {}): OperationResult => {
      // Old servers only accept schema 1 for server-owned recovery. Version 2 also blocks their legacy fallback.
      const data = { ...result.data, operation_recovery: { ...dict(result.data.operation_recovery), schema_version: 2, owner: 'cli' } }
      if (object(result.data.page_operation)) Object.assign(data, { page_operation: { ...result.data.page_operation, schema_version: 2, owner: 'cli' } })
      return { ...result, data: { ...data, visual_recovery: { schema_version: 1, owner: 'cli', state, receipts, observations, steps,
        original_run_id: original.run_id, original_code: original.code, original_effect: original.effect,
        original_execution: dict(original.data.operation_recovery).execution, policy, ...extra } } }
    }
    if (ctx.signal.aborted || original.code === 'CANCELLED') return finish(original, 'cancelled')
    const admitted = admit(operation.name, original)
    if (!admitted || ctx.browserTargetRef && ctx.browserTargetRef !== admitted.target) return finish(original, original.success ? 'not_needed' : 'not_eligible')
    policy = admitted.policy
    const env = deps.env ?? process.env
    if (!env.AID_BOSS_SERVER_URL?.trim() || !env.AID_BOSS_SERVER_TOKEN?.trim()) return finish(original, 'not_configured',
      { message: 'CLI视觉恢复未配置：需要AID_BOSS_SERVER_URL与AID_BOSS_SERVER_TOKEN；保留原操作结果' })
    const inspect = deps.inspect ?? createBossOverlayInspectOperation()
    const dismiss = deps.dismiss ?? createBossOverlayDismissOperation()
    const boundCtx: OpContext = { ...ctx, browserTargetRef: admitted.target }
    let context = admitted.context, mode: 'page' | 'window' = 'page'
    const abort = () => { if (ctx.signal.aborted) throw new Error('cancelled') }
    const assess = (r: OperationResult) => {
      const data = r.data, visual = dict(data.visual)
      return { data, visual, diagnosis: visual.http_status === 200 && visual.state === 'completed' && object(visual.diagnosis) ? visual.diagnosis : undefined, result: r }
    }
    const observe = async (branch: string, visualContext: VisualContext, observationMode: 'page' | 'window') => {
      abort()
      const id = (deps.requestId ?? randomUUID)()
      let a = assess(await inspect.execute({ request_id: id, visual_context: visualContext, observation_mode: observationMode }, boundCtx))
      const receipt = () => { if (a.visual.receipt && !seen.has(id)) { receipts.push(a.visual.receipt); seen.add(id) } }
      receipt()
      for (let i = 0; i < 2 && (['pending', 'unknown'].includes(String(a.visual.state)) || a.visual.http_status === 202); i++) {
        abort()
        a = assess(await inspect.execute({ request_id: id, retrieve_only: true }, boundCtx))
        receipt()
      }
      abort()
      observations.push({ branch, mode: observationMode, ...Object.fromEntries(['request_id', 'http_status', 'state', 'diagnosis', 'diagnosis_error', 'action_error'].map(k => [k, a.visual[k]])) })
      return a
    }
    const resume = async (): Promise<OperationResult> => {
      abort()
      if (policy === 'preserve_result') return finish(original, 'page_restored_result_preserved', { page_restored: true })
      const op = dict(context.operation)
      if (policy === 'readonly' && operation.name === 'boss_read_chat' && !op.target_label) return finish(original, 'page_restored_target_unbound', { page_restored: true })
      const recordedContact = admitted.facts.recovery_contact_ref, expectedContact = boundCtx.expectedContactRef
      const contact = recordedContact === undefined ? expectedContact : recordedContact
      const contactBound = typeof contact === 'string' && /^[a-f0-9]{64}$/.test(contact)
        && (recordedContact === undefined || expectedContact === undefined || recordedContact === expectedContact)
      if (policy === 'observe_only') {
        const business = dict(admitted.facts.business_effect)
        const safe = business.status === 'not_started' && business.completed_count === 0 && original.effect === 'none'
          && original.code !== 'EXECUTION_UNKNOWN' && (!CONTACT_TOOLS.has(operation.name) || contactBound)
        if (!safe) return finish(original, business.status === 'confirmed' ? 'page_restored_business_preserved' : 'page_restored_business_unconfirmed', { page_restored: true })
      }
      const resumeArgs = operation.name === 'boss_read_chat' && !dict(args).contact && op.target_label
        ? { ...args, contact: op.target_label } : { ...args }
      const resumeCtx = { ...boundCtx, ...(typeof contact === 'string' ? { expectedContactRef: contact } : {}) }
      // Raw operation only: do not enter another recovery cycle after the original goal is resumed.
      let result: OperationResult
      try { result = await operation.execute(resumeArgs, resumeCtx) }
      catch {
        // The resumed call may have crossed its commit boundary. Never resurrect the earlier not_started proof.
        const facts = { ...admitted.facts, execution: { stage: 'input', input_status: 'unknown', attempts_used: 1, ordinary_retries_used: 0 },
          verification: { status: 'inconclusive', evidence: ['恢复后原操作未取得结构化结果，不能确认最终业务状态'] },
          ...(policy === 'observe_only' ? { business_effect: { status: 'unknown', completed_count: 0 } } : {}) }
        result = failResult(randomUUID(), 'EXECUTION_UNKNOWN', '恢复后原操作未取得结构化结果，执行状态未知',
          policy === 'observe_only' ? 'unknown' : 'none', { browser_target_ref: admitted.target, operation_recovery: facts })
      }
      return finish(result, result.success ? 'recovered' : 'original_goal_unconfirmed', { page_restored: true, resumed: true,
        resume_execution: dict(result.data.operation_recovery).execution })
    }
    const appendEvidence = (evidence: string) => { context = { ...context, verification: {
      ...dict(context.verification), evidence: [...dict(context.verification).evidence as string[], evidence].slice(-10) } } }
    let lastState = 'diagnosis_unavailable'
    try {
      for (let index = 0; index < 3; index++) {
        const diagnoseContext = { ...context, purpose: 'diagnose', available_actions: ['dismiss_click'] }
        let a = await observe(`step${index}.diagnose`, diagnoseContext, mode)
        if (policy === 'observe_only' && a.diagnosis?.obstruction === 'absent' && (mode === 'window' || dict(context.execution).stage !== 'input')) return await resume()
        const captureFailed = !Object.keys(a.visual).length && ['CHROME_UNAVAILABLE', 'UI_CHANGED', 'INTERNAL_ERROR'].includes(a.result.code)
        const unexplained = a.diagnosis && (a.diagnosis.obstruction === 'unknown'
          || dict(context.execution).stage === 'input' && a.diagnosis.obstruction === 'absent'
          || a.diagnosis.goal_state !== 'satisfied' && !a.data.recovery_ref)
        if (mode === 'page' && (captureFailed || unexplained)) { mode = 'window'; a = await observe(`step${index}.diagnose.window`, diagnoseContext, mode) }
        if (!a.diagnosis) return finish(original, 'diagnosis_unavailable')
        if (a.diagnosis.obstruction === 'absent' && (policy === 'observe_only' || a.diagnosis.goal_state === 'satisfied')) return await resume()
        const action = dict(a.diagnosis.proposed_action)
        if (typeof a.data.recovery_ref !== 'string' || action.kind !== 'dismiss' || a.diagnosis.obstruction !== 'present') return finish(original, 'no_supported_action')
        abort()
        const closed = await dismiss.execute({ recovery_ref: a.data.recovery_ref }, boundCtx)
        const reason = closed.data.recovery_action_error
        const unknown = closed.code === 'EXECUTION_UNKNOWN' || reason === 'ALREADY_CONSUMED'
        const step: Dict = { index, mode, action_kind: action.kind, input_status: closed.success ? 'attempted' : unknown ? 'unknown' : 'not_started' }
        steps.push(step)
        abort()
        if (!closed.success && !unknown) {
          step.action_error = reason
          if (reason === 'STALE_OBSERVATION') { lastState = 'observation_changed'; appendEvidence('恢复观察已失效，守卫零输入拒绝；重新采图定位'); continue }
          return finish(original, 'dismiss_unconfirmed')
        }
        const post: VisualContext = { purpose: 'verify', available_actions: [], operation: { name: 'boss_overlay_dismiss', result_kind: 'page_state',
          goal: '核验刚才建议关闭的阻挡界面是否消失', expected: '刚才的阻挡界面及遮罩已消失，露出被遮挡的页面；原操作尚未重新执行' },
        execution: { stage: 'postcheck', input_status: step.input_status, attempts_used: 1, ordinary_retries_used: 0 },
        verification: { status: 'inconclusive', evidence: [closed.success ? '关闭输入正常返回，等待只读核验；原操作尚未重做' : '关闭输入响应未知；只读核验，不重放关闭输入；原操作尚未重做'] } }
        let after = await observe(`step${index}.postcheck`, post, mode)
        const postCaptureFailed = !Object.keys(after.visual).length && ['CHROME_UNAVAILABLE', 'UI_CHANGED', 'INTERNAL_ERROR'].includes(after.result.code)
        if (mode === 'page' && (postCaptureFailed || after.diagnosis?.obstruction === 'unknown')) { mode = 'window'; after = await observe(`step${index}.postcheck.window`, post, mode) }
        step.post_obstruction = after.diagnosis?.obstruction ?? 'unknown'
        step.post_goal_state = after.diagnosis?.goal_state ?? 'inconclusive'
        if (!after.diagnosis || after.diagnosis.obstruction === 'unknown') return finish(original, 'postcheck_inconclusive')
        if (after.diagnosis.obstruction === 'present') {
          lastState = 'obstruction_remains'
          appendEvidence(`恢复步骤${index + 1}动作${String(step.action_kind)}；fresh后态仍有遮挡，选择适合当前状态的动作，不重放旧引用`)
          continue
        }
        if (after.diagnosis.goal_state === 'inconclusive') return finish(original, 'postcheck_inconclusive')
        if (after.diagnosis.obstruction !== 'absent' || after.diagnosis.goal_state !== 'satisfied') return finish(original, 'postcheck_not_satisfied')
        return await resume()
      }
      return finish(original, 'recovery_limit_reached', { last_state: lastState, max_steps: 3 })
    } catch {
      // Recovery errors must never replace completed reads or confirmed/unknown external outcomes.
      return finish(original, ctx.signal.aborted ? 'cancelled' : 'recovery_error')
    }
  } }
}
