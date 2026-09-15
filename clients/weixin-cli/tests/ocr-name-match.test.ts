import test from 'node:test'
import assert from 'node:assert/strict'
import {ocrNameMatches} from '../src/platform/ocrTextMatch.js'
import {uniqueOcrName,uniqueContactRow,readNameSession,hasNewMessageOverlay} from '../src/platform/nameSession.js'
import {ConversationAligner} from '../src/platform/aligner.js'

const row=(text:string,y=50,score=.99)=>({text,score,x0:20,x1:130,y0:y,y1:y+15})
test('name permits one OCR edit with uniqueness over every approximate candidate',()=>{
  assert.ok(ocrNameMatches('WayneLu','WayneLv'))
  assert.ok(ocrNameMatches('WayneLu','ＷａｙｎｅＬｕ'))
  assert.equal(ocrNameMatches('张三','张四'),false)
  assert.equal(ocrNameMatches('WayneLu','OtherPerson'),false)
  assert.equal(uniqueOcrName([row('WayneLv')],'WayneLu').text,'WayneLv')
  assert.throws(()=>uniqueOcrName([row('WayneLu'),row('WayneLv',80,.5)],'WayneLu'))
})
test('contact labels tolerate OCR edits but retain section and hidden-row gates',()=>{
  const rows=[row('联系入',10),row('WayneLv',50),row('搜索网路',120)]
  assert.equal(uniqueContactRow(rows,300,200,'WayneLu').text,'WayneLv')
  assert.throws(()=>uniqueContactRow(rows.slice(0,2),300,200,'WayneLu'))
  assert.throws(()=>uniqueContactRow([...rows,row('更夕',80)],300,200,'WayneLu'))
})
test('generic OCR aligner defaults to fuzzy and keeps original text',()=>{
  const aligner=new ConversationAligner()
  const first=aligner.align([{sender:'peer',text:'“你好！”'}],null)
  assert.equal(first.kind,'ok');if(first.kind!=='ok')return
  const again=aligner.align([{sender:'peer',text:'你好'}],first.messages[0]!.local_message_id)
  assert.deepEqual(again,first)
})
test('read accepts uniquely confirmed near-name and overlay label typo',async()=>{
  const result=await readNameSession('WayneLu',{driver:async()=>({unchanged:false,title:'WayneLv',exact_name:true,unique_match:true,complete:true,input_empty:true,evidence_ref:'dpapi:test',frame:'a'.repeat(64),objects:[]})})
  assert.equal(result.unchanged,false)
  assert.ok(hasNewMessageOverlay([{text:'0条新消自',score:.99,x0:1690,y0:193,x1:1835,y1:228}],{x0:613,y0:150,x1:1914,y1:1570}))
})
