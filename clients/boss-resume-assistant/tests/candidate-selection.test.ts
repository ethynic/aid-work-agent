import test from 'node:test'
import assert from 'node:assert/strict'
import { loadedSnapshot, type Card } from './loadedCandidateFixture.js'
import { loadedCandidates, candidateReference, resolveCandidateReference, CANDIDATE_REF_TTL_MS } from '../src/main/boss/LoadedCandidateNavigation.js'
import { GreetExecutor } from '../src/main/boss/GreetExecutor.js'
import { createBossGreetSelectedOperation, createBossLookupCandidatesOperation } from '../src/main/operations/bossCandidateSelection.js'
import type { BossSession } from '../src/main/operations/bossContext.js'
import { withCanvas } from './recommendationBoundaryFixture.js'

const people: Card[]=[{name:'张测试',y:300,baseInfo:'28岁 | 5年 | 本科',expectation:'上海 | PHP'},
  {name:'张测试',y:7000,baseInfo:'39岁 | 10年以上 | 大专',expectation:'北京 | Java'}]
test('候选人卡片提供不同信息与编号；相同姓名/没有优势也有不同引用', async()=>{
  const s=loadedSnapshot(people),cards=loadedCandidates(s),first=candidateReference(cards[0]!),second=candidateReference(cards[1]!)
  assert.ok(first&&second&&first!==second)
  assert.equal(cards[0]!.cardInfo.base_info,'28岁 | 5年 | 本科')
  assert.equal(cards[1]!.cardInfo.expectation,'北京 | Java')
  assert.equal(cards[0]!.advantage,null)
  const result=await new GreetExecutor({snapshot:async()=>s,click:async()=>{throw Error('No name-only click')},sleep:async()=>{}}).greetVisible({names:['张测试']})
  assert.deepEqual(result.ambiguousNames?.[0]?.candidates.map(c=>c.option_index),[1,2])
  assert.ok(result.ambiguousNames?.[0]?.candidates.every(c=>c.candidate_ref))
})
test('卡片重排/滚动不改引用定位；口语选择由Agent转成已有ref，执行不做文字猜测', async()=>{
  const cards=structuredClone(people),ref=candidateReference(loadedCandidates(loadedSnapshot(cards))[1]!)!
  cards.reverse();cards[0]!.y=300;cards[1]!.y=7000
  const clicks: string[]=[]
  const result=await new GreetExecutor({snapshot:async()=>loadedSnapshot(cards),click:async()=>{clicks.push(cards[0]!.baseInfo!);cards[0]!.sent=true},sleep:async()=>{}})
    .greetVisible({names:['张测试'],selection:{name:'张测试',candidate_ref:ref}})
  assert.deepEqual(clicks,['39岁 | 10年以上 | 大专']);assert.equal(result.greeted,1);assert.equal(cards[1]!.sent,undefined)
})
test('过期、未来、篡改、换账号、换职位、卡片信息变化全部失效', ()=>{
  const s=loadedSnapshot(people),now=Date.now(),ref=candidateReference(loadedCandidates(s)[0]!,now)!
  assert.equal(resolveCandidateReference(s,'张测试',ref,now)?.cardInfo.base_info,people[0]!.baseInfo)
  assert.equal(resolveCandidateReference(s,'其他姓名',ref,now),null)
  assert.equal(resolveCandidateReference(s,'张测试',ref,now+CANDIDATE_REF_TTL_MS+1),null)
  assert.equal(resolveCandidateReference(s,'张测试',candidateReference(loadedCandidates(s)[0]!,now+1)!,now),null)
  assert.equal(resolveCandidateReference(s,'张测试',ref.slice(0,-1)+(ref.endsWith('a')?'b':'a'),now),null)
  assert.equal(resolveCandidateReference(s,'张测试',ref.replace(String(now),String(now-1)),now),null)
  assert.equal(resolveCandidateReference(s,'张测试',ref+'\n',now),null)
  for (const replace of ['https://example.com/test-account.png','PHP 开发工程师 _ 上海 15-30K','28岁 | 5年 | 本科']) {
    const changed=structuredClone(s),i=changed.strings.indexOf(replace);assert.ok(i>=0);changed.strings[i]='changed'
    assert.equal(resolveCandidateReference(changed,'张测试',ref,now),null)
  }
})
test('两张卡片所有识别信息相同：引用也不能解决歧义，零点击', async()=>{
  const cards=[people[0]!,{...people[0]!,y:3000}],s=loadedSnapshot(cards),ref=candidateReference(loadedCandidates(s)[0]!)!
  assert.equal(resolveCandidateReference(s,'张测试',ref),null)
  let clicks=0
  await assert.rejects(()=>new GreetExecutor({snapshot:async()=>s,click:async()=>{clicks++},sleep:async()=>{}})
    .greetVisible({names:['张测试'],selection:{name:'张测试',candidate_ref:ref}}),/不回退/)
  assert.equal(clicks,0)
})
test('选择后卡片年龄变化，不能仅凭名字继续点击', async()=>{
  const cards=structuredClone(people),ref=candidateReference(loadedCandidates(loadedSnapshot(cards))[1]!)!
  let scroll=0,clicks=0
  await assert.rejects(()=>new GreetExecutor({snapshot:async()=>loadedSnapshot(cards,scroll),
    scroll:async delta=>{scroll+=delta;cards[1]!.baseInfo='40岁 | 10年以上 | 大专'},click:async()=>{clicks++},sleep:async()=>{}})
    .greetVisible({names:['张测试'],selection:{name:'张测试',candidate_ref:ref}}),/不回退/)
  assert.equal(clicks,0)
})
function fakeSession(cards: Card[], onClick?:()=>void) {
  let inputs=0,closed=0
  const forbidden=async()=>{inputs++;throw Error('Unexpected input')}
  const session: BossSession={snapshot:async()=>loadedSnapshot(cards),click:async()=>{inputs++;onClick?.()},mouseWheel:forbidden,
    getUrl:async()=> 'https://www.zhipin.com/web/chat/recommend',close:async()=>{closed++},clickBrowse:forbidden,
    pressEscape:forbidden,clickAndType:forbidden,captureFullpage:async()=>{throw Error('Unexpected capture')}}
  return {session,counts:()=>({inputs,closed})}
}
const context=()=>({signal:new AbortController().signal,progress:()=>{}})
test('公开查询纯只读，回执包括完整卡片信息和候选引用',async()=>{
  const fake=fakeSession(people)
  const result=await createBossLookupCandidatesOperation(async()=>fake.session).execute({names:['张测试','不存在']},context())
  assert.equal(result.success,true);assert.equal(result.effect,'none');assert.deepEqual(fake.counts(),{inputs:0,closed:1})
  const groups=result.data.candidates as Array<{candidates:Array<{candidate_ref:string;card_info:{base_info:string}}> }>
  assert.equal(groups[0]!.candidates.length,2);assert.ok(groups[0]!.candidates.every(c=>/^boss-card-v1:/.test(c.candidate_ref)))
  assert.deepEqual(result.data.missing_names,['不存在'])
})
test('公开选定发送只发引用命中的一人，已打过不会重复发',async()=>{
  const cards=[structuredClone(people[0]!)],ref=candidateReference(loadedCandidates(loadedSnapshot(cards))[0]!)!,fake=fakeSession(cards,()=>{cards[0]!.sent=true})
  const op=createBossGreetSelectedOperation(async()=>fake.session)
  const result=await op.execute({name:'张测试',candidate_ref:ref},context())
  assert.equal(result.success,true);assert.equal(result.data.greeted,1);assert.equal(result.effect,'applied')
  const again=await op.execute({name:'张测试',candidate_ref:ref},context())
  assert.equal(again.success,true);assert.equal(again.data.already_greeted,true);assert.equal(again.effect,'none')
  assert.deepEqual(fake.counts(),{inputs:1,closed:2})
})
test('真实btn-continue状态可确认同一引用，其他按钮文案不能确认发送',async()=>{
  const cards=[structuredClone(people[0]!)],before=loadedSnapshot(cards)
  const ref=candidateReference(loadedCandidates(before)[0]!)!
  cards[0]!.sent=true
  const after=loadedSnapshot(cards)
  assert.equal(resolveCandidateReference(after,'张测试',ref)?.buttonText,'继续沟通')
  for (const replacement of ['btn btn-other','btn btn-continue-wrap']) {
    const unrelated=structuredClone(after),i=unrelated.strings.indexOf('btn btn-continue btn-outline')
    assert.ok(i>=0);unrelated.strings[i]=replacement
    assert.equal(loadedCandidates(unrelated)[0]!.buttonText,null)
    const fake=fakeSession(cards)
    let clicked=false
    fake.session.snapshot=async()=>clicked ? unrelated : before
    fake.session.click=async()=>{clicked=true}
    const result=await createBossGreetSelectedOperation(async()=>fake.session).execute({name:'张测试',candidate_ref:ref},context())
    assert.equal(result.code,'EXECUTION_UNKNOWN');assert.equal(result.effect,'unknown');assert.equal(result.retryable,false)
  }
})
test('公开引用不存在或参数缺失时零输入，不能降级姓名发送',async()=>{
  let connects=0
  const fake=fakeSession(people),op=createBossGreetSelectedOperation(async()=>{connects++;return fake.session})
  const missing=await op.execute({name:'张测试',candidate_ref:''},context())
  assert.equal(missing.code,'INVALID_ARGUMENT');assert.equal(connects,0)
  const invalid=await op.execute({name:'张测试',candidate_ref:`boss-card-v1:${Date.now()}:${'a'.repeat(64)}`},context())
  assert.equal(invalid.success,false);assert.equal(invalid.effect,'none');assert.deepEqual(fake.counts(),{inputs:0,closed:1})
})
test('公开发送后的画布或断连不能被标记为无副作用可重试',async()=>{
  for (const failure of ['canvas','connection']) {
    const cards=structuredClone(people),ref=candidateReference(loadedCandidates(loadedSnapshot(cards))[0]!)!
    let clicked=false,clicks=0
    const fake=fakeSession(cards)
    fake.session.click=async()=>{clicked=true;clicks++}
    fake.session.snapshot=async()=>{
      if (!clicked) return loadedSnapshot(cards)
      if (failure==='connection') throw Error('CDP request timed out')
      return withCanvas(loadedSnapshot(cards))
    }
    const result=await createBossGreetSelectedOperation(async()=>fake.session).execute({name:'张测试',candidate_ref:ref},context())
    assert.equal(result.code,'EXECUTION_UNKNOWN');assert.equal(result.effect,'unknown');assert.equal(result.retryable,false)
    assert.equal(clicks,1);assert.equal(fake.counts().closed,1)
  }
})
