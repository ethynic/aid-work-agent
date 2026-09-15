import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtempSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import type {ApiClient} from '../src/apiClient.js'
import {SessionTaskEngine,type ObserverResult} from '../src/sessionTasks/engine.js'

async function fixture(){
 const home=mkdtempSync(join(tmpdir(),'gap-recovery-'))
 let now=100000,reads=0
 const observedWatermarks:unknown[]=[]
 const complete:ObserverResult={observation_id:'ob',account_identity_version:0,conversation_binding_id:'binding',binding_version:0,observed_at:new Date(now).toISOString(),coverage:'complete_window',ordered_messages:[],window_fingerprint:'fp',gap_reason:null}
 let response=complete
 const engine=new SessionTaskEngine({api:{} as ApiClient,runtimeHome:home,runtimeInstanceId:'test',now:()=>now,crypto:{protect:async s=>Buffer.from(s).toString('base64'),unprotect:async s=>Buffer.from(s,'base64').toString()},observer:async(_task,request)=>{reads++;observedWatermarks.push(structuredClone(request.watermark));return structuredClone(response)}})
 await engine['adoptTask']({assignment_id:'assignment',task_id:'task',spec:{},spec_revision:1,fence:1,control_epoch:1,lease_seconds:3600,conversation_binding_id:'binding'})
 const task=engine['tasks'].get('task')!
 return{engine,task,complete,respond:(r:ObserverResult)=>{response=r},advance:()=>{now=task.observeDueAt+1},now:()=>now,reads:()=>reads,observedWatermarks,close:()=>rmSync(home,{recursive:true,force:true})}
}

test('five gaps preserve watermark and pending messages, then resume one batch without rebaseline',async()=>{
 const f=await fixture()
 try{
  f.task.watermark={last_local_message_id:'anchor',window_fingerprint:'fp'}
  f.task.pendingBatch={batchId:'pending',firstNewAt:f.now(),messages:[{sender:'peer',text:'pending message',local_message_id:'new',source_evidence_ref:'local'}]}
  f.task.inputVersion=1;f.task.phase='observing'
  f.respond({...f.complete,coverage:'gap',gap_reason:'alignment_broken'})
  for(let i=0;i<5;i++){await f.engine['actionUnit'](f.task,f.now());assert.notEqual(f.task.phase,'blocked');assert.equal(f.task.watermark.last_local_message_id,'anchor');assert.equal(f.task.pendingBatch?.messages.length,1);assert.ok(f.task.observeDueAt-f.now()<=30000);f.advance()}
  assert.equal(f.reads(),5)
  f.respond({...f.complete,ordered_messages:[{sender:'peer',text:'pending message',local_message_id:'new',source_evidence_ref:'local'}]})
  await f.engine['actionUnit'](f.task,f.now())
  assert.equal(f.task.gapStreak,0);assert.equal(f.task.watermark.last_local_message_id,'new');assert.equal(f.task.pendingBatch,null);assert.equal(f.task.decisionQueue.length,1)
  const events=[...f.task.pendingEvents.values()]
  assert.equal(events.filter(e=>e.type==='baseline').length,0);assert.equal(events.filter(e=>e.type==='batch').length,1)
  assert.ok(f.observedWatermarks.every(w=>(w as {last_local_message_id:string}).last_local_message_id==='anchor'))
 }finally{f.close()}
})

test('gap retry waits for due time and respects pause; event ACK cannot submit a queued decision',async()=>{
 const f=await fixture()
 try{
  f.task.watermark={last_local_message_id:'anchor',window_fingerprint:'fp'}
  f.respond({...f.complete,coverage:'gap',gap_reason:'alignment_broken'})
  await f.engine['actionUnit'](f.task,f.now())
  f.task.decisionQueue.push({batchId:'q',batchSeq:0,inputVersion:1})
  await f.engine['drainDecisionQueue'](f.task,f.now())
  await f.engine['actionUnit'](f.task,f.now());assert.equal(f.reads(),1);assert.equal(f.task.decisionQueue.length,1)
  f.advance();f.task.gate='paused_control';await f.engine['actionUnit'](f.task,f.now());assert.equal(f.reads(),1)
 }finally{f.close()}
})

test('identity mismatch remains blocked and is not made retryable',async()=>{
 const f=await fixture()
 try{
  f.respond({...f.complete,conversation_binding_id:'wrong'})
  await f.engine['actionUnit'](f.task,f.now());assert.equal(f.task.phase,'blocked')
  f.advance();await f.engine['actionUnit'](f.task,f.now());assert.equal(f.reads(),1)
 }finally{f.close()}
})
