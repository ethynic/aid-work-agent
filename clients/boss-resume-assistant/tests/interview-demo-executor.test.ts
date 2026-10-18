import assert from 'node:assert/strict'
import test from 'node:test'
import { InterviewDemoExecutor } from '../src/main/boss/InterviewDemoExecutor.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { indexedValues, type DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { actionSnapshot, text } from './chatActionFixture.js'
const REMARK='请带好简历',NOW=Date.parse('2026-10-18T12:00:00+08:00'),DATE='2026-10-19'
function harness(opts:{badRemark?:boolean;badDate?:boolean;cancelBlocked?:boolean;blockedOnce?:boolean;crossMonth?:boolean;denseValues?:boolean}={}) {
 let phase=0,typed='',date='',cancelClicks=0,typeCalls=0,clickCalls=0
 const inputs:Array<{x:number;y:number}>=[]
 const dateValue=opts.crossMonth?'2026-11-01':DATE
 const snapshot=async():Promise<DomSnapshot>=>{
  const extra=[text('约面试',1100,1000,70,30)]
  if(phase>0&&phase<5)extra.push(text('线下面试邀请',600,500,180,30),text('/140',1150,900,40,20),text(String(typed.length),1110,900,30,20),text('面试时间',600,1000,80,20),text('取消',1050,1150,80,30))
  if(phase===3)extra.push(text(opts.crossMonth?'1':'19',900,1100,20,20))
  const s=actionSnapshot({extra})
  if(phase>0&&phase<5){
   // Snapshot properties are distinct from strings: a stale unrelated date cannot satisfy this.
   const doc=s.documents[0]!,nodes=doc.nodes as typeof doc.nodes&{inputValue:{index:number[];value:number[]}|number[];textValue:{index:number[];value:number[]}|number[]}
   const values=nodes.nodeValue as number[],tags=nodes.nodeName as number[],parents=nodes.parentIndex as number[],attrs=nodes.attributes as number[][]
   const str=(value:string)=>{let i=s.strings.indexOf(value);if(i<0){i=s.strings.length;s.strings.push(value)}return i}
   nodes.inputValue=opts.denseValues?Array(values.length).fill(-1):{index:[],value:[]}
   nodes.textValue=opts.denseValues?Array(values.length).fill(-1):{index:[],value:[]}
   // TEXTAREA uses textValue in the real snapshot. The adjacent start-time INPUT must not make the date ambiguous.
   for(const [tag,value,b] of [['TEXTAREA',typed,[650,850,400,70]],['INPUT',date,[750,990,300,30]],['INPUT','10:00',[1062,990,180,30]]] as const){
    const ni=values.length;values.push(-1);tags.push(str(tag));parents.push(0);attrs.push([]);doc.layout.nodeIndex.push(ni);doc.layout.bounds.push([...b])
    for(const field of [nodes.textValue,nodes.inputValue])if(Array.isArray(field))field.push(-1)
    const field=tag==='TEXTAREA'?nodes.textValue:nodes.inputValue
    if(Array.isArray(field))field[ni]=str(value);else{field.index.push(ni);field.value.push(str(value))}
   }
  }
  return s
 }
 const deps={snapshot,click:async(p:{x:number;y:number})=>{
  inputs.push(p);clickCalls++
  if((phase===0||phase===5)&&p.y<1100){phase=1;return}
  if(p.y>1140){cancelClicks++;if(!opts.cancelBlocked)phase=5;return}
  if(phase===2){phase=3;return}
  if(phase===3&&p.y>1100){phase=4;date=opts.badDate?'2026-10-20':dateValue;return}
 },clickAndType:async(_p:unknown,_v:unknown,value:string)=>{typeCalls++;typed=opts.badRemark?'错误备注':value;phase=2},sleep:async()=>{},now:()=>opts.crossMonth?Date.parse('2026-10-31T12:00:00+08:00'):NOW}
 return {deps,inputs,counts:()=>({cancelClicks,typeCalls,clickCalls}),setPhase:(n:number)=>{phase=n},setRemark:(s:string)=>{typed=s}}
}
test('demo verifies actual remark and date values, cancels and never clicks send',async()=>{
 const h=harness(),executor=new InterviewDemoExecutor(h.deps),r=await executor.run({remark:REMARK})
 assert.deepEqual(r,{remark:REMARK,date:DATE});assert.equal(h.counts().cancelClicks,1);assert.equal(executor.state.execution.verification.status,'met');assert.equal(executor.state.businessEffect.completed_count,0)
 assert.ok(h.inputs.every(p=>p.y!==1233))
})
test('cross-month date uses fresh next-month snapshot and verifies first day actual value',async()=>{
 const h=harness({crossMonth:true}),r=await new InterviewDemoExecutor(h.deps).run({remark:REMARK});assert.equal(r.date,'2026-11-01')
})
test('dense textValue and inputValue verify only the field containing the actual input point',async()=>{
 const h=harness({denseValues:true}),r=await new InterviewDemoExecutor(h.deps).run({remark:REMARK})
 assert.deepEqual(r,{remark:REMARK,date:DATE});assert.equal(h.counts().typeCalls,1)
})
test('normal cancel with persistent form is retried exactly once, then remains a definite failure',async()=>{
 const h=harness({cancelBlocked:true}),executor=new InterviewDemoExecutor(h.deps)
 await assert.rejects(executor.run({remark:REMARK}),/仍打开/);assert.equal(h.counts().cancelClicks,2);assert.equal(executor.state.execution.ordinaryRetriesUsed,1)
})
test('direct input error has zero ordinary retries and never clicks send',async()=>{
 const h=harness();h.deps.clickAndType=async()=>{throw new WinClickError('guard refused',2)}
 const executor=new InterviewDemoExecutor(h.deps);await assert.rejects(executor.run({remark:REMARK}));assert.equal(executor.state.execution.ordinaryRetriesUsed,0);assert.equal(h.counts().cancelClicks,0)
})
test('same-length incorrect remark cannot satisfy the counter; one retry remains bounded',async()=>{
 const h=harness({badRemark:true}),executor=new InterviewDemoExecutor(h.deps)
 await assert.rejects(executor.run({remark:'正确备注'}),/备注文本/);assert.equal(h.counts().typeCalls,2);assert.equal(executor.state.execution.ordinaryRetriesUsed,1)
})
test('unrelated full strings date does not substitute for real input field value',async()=>{
 const h=harness({badDate:true}),snapshot=h.deps.snapshot
 h.deps.snapshot=async()=>{const s=await snapshot();s.strings.push(DATE);return s}
 await assert.rejects(new InterviewDemoExecutor(h.deps).run({remark:REMARK}),/日期字段/)
})
test('unreadable actual field value is inconclusive and not retried as a definite mismatch',async()=>{
 const h=harness(),snapshot=h.deps.snapshot;h.deps.snapshot=async()=>{const s=await snapshot();delete (s.documents[0]!.nodes as {textValue?:unknown}).textValue;return s}
 const executor=new InterviewDemoExecutor(h.deps);await assert.rejects(executor.run({remark:REMARK}),/备注文本/);assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})
test('a matching neighboring start-time value cannot satisfy a wrong date field',async()=>{
 const h=harness({badDate:true}),snapshot=h.deps.snapshot
 h.deps.snapshot=async()=>{const s=await snapshot(),nodes=s.documents[0]!.nodes as typeof s.documents[0]['nodes']&{inputValue:{index:number[];value:number[]}}
  const last=nodes.inputValue?.value.length-1
  if(last>=0)nodes.inputValue.value[last]=s.strings.push(DATE)-1
  return s}
 await assert.rejects(new InterviewDemoExecutor(h.deps).run({remark:REMARK}),/日期字段/)
})
test('overlapping textarea fields remain ambiguous even when only one exposes a readable value',async()=>{
 const h=harness(),snapshot=h.deps.snapshot
 h.deps.snapshot=async()=>{const s=await snapshot(),doc=s.documents[0]!,nodes=doc.nodes
  const ni=(nodes.nodeValue as number[]).length
  ;(nodes.nodeValue as number[]).push(-1);(nodes.nodeName as number[]).push(s.strings.indexOf('TEXTAREA'))
  doc.layout.nodeIndex.push(ni);doc.layout.bounds.push([650,850,400,70]);return s}
 const executor=new InterviewDemoExecutor(h.deps)
 await assert.rejects(executor.run({remark:REMARK}),/备注文本/);assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})
test('an already-open form is not silently overwritten',async()=>{
 const h=harness();h.setPhase(1);await assert.rejects(new InterviewDemoExecutor(h.deps).run({remark:REMARK}),/已处于打开/);assert.equal(h.counts().clickCalls,0)
})
