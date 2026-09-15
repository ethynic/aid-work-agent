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


test('恢复先冻结pending再继续原inFlight，gap与退避期间不触发poll',async()=>{
 const f=await fixture();let polls=0
 try{
  f.engine['opts'].api={sessionTaskGetDecision:async()=>{polls++;return{status:'pending'}}} as unknown as ApiClient
  f.task.watermark={last_local_message_id:'anchor',window_fingerprint:'fp'}
  f.task.pendingBatch={batchId:'pending',firstNewAt:f.now(),messages:[{sender:'peer',text:'synthetic',local_message_id:'new',source_evidence_ref:'local'}]}
  const inflight={decisionId:'decision',batchId:'prior',inputVersion:0,pollAt:0}
  f.task.inFlight=inflight;f.task.inputVersion=1;f.task.phase='decision_pending'
  f.respond({...f.complete,coverage:'gap',gap_reason:'alignment_broken'})
  for(let i=0;i<4;i++){await f.engine['actionUnit'](f.task,f.now());assert.equal(f.task.inFlight,inflight);assert.equal(polls,0);f.advance()}
  f.respond({...f.complete,ordered_messages:[{sender:'peer',text:'synthetic',local_message_id:'new',source_evidence_ref:'local'}]})
  await f.engine['actionUnit'](f.task,f.now())
  assert.equal(f.task.pendingBatch,null);assert.equal(f.task.inFlight,inflight);assert.equal(polls,0)
  await f.engine['actionUnit'](f.task,f.now());assert.equal(polls,1)
  const events=[...f.task.pendingEvents.values()]
  const recovered=events.filter(e=>e.type==='observation'&&(e.payload as {outcome?:string}).outcome==='recovered')
  assert.equal(recovered.length,1);assert.equal(JSON.stringify(recovered).includes('synthetic'),false)
  assert.equal(events.filter(e=>e.type==='batch').length,1)
 }finally{f.close()}
})
