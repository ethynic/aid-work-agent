import assert from 'node:assert/strict'
import test from 'node:test'
import { loadedSnapshot } from './loadedCandidateFixture.js'
import { findButtonsByExactText, pairCardNameWithPoint } from '../src/main/boss/cardName.js'
import { candidateReference, loadedCandidates } from '../src/main/boss/LoadedCandidateNavigation.js'
import { inspectRecommendationResults } from '../src/main/boss/RecommendationResultBoundary.js'
import { viewportOf } from '../src/main/boss/FilterSetter.js'
import { ResumeBatchReader, type ResumeBatchCheckpoint } from '../src/main/boss/ResumeBatchReader.js'
import { GreetExecutor } from '../src/main/boss/GreetExecutor.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { operationRecoveryFailure } from '../src/main/operations/pageOperation.js'
import { CodedOperationError } from '../src/main/operations/types.js'

test('a toolbar word on the same row cannot replace the name inside the candidate card', () => {
  const s = loadedSnapshot([{ name: '合成甲', y: 200, baseInfo: '28岁 | 本科' }])
  const d = s.documents[1]!, node = (d.nodes.nodeValue as number[]).length
  s.strings.push('推荐')
  ;(d.nodes.nodeValue as number[]).push(s.strings.length - 1)
  ;(d.nodes.nodeName as number[]).push(s.strings.indexOf('#text'))
  ;(d.nodes.parentIndex as number[]).push(0)
  ;(d.nodes.attributes as number[][]).push([])
  d.layout.nodeIndex.push(node); d.layout.bounds.push([0, 225, 40, 30])
  const button = findButtonsByExactText(s, '打招呼')[0]!
  assert.equal(pairCardNameWithPoint(s, button, viewportOf(s)).name, '合成甲')
})

test('an unknown inserted recommendation component does not invalidate the cards after it', () => {
  const s = loadedSnapshot([{ name: '合成甲', y: 200 }, { name: '合成乙', y: 600 }], 0, 1)
  s.strings = s.strings.map(t => t === '暂无符合牛人，为你推荐' ? '推广内容新版' : t)
  assert.equal(inspectRecommendationResults(s).stopReason, undefined)
  assert.deepEqual(loadedCandidates(s).filter(c => !c.excluded).map(c => c.name), ['合成甲', '合成乙'])
})

function readerDeps(snapshot: () => Promise<ReturnType<typeof loadedSnapshot>>) {
  return { snapshot, click: async (_p: {x:number;y:number}) => { throw new WinClickError('STALE_OBSERVATION: render widget hidden; input refused',2) },
    pressEscape: async () => {}, captureFullpage: async () => Buffer.alloc(0), wheel: async () => {},
    sameView: async () => true, stitch: async () => ({ width: 1, height: 1, overlaps: [], seamMis: [] }), sleep: async () => {} }
}

test('batch seeks a clipped known card and uses a fresh name anchor after reflow', async () => {
  let scroll = 0, moved = false, clicked: {x:number;y:number} | undefined
  const snapshot = async () => loadedSnapshot([{name:'合成甲',y:moved ? 1700 : 1800,baseInfo:'本科'}],scroll)
  const deps = readerDeps(snapshot)
  deps.click = async p => { clicked=p; throw new WinClickError('STALE_OBSERVATION: render widget hidden; input refused',2) }
  const reader = new ResumeBatchReader({...deps, scroll: async (_x,_y,delta) => { scroll += delta * 2; moved = true }})
  const r = await reader.readBatch({limit:1})
  assert.ok(moved); assert.ok(clicked)
  assert.deepEqual(clicked, loadedCandidates(await snapshot())[0]!.namePoint)
  assert.equal(r.resumes.length,0); assert.ok(r.recovery)
})

test('unreachable cards remain unprocessed rather than becoming permanent exclusions', async () => {
  const r = await new ResumeBatchReader(readerDeps(async () => loadedSnapshot([{name:'合成甲',y:1800}]))).readBatch({limit:1})
  assert.deepEqual(r.processed_names,[]); assert.equal(r.attempted,0)
  assert.equal(r.failures[0]?.execution?.inputStatus,'not_started')
})

test('two same-name cards cannot be bound by their viewport order for batch reads', async () => {
  const r = await new ResumeBatchReader(readerDeps(async () => loadedSnapshot([{name:'合成甲',y:200},{name:'合成甲',y:450}]))).readBatch({limit:1})
  assert.equal(r.resumes.length,0); assert.equal(r.attempted,0); assert.deepEqual(r.processed_names,[])
  assert.match(r.failures[0]!.error,/同名/)
})

test('an already contacted target is recorded without another external input', async () => {
  let inputs=0
  const r = await new GreetExecutor({ snapshot: async () => loadedSnapshot([{name:'合成甲',y:200,sent:true}]),
    click: async () => { inputs++ }, sleep: async () => {} }).greetVisible({names:['合成甲']})
  assert.equal(inputs,0); assert.equal(r.greeted,0); assert.deepEqual(r.alreadyContactedNames,['合成甲']); assert.deepEqual(r.missingNames,[])
})

test('one target stuck outside the window does not prevent another approved target', async () => {
  let sent=false, scrolls=0
  const r = await new GreetExecutor({ snapshot: async () => loadedSnapshot([{name:'合成甲',y:1800},{name:'合成乙',y:200,sent,baseInfo:'本科'}]),
    click: async () => { sent=true }, scroll: async () => { scrolls++ }, sleep: async () => {} }).greetVisible({names:['合成甲','合成乙']})
  assert.equal(scrolls,3); assert.deepEqual(r.greetedNames,['合成乙']); assert.deepEqual(r.unavailableNames,['合成甲'])
})

test('generic internal failures keep confirmed per-person facts without becoming page-replay errors', () => {
  const facts={operation_class:'external_effect',execution:{stage:'postcheck',input_status:'attempted'},
    business_effect:{status:'confirmed',completed_count:1}}
  assert.throws(() => operationRecoveryFailure(new Error('snapshot decode failed'),facts,{confirmed_items:[{name:'合成甲',greeted:true}]}),
    e => e instanceof CodedOperationError && e.code==='INTERNAL_ERROR' && Array.isArray(e.data?.confirmed_items)
      && (e.data?.operation_recovery as typeof facts).business_effect.completed_count===1)
})

test('offscreen continuation keeps the original binding and rejects a same-name replacement before scrolling', async () => {
  const original=loadedCandidates(loadedSnapshot([{name:'合成甲',y:200,baseInfo:'原始本科'}]))[0]!
  const ref=candidateReference(original)!
  const checkpoint: ResumeBatchCheckpoint={current:{name:original.name,reference:ref,phase:'open',failureStart:0}}
  let clicks=0,scrolls=0
  const r=await new ResumeBatchReader({...readerDeps(async()=>loadedSnapshot([{name:'合成甲',y:1800,baseInfo:'替代硕士'}])),checkpoint,
    click:async()=>{clicks++},scroll:async()=>{scrolls++}}).readBatch({limit:1})
  assert.equal(clicks,0); assert.equal(scrolls,0); assert.equal(r.resumes.length,0)
  assert.equal(checkpoint.current?.reference,ref); assert.match(r.recovery!.summary,/原未完成候选来源/)
})

test('an unchanged offscreen continuation scrolls fresh while retaining its original reference', async () => {
  const original=loadedCandidates(loadedSnapshot([{name:'合成甲',y:200,baseInfo:'原始本科'}]))[0]!
  const ref=candidateReference(original)!, checkpoint: ResumeBatchCheckpoint={current:{name:original.name,reference:ref,phase:'open',failureStart:0}}
  let scroll=0,scrolls=0
  const r=await new ResumeBatchReader({...readerDeps(async()=>loadedSnapshot([{name:'合成甲',y:1800,baseInfo:'原始本科'}],scroll)),checkpoint,
    scroll:async(_x,_y,delta)=>{scroll+=delta*2;scrolls++}}).readBatch({limit:1})
  assert.equal(scrolls,1); assert.ok(r.recovery); assert.equal(checkpoint.current?.reference,ref)
})
