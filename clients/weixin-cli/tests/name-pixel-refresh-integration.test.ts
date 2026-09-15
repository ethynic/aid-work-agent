import test from 'node:test'
import assert from 'node:assert/strict'
import {randomUUID} from 'node:crypto'
import {type NameWindow,type NameDriver} from '../src/platform/nameSession.js'
import {createNameOperations} from '../src/operations/nameOperations.js'

test('单次完整读取的最新消息进入观察一次，下轮相同frame不重复',async()=>{
 const obj=(text:string,y:number):NameWindow['objects'][number]=>({type:'text',sender:'peer',text,bounds:{x0:10,y0:y,x1:100,y1:y+15},clipped:false,evidence_hash:'e'.repeat(64)})
 const base:NameWindow={unchanged:false,title:'synthetic',exact_name:true,unique_match:true,complete:true,input_empty:true,frame:'a'.repeat(64),evidence_ref:'dpapi:synthetic',objects:[obj('old',20)]}
 let phase=0,captures=0
 const driver:NameDriver=async(req,signal)=>{
  assert.equal(req.action,'read')
  if(phase===0)return base
  if(phase===2)return{unchanged:true,frame:'c'.repeat(64)}
  captures++;return{...base,frame:'c'.repeat(64),objects:[...base.objects,obj('newest',50)]}
 }
 const ops=createNameOperations({driver}),ctx={signal:new AbortController().signal,progress:()=>{}}
 const args={target_name:'synthetic',conversation_binding_id:randomUUID(),binding_version:1,account_identity_version:0,watermark:null}
 const first=await ops.observe.execute(args,ctx);assert.equal(first.success,true)
 const initial=first.data.ordered_messages as Array<{local_message_id:string}>
 phase=1
 const second=await ops.observe.execute({...args,watermark:{last_local_message_id:initial[0]!.local_message_id,window_fingerprint:String(first.data.window_fingerprint)}},ctx)
 assert.equal(second.success,true);assert.equal(captures,1)
 const latest=second.data.ordered_messages as Array<{local_message_id:string;text:string}>
 assert.deepEqual(latest.map(m=>m.text),['newest'])
 phase=2
 const third=await ops.observe.execute({...args,watermark:{last_local_message_id:latest[0]!.local_message_id,window_fingerprint:String(second.data.window_fingerprint)}},ctx)
 assert.equal(third.success,true);assert.deepEqual(third.data.ordered_messages,[]);assert.equal(captures,1)
})
