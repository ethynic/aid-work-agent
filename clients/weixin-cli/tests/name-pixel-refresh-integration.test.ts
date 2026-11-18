import test from 'node:test'
import assert from 'node:assert/strict'
import {randomUUID} from 'node:crypto'
import {createNameOperations} from '../src/operations/nameOperations.js'

test('单次完整读取的最新消息进入观察一次，下轮相同frame不重复',async()=>{
 const row=(runtime_id:string,text:string,y:number)=>({runtime_id,kind:'text',sender:'peer',text,bounds:{x0:10,y0:y,x1:100,y1:y+15},clipped:false})
 const base={title:'synthetic',session_identity:'123:42:17.1',is_group:false,input_empty:true,rows:[row('a','old',20)]}
 let phase=0
 const ops=createNameOperations({
  observeCurrentOnly:true,
  nativeDriver:async()=>phase===0?base:{...base,rows:[...base.rows,row('b','newest',50)]},
 }),ctx={signal:new AbortController().signal,progress:()=>{}}
 const args={target_name:'synthetic',conversation_binding_id:randomUUID(),binding_version:1,account_identity_version:0,watermark:null}
 const first=await ops.observe.execute(args,ctx);assert.equal(first.success,true)
 const initial=first.data.ordered_messages as Array<{local_message_id:string}>
 phase=1
 const second=await ops.observe.execute({...args,watermark:{last_local_message_id:initial[0]!.local_message_id,window_fingerprint:String(first.data.window_fingerprint)}},ctx)
 assert.equal(second.success,true)
 const latest=second.data.ordered_messages as Array<{local_message_id:string;text:string}>
 assert.deepEqual(latest.map(m=>m.text),['newest'])
 phase=2
 const third=await ops.observe.execute({...args,watermark:{last_local_message_id:latest[0]!.local_message_id,window_fingerprint:String(second.data.window_fingerprint)}},ctx)
 assert.equal(third.success,true);assert.deepEqual(third.data.ordered_messages,[])
})
