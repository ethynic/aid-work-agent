import test from 'node:test'
import assert from 'node:assert/strict'
import { GreetExecutor } from '../src/main/boss/GreetExecutor.js'
import { candidateButtonVisible, candidateScrollEvent, loadedCandidates, normalizeAdvantage } from '../src/main/boss/LoadedCandidateNavigation.js'
import { loadedSnapshot, type Card } from './loadedCandidateFixture.js'
import { emptyRecommendations } from './recommendationBoundaryFixture.js'
import { createBossGreetOperation } from '../src/main/operations/bossGreet.js'
import type { BossSession } from '../src/main/operations/bossContext.js'

function harness(cards: Card[], initial: number, factor = 1) {
  let scroll = initial
  const deltas: number[] = [], clicks: string[] = []
  const deps = {
    snapshot: async () => loadedSnapshot(cards,scroll), sleep: async () => {},
    scroll: async (delta: number) => { deltas.push(delta); scroll = Math.max(0,scroll+delta*factor) },
    click: async (p: {x:number;y:number}) => {
      const target = loadedCandidates(loadedSnapshot(cards,scroll)).find(c=>c.point?.x===p.x && c.point?.y===p.y)
      assert.ok(target && candidateButtonVisible(target)); clicks.push(target.name)
      cards.find(c=>c.name===target.name && (c.advantage ? normalizeAdvantage(c.advantage) : null)===target.advantage)!.sent=true
    },
  }
  return { cards, deltas, clicks, deps }
}

test('历史卡片在视窗上方：一次大距离反向定位并且只点 fresh 目标', async () => {
  const h=harness([{name:'甲小明',y:3000},{name:'乙小明',y:10000}],10000)
  const result=await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.deepEqual(h.clicks,['甲小明']); assert.equal(result.greeted,1); assert.equal(h.deltas.length,1); assert.ok(h.deltas[0]! < -6000)
})
test('视窗下方远处：一次向下直接定位', async () => {
  const h=harness([{name:'甲小明',y:13000}],2000)
  await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.deepEqual(h.clicks,['甲小明']); assert.equal(h.deltas.length,1); assert.ok(h.deltas[0]!>10000)
})
test('多人名单从最近的目标开始，不按名单顺序重复扫描', async () => {
  const h=harness([{name:'甲小明',y:2000},{name:'乙小明',y:8000}],6000)
  await new GreetExecutor(h.deps).greetVisible({names:['甲小明','乙小明']})
  assert.deepEqual(h.clicks,['乙小明','甲小明']); assert.equal(h.deltas.length,2)
})
test('实际滚动不足：根据 fresh 位置修正，而非复用第一轮坐标', async () => {
  const h=harness([{name:'甲小明',y:3000}],10000,.8)
  await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.equal(h.deltas.length,3); assert.deepEqual(h.clicks,['甲小明'])
})
test('同名即使有一人不可见也跳过，不能先点击可见者', async () => {
  const h=harness([{name:'甲小明',y:100},{name:'甲小明',y:6000}],0)
  const result=await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.equal(result.ambiguousNames?.[0]?.name,'甲小明');assert.deepEqual(result.missingNames,[])
  assert.equal(h.clicks.length,0); assert.equal(h.deltas.length,0)
})
test('无推进最多三次定位，不能回退逐屏扫描', async () => {
  const h=harness([{name:'甲小明',y:3000}],10000,0)
  await assert.rejects(()=>new GreetExecutor(h.deps).greetVisible({names:['甲小明']}),/无法进入/)
  assert.equal(h.deltas.length,3); assert.equal(h.clicks.length,0)
})
test('滚动期间目标消失，跳过且禁止使用旧点击点', async () => {
  const h=harness([{name:'甲小明',y:3000}],10000)
  h.deps.scroll=async delta=>{h.deltas.push(delta);h.cards.splice(0)}
  const result=await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.deepEqual(result.missingNames,['甲小明'])
  assert.equal(h.clicks.length,0);assert.equal(h.deltas.length,1)
})
test('点击后卡片消失不能当作发送成功', async () => {
  const h=harness([{name:'甲小明',y:300}],0)
  h.deps.click=async()=>{h.cards.splice(0)}
  await assert.rejects(()=>new GreetExecutor(h.deps).greetVisible({names:['甲小明']}),/无法确认/)
})
test('隐藏历史节点不参与索引，空态推荐卡片不允许定位点击', async () => {
  assert.equal(loadedCandidates(loadedSnapshot([{name:'甲小明',y:300,hidden:true}])).length,0)
  let inputs=0
  const result=await new GreetExecutor({snapshot:async()=>emptyRecommendations(),click:async()=>{inputs++},scroll:async()=>{inputs++},sleep:async()=>{}}).greetVisible({names:['合成甲']})
  assert.equal(result.stopReason,'no_matching_candidates');assert.equal(inputs,0)
})
test('device px / CSS px=2：直接滚动正确转换；无有效 CSS 几何不得发输入', () => {
  const s=loadedSnapshot([{name:'甲小明',y:3000}],10000)
  assert.deepEqual(candidateScrollEvent(s,-10000),{x:275,y:260,deltaY:-5000})
  const broken=structuredClone(s); delete broken.documents[0]!.layout.clientRects
  assert.throws(()=>candidateScrollEvent(broken,200),/有效根 HTML CSS/)
  assert.throws(()=>candidateScrollEvent(s,NaN),/有效根 HTML CSS/)
})
test('已滚入 fallback 区仍向上直接定位正常目标，不点推荐人', async () => {
  const h=harness([{name:'甲小明',y:3000},{name:'乙小明',y:10000}],10000)
  h.deps.snapshot=async()=>loadedSnapshot(h.cards,10000-h.deltas.reduce((a,b)=>a-b,0),1)
  await new GreetExecutor(h.deps).greetVisible({names:['甲小明','乙小明']})
  assert.deepEqual(h.clicks,['甲小明']); assert.ok(h.deltas[0]!<0)
})
test('定位时列表重新排布，按新布局修正，不使用原按钮坐标', async () => {
  const h=harness([{name:'甲小明',y:3000}],10000)
  const scroll=h.deps.scroll; h.deps.scroll=async d=>{await scroll(d);h.cards[0]!.y+=100}
  await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.deepEqual(h.clicks,['甲小明'])
})
test('滚动后取消不会执行点击', async () => {
  const h=harness([{name:'甲小明',y:3000}],10000), controller=new AbortController()
  const scroll=h.deps.scroll;h.deps.scroll=async d=>{await scroll(d);controller.abort()}
  await assert.rejects(()=>new GreetExecutor({...h.deps,signal:controller.signal}).greetVisible({names:['甲小明']}),/已取消/)
  assert.equal(h.clicks.length,0)
})
test('snapshot 等待期间取消，返回后不执行点击或滚动', async () => {
  for (const y of [300,6000]) {
    const h=harness([{name:'甲小明',y}],0),controller=new AbortController()
    const snapshot=h.deps.snapshot
    h.deps.snapshot=async()=>{const s=await snapshot();controller.abort();return s}
    await assert.rejects(()=>new GreetExecutor({...h.deps,signal:controller.signal}).greetVisible({names:['甲小明']}),/已取消/)
    assert.equal(h.clicks.length,0);assert.equal(h.deltas.length,0)
  }
})
test('子文档比实际 iframe 高，按钮在 owner 外绝不点击', async () => {
  const s=loadedSnapshot([{name:'甲小明',y:300}],0)
  s.documents[0]!.layout.bounds[2]![3]=100
  const candidate=loadedCandidates(s)[0]!
  assert.equal(candidate.bottom,140);assert.equal(candidateButtonVisible(candidate),false)
  let clicks=0
  await assert.rejects(()=>new GreetExecutor({snapshot:async()=>s,click:async()=>{clicks++},scroll:async()=>{},sleep:async()=>{}}).greetVisible({names:['甲小明']}),/无法进入/)
  assert.equal(clicks,0)
})
test('owner 被祖先 overflow 裁剪，按钮不进入操作区域', () => {
  const s=loadedSnapshot([{name:'甲小明',y:300}],0),d=s.documents[0]!
  d.layout.bounds[1]![3]=180
  s.strings.push('hidden');d.layout.styles![1]![1]=s.strings.length-1
  assert.equal(candidateButtonVisible(loadedCandidates(s)[0]!),false)
})
test('同名不同优势跳过：先完成其他唯一姓名，回执含各人的优势', async () => {
  const h=harness([{name:'甲小明',y:300,advantage:'擅长 Java 后端'},{name:'甲小明',y:5000,advantage:'擅长 PHP 系统'},
    {name:'乙小明',y:8000,advantage:'擅长 Go'}],0)
  const result=await new GreetExecutor(h.deps).greetVisible({names:['甲小明','不存在','乙小明']})
  assert.deepEqual(h.clicks,['乙小明']);assert.equal(h.deltas.length,1)
  assert.deepEqual(result.greetedNames,['乙小明']);assert.deepEqual(result.missingNames,['不存在'])
  assert.deepEqual(result.ambiguousNames?.[0]?.candidates.map(c=>c.advantage),['擅长 Java 后端','擅长 PHP 系统'])
  assert.equal(result.ambiguousNames?.[0]?.candidates[0]?.option_index,1)
})
test('同名同优势仍跳过，即使仅一人可见也不能点击', async () => {
  const h=harness([{name:'甲小明',y:300,advantage:'擅长 PHP'},{name:'甲小明',y:5000,advantage:'擅长 PHP'}],0)
  const result=await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.equal(result.ambiguousNames?.[0]?.candidates.length,2)
  assert.equal(h.clicks.length,0);assert.equal(h.deltas.length,0)
})
test('优势只规范空白用于说明，不要求用户提供；没有优势也可处理唯一姓名', async () => {
  const h=harness([{name:'甲小明',y:300,advantage:'  擅长\n PHP  后端  '}],0)
  assert.equal(loadedCandidates(await h.deps.snapshot())[0]!.advantage,'擅长 PHP 后端')
  await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.equal(h.clicks.length,1)
  const noAdv=harness([{name:'乙小明',y:300}],0)
  await new GreetExecutor(noAdv.deps).greetVisible({names:['乙小明']});assert.equal(noAdv.clicks.length,1)
})
test('定位期间优势被替换，禁止点同名替代人', async () => {
  const h=harness([{name:'甲小明',y:3000,advantage:'原来优势'}],10000),scroll=h.deps.scroll
  h.deps.scroll=async d=>{await scroll(d);h.cards[0]!.advantage='另一优势'}
  const result=await new GreetExecutor(h.deps).greetVisible({names:['甲小明']})
  assert.deepEqual(result.missingNames,['甲小明'])
  assert.equal(h.clicks.length,0)
})
test('点击后同名但优势变了，不能把另一人的继续沟通当成功', async () => {
  const h=harness([{name:'甲小明',y:300,advantage:'原来优势'}],0)
  h.deps.click=async()=>{h.cards[0]!.advantage='另一优势';h.cards[0]!.sent=true}
  await assert.rejects(()=>new GreetExecutor(h.deps).greetVisible({names:['甲小明']}),/无法确认/)
})
test('全名单不在已加载列表或列表已空：零滚动、零点击直接返回未找到', async () => {
  for (const cards of [[],[{name:'另一个人',y:300}]]) {
    const h=harness(cards,0)
    const result=await new GreetExecutor(h.deps).greetVisible({names:['甲小明','乙小明']})
    assert.deepEqual(result.missingNames,['甲小明','乙小明']);assert.equal(h.clicks.length,0);assert.equal(h.deltas.length,0)
  }
})
test('正式operation：混合名单公开回执分别包含成功、重名优势及未找到说明', async () => {
  const h=harness([{name:'甲小明',y:300,advantage:'Java优势'},{name:'甲小明',y:5000,advantage:'PHP优势'},
    {name:'乙小明',y:7000}],0)
  const events: number[][]=[]
  const session: BossSession={snapshot:h.deps.snapshot,click:h.deps.click,
    mouseWheel:async(x,y,delta)=>{events.push([x,y,delta]);await h.deps.scroll(delta*2)},
    getUrl:async()=> 'https://www.zhipin.com/web/chat/recommend',close:async()=>{},
    clickBrowse:async()=>{throw Error('Unexpected clickBrowse')},pressEscape:async()=>{throw Error('Unexpected Escape')},
    clickAndType:async()=>{throw Error('Unexpected type')},captureFullpage:async()=>{throw Error('Unexpected capture')}}
  const result=await createBossGreetOperation(async()=>session).execute({names:['甲小明','不存在','乙小明']},
    {signal:new AbortController().signal,progress:()=>{}})
  assert.equal(result.success,true);assert.deepEqual(h.clicks,['乙小明']);assert.equal(events.length,1)
  assert.deepEqual(result.data?.greeted_names,['乙小明']);assert.deepEqual(result.data?.missing_names,['不存在'])
  assert.deepEqual((result.data?.ambiguous_names as NonNullable<typeof result.data>['ambiguous_names'] as Array<{candidates:Array<{advantage:string|null}>}>)[0]?.candidates.map(c=>c.advantage),['Java优势','PHP优势'])
  assert.match(result.message,/姓名输入有误或页面已刷新/);assert.match(result.message,/重名 1 个/)
})
test('正式operation：推荐边界分支仍保留重名、未找到提示，零输入', async () => {
  const snapshot=loadedSnapshot([{name:'甲小明',y:300,advantage:'Java优势'},
    {name:'甲小明',y:5000},{name:'乙小明',y:10000}],10000,2)
  let inputs=0
  const forbidden=async()=>{inputs++;throw Error('Unexpected input')}
  const session: BossSession={snapshot:async()=>snapshot,click:forbidden,mouseWheel:forbidden,
    getUrl:async()=> 'https://www.zhipin.com/web/chat/recommend',close:async()=>{},
    clickBrowse:forbidden,pressEscape:forbidden,clickAndType:forbidden,captureFullpage:async()=>{throw Error('Unexpected capture')}}
  const result=await createBossGreetOperation(async()=>session).execute({names:['甲小明','不存在','乙小明']},
    {signal:new AbortController().signal,progress:()=>{}})
  assert.equal(result.success,true);assert.equal(inputs,0)
  assert.equal(result.data?.stop_reason,'recommendations_boundary')
  assert.deepEqual(result.data?.missing_names,['不存在','乙小明'])
  assert.match(result.message,/推荐内容边界/);assert.match(result.message,/重名 1 个/);assert.match(result.message,/未找到 2 人/)
})
