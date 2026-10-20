import assert from 'node:assert/strict'
import test from 'node:test'
import { PagePostconditionError, runVerifiedPageOperation, operationRecoveryFailure, operationRecoveryFacts } from '../src/main/operations/pageOperation.js'
import { CodedOperationError, type OpContext } from '../src/main/operations/types.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { createBossGotoOperation } from '../src/main/operations/bossGoto.js'
import { createBossOpenChatOperation } from '../src/main/operations/bossOpenChat.js'
import { createBossReadChatOperation } from '../src/main/operations/bossReadChat.js'
import { createBossListJobsOperation } from '../src/main/operations/bossListJobs.js'
import { createBossSelectJobOperation } from '../src/main/operations/bossSelectJob.js'
import { createBossFilterOptionsOperation } from '../src/main/operations/bossFilterOptions.js'
import { createBossCloseDetailOperation } from '../src/main/operations/bossCloseDetail.js'
import { createBossResumeDetailOperation } from '../src/main/operations/bossResumeDetail.js'
import { createBossResumeBatchOperation } from '../src/main/operations/bossResumeBatch.js'

const intent = { name: 'boss_select_job', operationClass: 'page_state' as const, goal: '切职', expected: '当前职位框等于目标' }
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })

test('only a definite readable negative result retries once with fresh body state', async () => {
  let inputs = 0
  const result = await runVerifiedPageOperation(intent, async (attempt) => {
    await attempt.input(async () => { inputs++ })
    attempt.verified({ status: inputs === 1 ? 'unmet' : 'met', evidence: ['当前职位框可读'] })
    return inputs
  })
  assert.equal(inputs, 2)
  assert.deepEqual(result.facts.execution, { stage: 'postcheck', input_status: 'attempted', attempts_used: 2, ordinary_retries_used: 1 })
})

test('direct input failure goes to shared recovery with zero ordinary retries', async () => {
  let inputs = 0
  await assert.rejects(runVerifiedPageOperation(intent, async (attempt) => {
    await attempt.input(async () => { inputs++; throw new WinClickError('窗口被遮挡', 2) })
  }), (error: unknown) => {
    assert.ok(error instanceof CodedOperationError)
    const facts = error.data!.operation_recovery as any
    assert.equal(facts.execution.attempts_used, 1)
    assert.equal(facts.execution.ordinary_retries_used, 0)
    assert.equal(facts.verification.status, 'inconclusive')
    assert.equal(facts.execution.input_status, 'not_started')
    return true
  })
  assert.equal(inputs, 1)
})

test('unknown postcondition is distinct from known failure and does not resend input', async () => {
  let inputs = 0
  await assert.rejects(runVerifiedPageOperation(intent, async (attempt) => {
    await attempt.input(async () => { inputs++ })
    attempt.verified({ status: 'inconclusive', evidence: ['职位框不可读'] })
  }), (error: unknown) => {
    assert.ok(error instanceof CodedOperationError)
    assert.equal((error.data!.operation_recovery as any).verification.status, 'inconclusive')
    return true
  })
  assert.equal(inputs, 1)
})

test('existing internal fallback consumes the same retry budget', async () => {
  let bodies = 0
  await assert.rejects(runVerifiedPageOperation(intent, async (attempt) => {
    bodies++
    assert.equal(attempt.retry(), true)
    assert.equal(attempt.retry(), false)
    throw new PagePostconditionError('直跳仍未到达', { status: 'unmet', evidence: ['当前URL仍不匹配'] })
  }), (error: unknown) => {
    assert.ok(error instanceof CodedOperationError)
    assert.equal((error.data!.operation_recovery as any).execution.ordinary_retries_used, 1)
    return true
  })
  assert.equal(bodies, 1)
})

test('external business unknown never acquires a resend from the page helper', async () => {
  let inputs = 0
  await assert.rejects(runVerifiedPageOperation({ ...intent, operationClass: 'external_effect' }, async (attempt) => {
    await attempt.input(async () => { inputs++ })
    attempt.verified({ status: 'unmet', evidence: ['页面仍为原状态，无法证明外发没发生'] })
  }), (error: unknown) => error instanceof CodedOperationError && error.code === 'EXECUTION_UNKNOWN')
  assert.equal(inputs, 1)
})

test('known pre-submit business state does not turn harmless prepare input into unknown send', () => {
  const facts = operationRecoveryFacts({ ...intent, operationClass: 'external_effect' }, { attemptsUsed: 1, ordinaryRetriesUsed: 0,
    inputStatus: 'attempted', stage: 'input', verification: { status: 'inconclusive', evidence: [] } })
  facts.business_effect = { status: 'not_started', completed_count: 0 }
  assert.throws(() => operationRecoveryFailure(new CodedOperationError('UI_CHANGED', '准备失败'), facts),
    (error: unknown) => error instanceof CodedOperationError && error.code === 'UI_CHANGED')
})

const cases = [
  ['boss_goto', createBossGotoOperation, { target: 'chat' }, 'recommend'],
  ['boss_open_chat', createBossOpenChatOperation, { contact: '张三' }, 'chat'],
  ['boss_read_chat', createBossReadChatOperation, { contact: '张三' }, 'chat'],
  ['boss_list_jobs', createBossListJobsOperation, {}, 'recommend'],
  ['boss_select_job', createBossSelectJobOperation, { job_name: '开发' }, 'recommend'],
  ['boss_filter_options', createBossFilterOptionsOperation, {}, 'recommend'],
  ['boss_close_detail', createBossCloseDetailOperation, {}, 'recommend'],
  ['boss_resume_detail', createBossResumeDetailOperation, { candidate_name: '张三' }, 'recommend'],
  ['boss_resume_batch', createBossResumeBatchOperation, {}, 'recommend'],
] as const
for (const [name, create, args, page] of cases) test(`${name} emits canonical recovery facts for a direct page read error`, async () => {
  let inputs = 0
  const session: BossSession = {
    snapshot: async () => { throw new CodedOperationError('UI_CHANGED', '页面布局受阻') },
    click: async () => { inputs++ }, clickBrowse: async () => { inputs++ },
    clickAndType: async () => { inputs++ }, pressEscape: async () => { inputs++ },
    mouseWheel: async () => { inputs++ }, captureFullpage: async () => Buffer.alloc(0),
    getUrl: async () => `https://www.zhipin.com/web/chat/${page === 'chat' ? 'index' : 'recommend'}`, close: async () => {},
  }
  const result = await (create(async () => session) as any).execute(args, ctx())
  assert.equal(result.success, false)
  const facts = result.data.operation_recovery as any
  assert.equal(facts.operation.name, name)
  assert.equal(facts.execution.attempts_used, 1)
  assert.equal(facts.execution.ordinary_retries_used, 0)
  assert.equal(facts.verification.status, 'inconclusive')
  assert.equal(inputs, 0)
})


test('later zero-input refusal cannot erase a preceding successful input', async () => {
  await assert.rejects(runVerifiedPageOperation(intent, async (attempt) => {
    await attempt.input(async () => {})
    await attempt.input(async () => { throw new WinClickError('后续落点拒绝', 2) })
  }), (error: unknown) => {
    assert.ok(error instanceof CodedOperationError)
    assert.equal((error.data!.operation_recovery as any).execution.input_status, 'attempted')
    return true
  })
})


test('retry refusal preserves the first completed input in the command history', async () => {
  let calls = 0
  await assert.rejects(runVerifiedPageOperation(intent, async (attempt) => {
    await attempt.input(async () => {
      if (++calls === 2) throw new WinClickError('重试落点拒绝', 2)
    })
    attempt.verified({ status: 'unmet', evidence: ['首轮当前框仍为旧值'] })
  }), (error: unknown) => {
    assert.ok(error instanceof CodedOperationError)
    const facts = error.data!.operation_recovery as any
    assert.equal(facts.execution.input_status, 'attempted')
    assert.equal(facts.execution.attempts_used, 2)
    assert.equal(facts.execution.ordinary_retries_used, 1)
    return true
  })
  assert.equal(calls, 2)
})
