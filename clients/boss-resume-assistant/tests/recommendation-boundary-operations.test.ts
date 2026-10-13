import assert from 'node:assert/strict'
import test from 'node:test'
import { createBossResumeBatchOperation } from '../src/main/operations/bossResumeBatch.js'
import { createBossResumeDetailOperation } from '../src/main/operations/bossResumeDetail.js'
import { createBossOpenDetailOperation } from '../src/main/operations/bossOpenDetail.js'
import { createBossGreetDetailOperation } from '../src/main/operations/bossGreetDetail.js'
import { createBossGreetOperation } from '../src/main/operations/bossGreet.js'
import { FILTER_SNAPSHOT_OPTIONS } from '../src/main/boss/FilterSetter.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import type { OpContext } from '../src/main/operations/types.js'
import { emptyRecommendations, withCanvas } from './recommendationBoundaryFixture.js'

test('正式operations空态零输入，关闭session，typed回执停筛且请求canonical visibility styles', async () => {
  const snap = withCanvas(emptyRecommendations())
  // 原截取fixture仅包含列表，补合成职位/筛选栏供正式next前置及job绑定验证。
  const header = snap.documents[0]!
  for (const [text, bounds] of [['筛选', [3000, 30, 100, 30]], ['测试职位 _ 10-20K 北京', [2200, 30, 500, 30]]] as const) {
    const node = (header.nodes.nodeValue as number[]).length
    snap.strings.push(text)
    ;(header.nodes.nodeValue as number[]).push(snap.strings.length - 1)
    ;(header.nodes.nodeName as number[]).push(-1)
    ;(header.nodes.parentIndex as number[]).push(0)
    ;(header.nodes.attributes as number[][]).push([])
    ;(header.nodes.contentDocumentIndex as number[]).push(-1)
    header.layout.nodeIndex.push(node); header.layout.bounds.push([...bounds])
  }
  const ctx: OpContext = { signal: new AbortController().signal, progress: () => {} }
  const cases = ['batch', 'next', 'open', 'greet', 'greet-detail', 'resume-detail'] as const
  for (const kind of cases) {
    let closed = 0, canonical = 0
    const forbidden = async (): Promise<never> => { assert.fail(`${kind}不能输入或采图`) }
    const session: BossSession = {
      snapshot: async (options) => { if (options) { assert.deepEqual(options, FILTER_SNAPSHOT_OPTIONS); canonical++ } return snap },
      getUrl: async () => 'https://www.zhipin.com/web/chat/recommend', close: async () => { closed++ },
      click: forbidden, clickBrowse: forbidden, clickAndType: forbidden, mouseWheel: forbidden, pressEscape: forbidden, captureFullpage: forbidden,
    }
    const factory = async () => session
    const result = kind === 'batch' ? await createBossResumeBatchOperation(factory).execute({ limit: 3 }, ctx)
      : kind === 'next' ? await createBossResumeBatchOperation(factory, true).execute({ limit: 3, exclude_names: ['钱测试'], expected_job_name: '测试职位' }, ctx)
      : kind === 'open' ? await createBossOpenDetailOperation(factory).execute({ name: '李样例' }, ctx)
      : kind === 'greet' ? await createBossGreetOperation(factory).execute({ names: ['李样例'] }, ctx)
      : kind === 'greet-detail' ? await createBossGreetDetailOperation(factory).execute({ name: '李样例' }, ctx)
      : await createBossResumeDetailOperation(factory).execute({ candidate_name: '李样例' }, ctx)
    assert.equal(result.effect, 'none')
    assert.equal(closed, 1)
    if (kind === 'resume-detail') { assert.equal(result.code, 'NO_MATCHING_CANDIDATES'); assert.equal(result.success, false) }
    else {
      assert.equal(result.success, true)
      assert.equal(result.data.stop_reason, 'no_matching_candidates')
      if (kind === 'batch' || kind === 'next') {
        assert.equal(result.data.attempted, 0)
        assert.equal((result.data.continuation as { available: boolean }).available, false)
        assert.equal((result.data.continuation as { reason: string }).reason, 'no_matching_candidates')
      }
    }
    assert.ok(canonical > 0)
  }
})
