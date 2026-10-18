import assert from 'node:assert/strict'
import test from 'node:test'
import { GreetExecutor, GreetError } from '../src/main/boss/GreetExecutor.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { loadedCandidates } from '../src/main/boss/LoadedCandidateNavigation.js'
import { loadedSnapshot, type Card } from './loadedCandidateFixture.js'

function harness(cards: Card[], opts: { scroll?: boolean; limit?: number } = {}) {
  let offset = 0
  const clicks: string[] = [], scrolls: number[] = []
  const deps = { snapshot: async () => loadedSnapshot(cards, offset), sleep: async () => {},
    click: async (p: {x:number;y:number}) => {
      const target = loadedCandidates(loadedSnapshot(cards, offset)).find(c => c.point?.x === p.x && c.point?.y === p.y)
      assert.ok(target, 'every input must use a fresh indexed card')
      clicks.push(target.name); cards.find(c => c.name === target.name)!.sent = true
    },
    ...(opts.scroll ? { scroll: async (delta:number) => { scrolls.push(delta); offset = Math.min(opts.limit ?? 10000, Math.max(0, offset + delta)) } } : {}) }
  return { cards, clicks, scrolls, deps }
}
const person = (name: string, y = 200): Card => ({ name, y, baseInfo: '28岁 | 5年 | 本科' })

test('greet confirms each original card becoming continue, rather than global counts', async () => {
  const h = harness([person('合成甲',150),person('合成乙',430)])
  const executor = new GreetExecutor(h.deps), result = await executor.greetVisible()
  assert.equal(result.greeted,2); assert.deepEqual(h.clicks,['合成甲','合成乙'])
  assert.equal(executor.state.businessEffect.completed_count,2)
})
test('named greetings only touch approved unique targets; missing names remain missing', async () => {
  const h=harness([person('合成甲'),person('合成乙',450)])
  const result=await new GreetExecutor(h.deps).greetVisible({names:['合成乙','不存在']})
  assert.deepEqual(h.clicks,['合成乙']);assert.deepEqual(result.greetedNames,['合成乙']);assert.deepEqual(result.missingNames,['不存在'])
})
test('offscreen unnamed cards are never clicked without scrolling', async()=>{
 const h=harness([person('合成甲'),person('合成乙',3000)]), result=await new GreetExecutor(h.deps).greetVisible()
 assert.deepEqual(h.clicks,['合成甲']);assert.equal(result.greeted,1)
})
test('limit stops after confirmed targets, not attempted clicks', async()=>{
 const h=harness([person('合成甲'),person('合成乙',450)]),r=await new GreetExecutor(h.deps).greetVisible({limit:1})
 assert.equal(r.greeted,1);assert.equal(r.reachedEnd,false);assert.deepEqual(h.clicks,['合成甲'])
})
test('empty loaded list returns no sends; explicit empty names are invalid',async()=>{
 const h=harness([]);assert.equal((await new GreetExecutor(h.deps).greetVisible()).greeted,0)
 await assert.rejects(new GreetExecutor(h.deps).greetVisible({names:[]}),/不能是空数组/);assert.equal(h.clicks.length,0)
})
test('same name with different cards is ambiguous and never silently picks the first',async()=>{
 const h=harness([person('重名甲'),{...person('重名甲',450),baseInfo:'35岁 | 10年 | 硕士'}])
 const r=await new GreetExecutor(h.deps).greetVisible({names:['重名甲']});assert.equal(h.clicks.length,0);assert.equal(r.ambiguousNames?.length,1)
})
test('disappearance after a click is unknown, not a successful greeting',async()=>{
 const h=harness([person('合成甲')]);h.deps.click=async()=>{h.clicks.push('合成甲');h.cards.splice(0)}
 const executor=new GreetExecutor(h.deps);await assert.rejects(executor.greetVisible(),/无法确认/)
 assert.equal(h.clicks.length,1);assert.equal(executor.state.businessEffect.status,'unknown')
})
test('an unchanged original greet button never causes an automatic duplicate',async()=>{
 const h=harness([person('合成甲')]);h.deps.click=async()=>{h.clicks.push('合成甲')}
 const executor=new GreetExecutor(h.deps);await assert.rejects(executor.greetVisible(),/无法确认/)
 assert.equal(h.clicks.length,1);assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})
test('unrelated continue and shrinking total do not prove the original target',async()=>{
 const h=harness([person('合成甲'),person('合成乙',450)])
 h.deps.click=async()=>{h.clicks.push('合成甲');h.cards.shift();h.cards[0]!.sent=true}
 await assert.rejects(new GreetExecutor(h.deps).greetVisible(),/无法确认/);assert.equal(h.clicks.length,1)
})
test('new unrelated cards do not invalidate a confirmed original continue state',async()=>{
 const h=harness([person('合成甲')]),click=h.deps.click
 h.deps.click=async p=>{await click(p);h.cards.push(person('新推荐',450))}
 const r=await new GreetExecutor(h.deps).greetVisible({names:['合成甲']});assert.equal(r.greeted,1)
})
test('after a partial batch fails, confirmed per-person outcomes remain available',async()=>{
 const h=harness([person('合成甲'),person('合成乙',450)]),click=h.deps.click
 h.deps.click=async p=>{if(h.clicks.length)throw new WinClickError('[BOSS_INPUT_UNKNOWN] timed out after input');await click(p)}
 const executor=new GreetExecutor(h.deps);await assert.rejects(executor.greetVisible())
 assert.equal(executor.state.businessEffect.completed_count,1);assert.deepEqual(executor.state.confirmedItems,[{name:'合成甲',greeted:true}]);assert.equal(executor.state.businessEffect.status,'unknown')
})
test('native guard refusal is not an external effect and never spends an ordinary retry',async()=>{
 const h=harness([person('合成甲')]);let count=0;h.deps.click=async()=>{count++;throw new WinClickError('guard refused',2)}
 const executor=new GreetExecutor(h.deps);await assert.rejects(executor.greetVisible());assert.equal(count,1)
 assert.equal(executor.state.businessEffect.status,'not_started');assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})
test('paywall has its own business error and is not treated as an ordinary retry',async()=>{
 const h=harness([person('合成甲')]);let clicked=false
 h.deps.click=async()=>{clicked=true;h.clicks.push('合成甲')};const snapshot=h.deps.snapshot
 h.deps.snapshot=async()=>{const s=await snapshot();if(clicked)s.strings.push('该职位无开聊权益');return s}
 await assert.rejects(new GreetExecutor(h.deps).greetVisible(),(e:unknown)=>e instanceof GreetError&&/付费墙/.test(e.message));assert.equal(h.clicks.length,1)
})
test('cancellation before any action uses no input',async()=>{
 const h=harness([person('合成甲')]),abort=new AbortController();abort.abort()
 await assert.rejects(new GreetExecutor({...h.deps,signal:abort.signal}).greetVisible(),/取消/);assert.equal(h.clicks.length,0)
})
test('fresh reflow coordinates are used for the next authorized target',async()=>{
 const h=harness([person('合成甲'),person('合成乙',450)]),click=h.deps.click
 h.deps.click=async p=>{await click(p);if(h.clicks.length===1)h.cards[1]!.y=500}
 const r=await new GreetExecutor(h.deps).greetVisible();assert.equal(r.greeted,2);assert.deepEqual(h.clicks,['合成甲','合成乙'])
})

test('second target guard refusal preserves the first confirmed person and never marks whole batch untouched',async()=>{
 const h=harness([person('合成甲'),person('合成乙',450)]),click=h.deps.click
 h.deps.click=async p=>{if(h.clicks.length)throw new WinClickError('guard refused',2);await click(p)}
 const executor=new GreetExecutor(h.deps);await assert.rejects(executor.greetVisible())
 assert.equal(executor.state.businessEffect.status,'confirmed');assert.equal(executor.state.businessEffect.completed_count,1);assert.equal(executor.state.confirmedItems.length,1)
})
