import assert from 'node:assert/strict'
import test from 'node:test'
import { ResumeBatchReader } from '../src/main/boss/ResumeBatchReader.js'
import { createBossResumeBatchOperation } from '../src/main/operations/bossResumeBatch.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { PageExecution } from '../src/main/operations/pageOperation.js'
import { buildChatSnapshot } from './chatReadFixture.js'
import { emptyRecommendations } from './recommendationBoundaryFixture.js'

const probe = buildChatSnapshot([
  { tag: '#text', text: '筛选', bounds: [1100, 56, 100, 28] },
  { tag: '#text', text: '合成职位 _ 上海 10-20K', bounds: [700, 56, 300, 28] },
])
const failInput = async (): Promise<never> => { assert.fail('the mocked reader must not perform browser or native IO') }

for (const continuation of [false, true]) for (const state of ['normal', 'retried', 'empty'] as const) {
  test(`independent ${continuation ? 'next' : 'batch'} success reports ${state} execution from its reader`, async () => {
    const prior = ResumeBatchReader.prototype.readBatch
    const expected: PageExecution = { attemptsUsed: state === 'retried' ? 2 : 1,
      ordinaryRetriesUsed: state === 'retried' ? 1 : 0,
      inputStatus: state === 'empty' ? 'not_started' : 'attempted', stage: 'postcheck',
      verification: { status: 'met', evidence: ['本轮最后候选人的实际执行事实'] } }
    let reads = 0
    ResumeBatchReader.prototype.readBatch = async function () {
      reads++
      Reflect.set(this, 'execution', structuredClone(expected))
      return { resumes: state === 'empty' ? [] : [{ name: '合成甲', readResult: {
        segments: 1, bottomReached: true, width: 100, height: 200,
        imageBuffer: Buffer.from('synthetic-image'), seamMis: [], suspectSeams: [],
      } }], failures: [], attempted: state === 'empty' ? 0 : 1,
      processed_names: state === 'empty' ? [] : ['合成甲'],
      stop_reason: state === 'empty' ? 'no_matching_candidates' : 'limit_reached' }
    }
    const session: BossSession = { snapshot: async () => probe,
      getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => {},
      click: failInput, clickBrowse: failInput, clickAndType: failInput, mouseWheel: failInput,
      pressEscape: failInput, captureFullpage: failInput }
    try {
      const result = await createBossResumeBatchOperation(async () => session, continuation).execute({
        limit: 1, ...(continuation ? { exclude_names: ['已读乙'], expected_job_name: '合成职位' } : {}),
      }, { signal: new AbortController().signal, progress() {} })
      assert.equal(result.success, true)
      assert.equal(reads, 1)
      const facts = result.data.operation_recovery as any
      assert.equal(facts.operation.name, continuation ? 'boss_resume_next' : 'boss_resume_batch')
      assert.equal(facts.execution.input_status, expected.inputStatus)
      assert.equal(facts.execution.attempts_used, expected.attemptsUsed)
      assert.equal(facts.execution.ordinary_retries_used, expected.ordinaryRetriesUsed)
      assert.equal(facts.execution.stage, 'postcheck')
      assert.equal(facts.verification.status, 'satisfied')
      assert.equal((result.data.resumes as unknown[]).length, state === 'empty' ? 0 : 1)
    } finally { ResumeBatchReader.prototype.readBatch = prior }
  })
}

test('independent actual reader empty boundary resets preceding call facts and exposes a snapshot copy', async () => {
  const snapshot = emptyRecommendations()
  const reader = new ResumeBatchReader({ snapshot: async () => snapshot,
    click: failInput, pressEscape: failInput, wheel: failInput, captureFullpage: failInput,
    sameView: failInput, stitch: failInput, sleep: async () => {} })
  Reflect.set(reader, 'execution', { attemptsUsed: 2, ordinaryRetriesUsed: 1,
    inputStatus: 'attempted', stage: 'postcheck', verification: { status: 'met', evidence: ['上轮历史'] } })
  const result = await reader.readBatch({ limit: 1 })
  assert.equal(result.stop_reason, 'no_matching_candidates')
  assert.deepEqual(result.resumes, [])
  assert.equal(reader.batchExecution.inputStatus, 'not_started')
  assert.equal(reader.batchExecution.ordinaryRetriesUsed, 0)
  const copy = reader.batchExecution
  copy.inputStatus = 'unknown'; copy.verification.evidence.push('外部修改')
  assert.equal(reader.batchExecution.inputStatus, 'not_started')
  assert.deepEqual(reader.batchExecution.verification.evidence, [])
})
