import test from 'node:test'
import assert from 'node:assert/strict'
import {textFromBubbleRegions} from '../src/platform/bubbleSegmentation.js'
import {ConversationAligner} from '../src/platform/aligner.js'

test('同一消息两OCR框横向重叠4px不丢失正文，重复观察不追加',()=>{
 const region={sender:'self' as const,bounds:{x0:10,y0:5,x1:210,y1:50}}
 const result=textFromBubbleRegions([region],[{text:'前半正文',x0:20,y0:10,x1:100,y1:40},{text:'后半正文',x0:96,y0:9,x1:190,y1:39}])
 assert.equal(result.kind,'candidates');if(result.kind!=='candidates')return
 assert.equal(result.messages.length,1)
 assert.equal(result.messages[0]!.text.replace(/\s/g,''),'前半正文后半正文')
 const aligner=new ConversationAligner(),first=aligner.align(result.messages,null)
 assert.equal(first.kind,'ok');if(first.kind!=='ok')return
 const next=aligner.align(result.messages,aligner.lastKnownId)
 assert.equal(next.kind,'ok');if(next.kind!=='ok')return
 assert.equal(next.messages.length,1);assert.equal(next.messages[0]!.local_message_id,first.messages[0]!.local_message_id)
})

test('轻微越界文字只归属一个气泡，不能重复生成两条正文',()=>{
 const regions=[{sender:'peer' as const,bounds:{x0:10,y0:10,x1:100,y1:45}},{sender:'self' as const,bounds:{x0:110,y0:10,x1:210,y1:45}}]
 const result=textFromBubbleRegions(regions,[{text:'第一条',x0:9,y0:15,x1:105,y1:40},{text:'第二条',x0:115,y0:15,x1:205,y1:40}])
 assert.equal(result.kind,'candidates');if(result.kind!=='candidates')return
 assert.deepEqual(result.messages.map(m=>m.text),['第一条','第二条'])
})
