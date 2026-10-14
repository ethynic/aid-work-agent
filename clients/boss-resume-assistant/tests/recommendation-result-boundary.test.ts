import assert from 'node:assert/strict'
import test from 'node:test'
import { inspectRecommendationResults, excludedRecommendationName } from '../src/main/boss/RecommendationResultBoundary.js'
import { findButtonsByExactText, GREET_TEXT } from '../src/main/boss/cardName.js'
import { ResumeBatchReader, type ResumeBatchDeps } from '../src/main/boss/ResumeBatchReader.js'
import { GreetExecutor } from '../src/main/boss/GreetExecutor.js'
import { DetailGreetExecutor } from '../src/main/boss/DetailGreetExecutor.js'
import { classOf, parentMapOf } from '../src/main/boss/domSnapshot.js'
import { buildResumeContinuation } from '../src/main/operations/bossResumeBatch.js'
import { emptyRecommendations, matchingThenRecommendations, setHidden, withCanvas } from './recommendationBoundaryFixture.js'
import { ResumeReader } from '../src/main/boss/ResumeReader.js'

const forbidden = async (): Promise<never> => { assert.fail('空筛选/推荐边界不允许输入或读取') }
const batchDeps = (snapshot: ResumeBatchDeps['snapshot']): ResumeBatchDeps => ({ snapshot, click: forbidden, pressEscape: forbidden,
  captureFullpage: forbidden, wheel: forbidden, sameView: forbidden, stitch: forbidden, scroll: forbidden, sleep: async () => {} })

test('真实空态即使有打招呼按钮也没有符合卡片；全量DOM中离屏空态仍有效', () => {
  for (const scrollOffsetY of [0, 400, 900]) {
    const snap = emptyRecommendations(); snap.documents[2]!.scrollOffsetY = scrollOffsetY
    assert.equal(inspectRecommendationResults(snap).stopReason, 'no_matching_candidates')
    assert.equal(findButtonsByExactText(snap, GREET_TEXT).length, 0)
    assert.equal(new ResumeBatchReader(batchDeps(async () => snap)).locateCards(snap).length, 0)
    assert.equal(excludedRecommendationName(snap, '李样例'), 'no_matching_candidates')
  }
})

test('真实空态batch/next/greet/open与已打开推荐详情停止，零输入零读取', async () => {
  const snap = emptyRecommendations()
  for (const scanMore of [false, true]) {
    const result = await new ResumeBatchReader(batchDeps(async () => snap)).readBatch({ limit: 3, scanMore })
    assert.deepEqual(result, { resumes: [], failures: [], attempted: 0, processed_names: [], stop_reason: 'no_matching_candidates' })
  }
  for (const names of [undefined, ['李样例']]) {
    const result = await new GreetExecutor({ snapshot: async () => snap, click: forbidden, scroll: forbidden }).greetVisible({ limit: 3, names })
    assert.equal(result.greeted, 0)
    assert.equal(result.stopReason, 'no_matching_candidates')
  }
  const detail = new DetailGreetExecutor({ snapshot: async () => snap, click: forbidden, pressEscape: forbidden, scroll: forbidden })
  assert.equal((await detail.openDetail({ name: '李样例' })).opened, false)
  assert.equal((await detail.greet({ name: '李样例' })).stopReason, 'no_matching_candidates')
})

test('边界前正常卡片可定位，边界后推荐被排除；读完/排除正常卡片后不滚入推荐', async () => {
  const snap = matchingThenRecommendations()
  const reader = new ResumeBatchReader(batchDeps(async () => snap))
  assert.deepEqual(reader.locateCards(snap).map((card) => card.name), ['钱测试'])
  assert.equal(inspectRecommendationResults(snap).stopReason, 'recommendations_boundary')
  const result = await reader.readBatch({ limit: 3, scanMore: true, excludeNames: ['钱测试'] })
  assert.equal(result.attempted, 0)
  assert.equal(result.stop_reason, 'recommendations_boundary')
  assert.equal(excludedRecommendationName(snap, '钱测试'), undefined)
  assert.equal(excludedRecommendationName(snap, '李样例'), 'recommendations_boundary')
})

test('隐藏空态/隐藏frame及其他文档或候选人描述的同文案不制造空结果', () => {
  const snap = emptyRecommendations(), state = inspectRecommendationResults(snap)
  assert.equal(inspectRecommendationResults(setHidden(snap, 2, state.boundaries[0]!.card)).boundaries.length, 0)
  const owner = (snap.documents[0]!.nodes.contentDocumentIndex as number[]).indexOf(2)
  assert.equal(inspectRecommendationResults(setHidden(snap, 0, owner)).boundaries.length, 0)
  const noStructure = structuredClone(snap)
  const doc = noStructure.documents[2]!
  const component = doc.layout.nodeIndex.find((node) => (classOf(noStructure, 2, node) ?? '') === 'mome-main-ui')!
  ;(doc.nodes.attributes as number[][])[component] = []
  assert.equal(inspectRecommendationResults(noStructure).boundaries.length, 0)
  const missingOwner = structuredClone(snap)
  missingOwner.documents[0]!.layout.nodeIndex = [0]; missingOwner.documents[0]!.layout.bounds = [[0, 0, 3200, 1618]]
  assert.equal(inspectRecommendationResults(missingOwner).boundaries.length, 0)
})

test('真实空态tip仍可见，推荐divider隐藏或缺失仍应零匹配停止', () => {
  const snap = emptyRecommendations(), doc = snap.documents[2]!
  const divider = doc.layout.nodeIndex.find((node) => (classOf(snap, 2, node) ?? '') === 'recommend-mome-ui')!
  const hiddenDivider = setHidden(snap, 2, divider)
  assert.equal(inspectRecommendationResults(hiddenDivider).stopReason, 'no_matching_candidates')
  assert.equal(findButtonsByExactText(hiddenDivider, GREET_TEXT).length, 0)
  const missingDivider = structuredClone(snap)
  ;(missingDivider.documents[2]!.nodes.attributes as number[][])[divider] = []
  assert.equal(inspectRecommendationResults(missingDivider).stopReason, 'no_matching_candidates')
})

test('经历中的目标同名不能证明详情来自推荐；两区姓名字段同名则歧义停止', () => {
  const snap = matchingThenRecommendations(), doc = snap.documents[2]!, parents = parentMapOf(doc)!
  const values = doc.nodes.nodeValue as number[]
  const actualName = values.findIndex((value) => snap.strings[value] === '李样例')
  const card = inspectRecommendationResults(snap).boundaries[0]!.cards.at(-1)!
  const node = values.length; snap.strings.push('钱测试'); values.push(snap.strings.length - 1)
  ;(doc.nodes.parentIndex as number[]).push(card)
  ;(doc.nodes.attributes as number[][]).push([])
  // New snapshot isolates immutable cached indexes and models arbitrary experience text.
  const experience = structuredClone(snap)
  assert.equal(excludedRecommendationName(experience, '钱测试'), undefined)
  const duplicate = structuredClone(snap)
  ;(duplicate.documents[2]!.nodes.nodeValue as number[])[actualName] = duplicate.strings.indexOf('钱测试')
  assert.throws(() => excludedRecommendationName(duplicate, '钱测试'), /同名候选人/)
  assert.ok(parents)
})

test('未知推荐组件文案不放行；终态原因不会被缺少职位/历史覆盖', () => {
  const snap = matchingThenRecommendations()
  snap.strings = snap.strings.map((text) => text === '暂无符合牛人，为你推荐' ? '未知推荐区文案' : text)
  assert.throws(() => inspectRecommendationResults(snap), /未确认/)
  for (const reason of ['no_matching_candidates', 'recommendations_boundary']) {
    const next = buildResumeContinuation([], [], null, reason)
    assert.equal(next.available, false)
    assert.equal(next.reason, reason)
  }
})

test('卡片首击后列表变空态，batch/open的重击前门禁阻止缓存坐标再次点击', async () => {
  for (const canvas of [false, true]) {
    const matching = matchingThenRecommendations(), empty = canvas ? withCanvas(emptyRecommendations()) : emptyRecommendations()
    let state = matching, clicks = 0
    const deps = { ...batchDeps(async () => state), click: async () => { clicks++; state = empty } }
    const result = await new ResumeBatchReader(deps).readBatch({ limit: 3 })
    assert.equal(result.stop_reason, 'recommendations_boundary')
    assert.equal(result.resumes.length, 0)
    assert.equal(clicks, 1)
    state = matching; clicks = 0
    const detail = new DetailGreetExecutor({ snapshot: async () => state, click: async () => { clicks++; state = empty }, pressEscape: forbidden, sleep: async () => {} })
    const opened = await detail.openDetail({ name: '钱测试' })
    assert.equal(opened.opened, false)
    assert.equal(opened.stopReason, 'recommendations_boundary')
    assert.equal(clicks, 1)
  }
})

test('先读到真实匹配简历随后变空态，保留成功结果并以推荐边界停止', async () => {
  const matching = matchingThenRecommendations()
  let state = matching, reads = 0
  const original = ResumeReader.prototype.readResume
  ResumeReader.prototype.readResume = async () => {
    reads++
    return { segments: 1, bottomReached: true, width: 1200, height: 1400, imageBuffer: Buffer.from('test-image'), seamMis: [], suspectSeams: [] }
  }
  try {
    const reader = new ResumeBatchReader({ ...batchDeps(async () => state), click: async () => { state = withCanvas(matching) }, pressEscape: async () => { state = emptyRecommendations() } })
    const result = await reader.readBatch({ limit: 3 })
    assert.equal(result.resumes.length, 1)
    assert.equal(result.resumes[0]!.name, '钱测试')
    assert.equal(result.stop_reason, 'recommendations_boundary')
    assert.equal(reads, 1)
  } finally { ResumeReader.prototype.readResume = original }
})
