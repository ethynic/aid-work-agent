import assert from 'node:assert/strict'
import test from 'node:test'
import { createBossGreetOperation } from '../src/main/operations/bossGreet.js'
import { createBossGreetSelectedOperation } from '../src/main/operations/bossCandidateSelection.js'
import { createBossGreetDetailOperation } from '../src/main/operations/bossGreetDetail.js'
import { createBossSendToOperation } from '../src/main/operations/bossSendTo.js'
import { createBossSendCurrentOperation } from '../src/main/operations/bossSendCurrent.js'
import { createBossAcceptResumeOperation } from '../src/main/operations/bossAcceptResume.js'
import { createBossRejectCurrentOperation } from '../src/main/operations/bossRejectCurrent.js'
import { createBossInterviewDemoOperation } from '../src/main/operations/bossInterviewDemo.js'
import { BusinessOperationState, withBusinessRecovery } from '../src/main/boss/BusinessOperationState.js'
import { ChatRejectExecutor } from '../src/main/boss/ChatRejectExecutor.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { CodedOperationError, type OpContext } from '../src/main/operations/types.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { actionSnapshot, text } from './chatActionFixture.js'

const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const cases = [
  ['boss_greet', createBossGreetOperation, { names: ['合成甲'], limit: 1 }, 'recommend'],
  ['boss_greet_selected', createBossGreetSelectedOperation,
    { name: '合成甲', candidate_ref: `boss-card-v1:${Date.now()}:${'a'.repeat(64)}` }, 'recommend'],
  ['boss_greet_detail', createBossGreetDetailOperation,
    { name: '合成甲', greet_in_detail: true }, 'recommend'],
  ['boss_send_to', createBossSendToOperation, { to: '合成甲', message: '合成消息' }, 'index'],
  ['boss_send_current', createBossSendCurrentOperation, { message: '合成消息' }, 'index'],
  ['boss_accept_resume', createBossAcceptResumeOperation, { limit: 1, preview: false }, 'index'],
  ['boss_reject_current', createBossRejectCurrentOperation, {}, 'index'],
  ['boss_interview_demo', createBossInterviewDemoOperation, { remark: '演示合成内容' }, 'index'],
] as const

for (const [name, create, args, page] of cases) test(`independent ${name}: a real operation receipt permits diagnosis but proves no submission`, async () => {
  let inputs = 0, captures = 0, closes = 0
  const input = async () => { inputs++ }
  const session: BossSession = {
    snapshot: async () => { captures++; throw new CodedOperationError('UI_CHANGED', '合成界面被弹层阻挡') },
    getUrl: async () => `https://www.zhipin.com/web/chat/${page}`,
    click: input, clickBrowse: input, clickAndType: input, mouseWheel: input, pressEscape: input,
    captureFullpage: async () => Buffer.alloc(0), close: async () => { closes++ },
  }
  const operation = create(async () => session)
  const result = await (operation as any).execute(args, ctx())
  assert.equal(result.success, false)
  assert.equal(result.code, 'UI_CHANGED')
  assert.equal(result.effect, 'none')
  assert.equal(inputs, 0)
  assert.equal(captures, 1, 'direct observation errors must bypass the ordinary retry')
  assert.equal(closes, 1)
  const facts = result.data.operation_recovery
  assert.equal(facts.schema_version, 1)
  assert.equal(facts.operation.name, name)
  assert.equal(facts.operation_class, name === 'boss_interview_demo' ? 'page_state' : 'external_effect')
  if (name === 'boss_interview_demo') {
    assert.equal(facts.business_effect, undefined, 'Runner page_state policy must not reject its producer business payload')
  } else assert.deepEqual(facts.business_effect, { status: 'not_started', completed_count: 0 })
  assert.deepEqual(facts.execution, { stage: 'prepared', input_status: 'not_started', attempts_used: 1, ordinary_retries_used: 0 })
  assert.equal(facts.verification.status, 'inconclusive')
  if (name !== 'boss_interview_demo') assert.equal(result.data.page_operation, undefined)
})

test('independent: zero-input final refusal after preparing a draft preserves safe business facts and original identity', async () => {
  const state = new BusinessOperationState()
  state.bindContact(actionSnapshot())
  await state.input(async () => {})
  await assert.rejects(withBusinessRecovery(state,
    { name: 'boss_send_current', operationClass: 'external_effect', goal: '发送原消息', expected: '同会话新增出站' },
    async () => { await state.input(async () => { throw new WinClickError('guard refused', 2) }, true); return {} }),
    (error: unknown) => {
      assert.ok(error instanceof CodedOperationError)
      const facts = error.data!.operation_recovery as any
      assert.equal(error.code, 'UI_CHANGED')
      assert.deepEqual(facts.business_effect, { status: 'not_started', completed_count: 0 })
      assert.equal(facts.execution.input_status, 'attempted', 'earlier draft input must not be erased')
      assert.equal(facts.execution.ordinary_retries_used, 0)
      assert.match(facts.recovery_contact_ref, /^[a-f0-9]{64}$/)
      return true
    })
})

test('independent: a native response carrying the input-unknown marker cannot be reclassified by exit code 2', async () => {
  const state = new BusinessOperationState()
  await assert.rejects(withBusinessRecovery(state,
    { name: 'boss_send_current', operationClass: 'external_effect', goal: '发送原消息', expected: '同会话新增出站' },
    async () => { await state.input(async () => { throw new WinClickError('[BOSS_INPUT_UNKNOWN] 已开始后超时', 2) }, true); return {} }),
    (error: unknown) => {
      assert.ok(error instanceof CodedOperationError)
      assert.equal(error.code, 'EXECUTION_UNKNOWN')
      const facts = error.data!.operation_recovery as any
      assert.equal(facts.business_effect.status, 'unknown')
      assert.equal(facts.execution.input_status, 'unknown')
      assert.equal(facts.execution.ordinary_retries_used, 0)
      return true
    })
})

test('independent: a uniquely removed conversation remains success when the other loaded rows reorder', async () => {
  const before = actionSnapshot({ list: ['合成甲', '合成乙', '合成丙'], extra: [text('不合适', 1100, 1000, 80, 36)] })
  const after = actionSnapshot({ contact: '合成乙', list: ['合成丙', '合成乙'] })
  let clicks = 0
  const executor = new ChatRejectExecutor({ snapshot: async () => clicks ? after : before,
    click: async () => { clicks++ }, sleep: async () => {} })
  await executor.rejectCurrent()
  assert.equal(clicks, 1)
  assert.equal(executor.state.businessEffect.status, 'confirmed')
})

test('independent: duplicate same-name list rows do not prove which conversation disappeared', async () => {
  const before = actionSnapshot({ list: ['合成甲', '合成甲', '合成乙'], extra: [text('不合适', 1100, 1000, 80, 36)] })
  const after = actionSnapshot({ contact: '合成乙', list: ['合成乙'] })
  let clicks = 0
  const executor = new ChatRejectExecutor({ snapshot: async () => clicks ? after : before,
    click: async () => { clicks++ }, sleep: async () => {} })
  await assert.rejects(executor.rejectCurrent())
  assert.equal(clicks, 1)
  assert.equal(executor.state.businessEffect.status, 'unknown')
})
