import test from 'node:test'
import assert from 'node:assert/strict'
import {ConversationAligner} from '../src/platform/aligner.js'
const b=(text:string)=>({sender:'peer' as const,text})
const opaque={sender:'system' as const,text:'',unsupported:true}
test('损坏旧prefix不丢未ACK尾链；gap恢复后复用pending ID',()=>{
 const a=new ConversationAligner();const base=a.align([b('旧历史'),b('水位消息')],null);assert.equal(base.kind,'ok');if(base.kind!=='ok')return
 const anchor=base.messages[1]!.local_message_id
 const next=a.align([opaque,b('水位消息'),b('待确认消息')],anchor);assert.equal(next.kind,'ok');if(next.kind!=='ok')return
 const pending=next.messages.at(-1)!.local_message_id
 assert.equal(a.align([opaque,b('水位消息'),opaque],anchor).kind,'gap')
 const recovered=a.align([opaque,b('水位消息'),b('待确认消息'),b('新消息')],anchor);assert.equal(recovered.kind,'ok');if(recovered.kind!=='ok')return
 assert.equal(recovered.messages.find(x=>x.text==='待确认消息')!.local_message_id,pending)
 assert.equal(new Set(recovered.messages.map(x=>x.local_message_id)).size,recovered.messages.length)
})
test('真正空基线不吞新媒体，历史已有媒体不会永久断层',()=>{
 const empty=new ConversationAligner();empty.align([],null);assert.equal(empty.align([opaque],null,true).kind,'gap')
 const a=new ConversationAligner();const first=a.align([b('水位消息'),opaque],null);assert.equal(first.kind,'ok');if(first.kind!=='ok')return
 const next=a.align([b('水位消息'),opaque,b('新回复')],first.messages[0]!.local_message_id);assert.equal(next.kind,'ok');if(next.kind!=='ok')return
 assert.equal(next.messages.at(-1)!.text,'新回复');assert.equal(next.messages.some(m=>m.text===''),false)
})
