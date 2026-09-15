import test from 'node:test'
import assert from 'node:assert/strict'
import {ocrTextMatches} from '../src/platform/ocrTextMatch.js'
import {ConversationAligner} from '../src/platform/aligner.js'

test('OCR formatting differs without changing original identity text',()=>{
  for(const [a,b] of [['“你好，世界！”','你好 世界'],['ＡＢＣ','abc'],['long OCR\n sentence','LONG OCR sentence']])assert.ok(ocrTextMatches(a!,b!))
  for(const [a,b] of [['好','不好'],['!','?'],['!',''],['👍','👎'],['金额100元','金额1000元'],['数量1.2公斤','数量12公斤'],['余额-100元','余额100元']])assert.equal(ocrTextMatches(a!,b!),false)
})

test('long text tolerates small OCR edits but protects negation',()=>{
  assert.ok(ocrTextMatches('这是一条用于测试文字匹配的消息','这是一条用于测试文宇匹配的消息'))
  assert.equal(ocrTextMatches('这是一条不能自动发送的重要消息','这是一条能自动发送的重要消息'),false)
  assert.equal(ocrTextMatches('This will not be sent automatically','This will be sent automatically'),false)
  assert.equal(ocrTextMatches('这是今天已经安排好的会议😀','这是今天已经安排好的会议😢'),false)
})

test('injected matcher retains first IDs/text and rejects gradual drift',()=>{
  const original='abcdefghij',variant='abcdefghiX',drift='abcdefghYX'
  const aligner=new ConversationAligner(ocrTextMatches)
  const first=aligner.align([{sender:'peer',text:original}],null)
  assert.equal(first.kind,'ok');if(first.kind!=='ok')return
  const id=first.messages[0]!.local_message_id
  const second=aligner.align([{sender:'peer',text:variant}],id)
  assert.equal(second.kind,'ok');if(second.kind==='ok')assert.deepEqual(second.messages,first.messages)
  assert.equal(aligner.align([{sender:'peer',text:drift}],id).kind,'gap')
  assert.equal(aligner.align([{sender:'self',text:original}],id).kind,'gap')
})

test('explicit exact aligner remains available; fuzzy duplicate overlaps remain ambiguous',()=>{
  const exact=new ConversationAligner((a,b)=>a===b)
  const first=exact.align([{sender:'peer',text:'“hello”'}],null)
  assert.equal(first.kind,'ok');if(first.kind!=='ok')return
  assert.equal(exact.align([{sender:'peer',text:'hello'}],first.messages[0]!.local_message_id).kind,'gap')
  const fuzzy=new ConversationAligner(ocrTextMatches)
  const repeated=fuzzy.align([{sender:'peer',text:'“hello”'},{sender:'peer',text:'hello'}],null)
  assert.equal(repeated.kind,'ok');if(repeated.kind!=='ok')return
  assert.equal(fuzzy.align([{sender:'peer',text:'hello'},{sender:'peer',text:'hello'}],repeated.messages[1]!.local_message_id).kind,'gap')
})

test('twenty thousand characters use a bounded band with prefix trimming',()=>{
  const start=Date.now()
  assert.equal(ocrTextMatches('甲'.repeat(20000),'乙'.repeat(20000)),false)
  assert.equal(ocrTextMatches('甲'.repeat(19999)+'乙','甲'.repeat(19999)+'丙'),true)
  assert.equal(ocrTextMatches('\uFDFA'.repeat(19999)+'甲','\uFDFA'.repeat(19999)+'乙'),false)
  assert.ok(Date.now()-start<2000)
})
