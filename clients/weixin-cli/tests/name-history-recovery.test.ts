import test from 'node:test'
import assert from 'node:assert/strict'
import { ConversationAligner } from '../src/platform/aligner.js'

test('a clipped older message stays opaque while a new bottom message survives',()=>{
  const p=(text:string)=>({sender:'peer' as const,text})
  const a=new ConversationAligner(),base=a.align([p('old'),p('anchor')],null)
  assert.ok(base.kind==='ok')
  const result=a.align([{...p(''),unsupported:true},p('anchor'),p('new')],base.messages[1]!.local_message_id)
  assert.ok(result.kind==='ok')
  assert.equal(result.messages.at(-1)!.text,'new')
})

test('baseline opaque slots remain history without hiding newly appended media',()=>{
  const p=(text:string)=>({sender:'peer' as const,text})
  const opaque={sender:'system' as const,text:'',unsupported:true}
  const a=new ConversationAligner()
  const base=a.align([p('anchor'),opaque],null)
  assert.ok(base.kind==='ok');assert.equal(base.messages.length,1)
  const anchor=base.messages[0]!.local_message_id
  assert.equal(a.lastKnownId,anchor)
  assert.deepEqual(a.align([p('anchor'),opaque],anchor),base)
  assert.equal(a.align([p('anchor'),opaque,opaque],anchor).kind,'gap')
  const next=a.align([p('anchor'),opaque,p('new')],anchor)
  assert.ok(next.kind==='ok');assert.equal(next.messages.length,2)
  assert.equal(next.messages[0]!.local_message_id,anchor)
  assert.equal(next.messages[1]!.text,'new')
  const empty=new ConversationAligner()
  const mediaBase=empty.align([opaque],null)
  assert.ok(mediaBase.kind==='ok');assert.deepEqual(mediaBase.messages,[])
  assert.equal(empty.lastKnownId,null)
  assert.equal(empty.align([opaque,opaque],null,true).kind,'gap')
  assert.deepEqual(empty.align([opaque],null,true),mediaBase)
  const first=empty.align([opaque,p('first')],null,true)
  assert.ok(first.kind==='ok');assert.equal(first.messages.length,1)
  assert.deepEqual(empty.align([opaque,p('first')],null,true),first)
})
