import test from 'node:test'
import assert from 'node:assert/strict'
import {randomUUID} from 'node:crypto'
import {createNameOperations} from '../src/operations/nameOperations.js'
import {performance} from 'node:perf_hooks'
import {ocrTextMatches} from '../src/platform/ocrTextMatch.js'
import {newNameObjects,type NameWindow} from '../src/platform/nameSession.js'
import {ConversationAligner} from '../src/platform/aligner.js'

test('归一化引号全角空白大小写而不吞短句数字emoji语义变化',()=>{
 assert.equal(ocrTextMatches('请回复“HELLO WORLD”，谢谢。','请回复＂hello　world＂谢谢'),true)
 assert.equal(ocrTextMatches('今天下午请确认会议安排是否合适','今天下午请确人会议安排是否合适'),true)
 for(const [a,b] of [['好','不好'],['今天下午会议时间为12:30请确认','今天下午会议时间为13:30请确认'],['本次约定付款金额是12.50元整','本次约定付款金额是1250元整'],['好的😀','好的😢'],['今天下午会议安排已经确认好了😀','今天下午会议安排已经确认好了😢']])assert.equal(ocrTextMatches(a!,b!),false,a)
})

test('显式精确；默认OCR锚点保留最初正文防渐进漂移，重复消息各有ID',()=>{
 const bubble=(text:string)=>({sender:'peer' as const,text})
 const exact=new ConversationAligner((a,b)=>a===b);exact.align([bubble('原句。')],null)
 assert.equal(exact.align([bubble('原句')],exact.lastKnownId).kind,'gap')
 const fuzzy=new ConversationAligner()
 const baseline=fuzzy.align([bubble('abcdefghijabcdefghij')],null);assert.equal(baseline.kind,'ok')
 const anchor=fuzzy.lastKnownId
 assert.equal(fuzzy.align([bubble('Xbcdefghijabcdefghij')],anchor).kind,'ok')
 assert.equal(fuzzy.align([bubble('XYcdefghijabcdefghij')],anchor).kind,'ok')
 assert.equal(fuzzy.align([bubble('XYZdefghijabcdefghij')],anchor).kind,'gap')
 const duplicate=new ConversationAligner(ocrTextMatches)
 const first=duplicate.align([bubble('边界'),bubble('收到'),bubble('收到')],null);assert.equal(first.kind,'ok')
 if(first.kind!=='ok')return
 const next=duplicate.align([bubble('边界'),bubble('收到'),bubble('收到'),bubble('收到')],duplicate.lastKnownId);assert.equal(next.kind,'ok')
 if(next.kind!=='ok')return
 assert.equal(new Set(next.messages.map(m=>m.local_message_id)).size,4)
 assert.deepEqual(next.messages.slice(0,3).map(m=>m.local_message_id),first.messages.map(m=>m.local_message_id))
})

test('两万字输入保持有界计算，超限拒绝',()=>{
 const original='a'.repeat(20000),changed='a'.repeat(19999)+'b',start=performance.now()
 assert.equal(ocrTextMatches(original,changed),true)
 assert.equal(ocrTextMatches(original,'b'.repeat(20000)),false)
 assert.equal(ocrTextMatches(original+'a',original+'a'),false)
 assert.ok(performance.now()-start<5000)
})


test('名称对象对齐容忍引号与错字，发送方数字变化和相同追加不丢失',()=>{
 const obj=(text:string):NameWindow['objects'][number]=>({type:'text',sender:'peer',text,bounds:{x0:1,y0:1,x1:20,y1:20},clipped:false,evidence_hash:'a'.repeat(64)})
 const old=obj('请确认“今天下午会议安排已经确定”')
 const seen=obj('请确认今天下午会议安排已经确走')
 assert.deepEqual(newNameObjects({objects:[old]},{objects:[seen,obj('新增回复')]}),[obj('新增回复')])
 assert.equal(newNameObjects({objects:[old]},{objects:[{...seen,sender:'self'}]}),null)
 assert.equal(newNameObjects({objects:[obj('本次金额约定为100元请确认')]},{objects:[obj('本次金额约定为200元请确认')]}),null)
 assert.deepEqual(newNameObjects({objects:[obj('收到')]},{objects:[obj('收到'),obj('收到')]}),[obj('收到')])
})


test('名称观察格式漂移保留初见正文和ID，新回复只追加一次',async()=>{
 const first='请确认“HELLO WORLD”，谢谢。',variant='请确认＂hello　world＂谢谢'
 const obj=(text:string,y:number):NameWindow['objects'][number]=>({type:'text',sender:'peer',text,bounds:{x0:1,y0:y,x1:200,y1:y+15},clipped:false,evidence_hash:'a'.repeat(64)})
 let phase=0
 const ops=createNameOperations({driver:async()=>({unchanged:false,title:'synthetic',exact_name:true,unique_match:true,complete:true,input_empty:true,evidence_ref:'dpapi:synthetic',frame:(phase?'b':'a').repeat(64),objects:phase?[obj(variant,20),obj('新的回复',50)]:[obj(first,20)]})})
 const args={target_name:'synthetic',conversation_binding_id:randomUUID(),binding_version:1,account_identity_version:0,watermark:null},ctx={signal:new AbortController().signal,progress:()=>{}}
 const baseline=await ops.observe.execute(args,ctx);assert.equal(baseline.success,true)
 const old=baseline.data.ordered_messages as Array<{text:string;local_message_id:string}>
 phase=1
 const next=await ops.observe.execute({...args,watermark:{last_local_message_id:old[0]!.local_message_id,window_fingerprint:String(baseline.data.window_fingerprint)}},ctx)
 assert.equal(next.success,true)
 const added=next.data.ordered_messages as Array<{text:string;local_message_id:string}>
 assert.deepEqual(added.map(m=>m.text),['新的回复'])
 const repeated=await ops.observe.execute({...args,watermark:{last_local_message_id:added[0]!.local_message_id,window_fingerprint:String(next.data.window_fingerprint)}},ctx)
 assert.equal(repeated.success,true);assert.deepEqual(repeated.data.ordered_messages,[])
})
