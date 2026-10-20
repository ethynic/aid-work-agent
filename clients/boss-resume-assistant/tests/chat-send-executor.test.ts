import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatSendExecutor, locateSendButton } from '../src/main/boss/ChatSendExecutor.js'
import { chatContactRef } from '../src/main/boss/BusinessOperationState.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { actionSnapshot, text } from './chatActionFixture.js'
const MSG='你好，我们聊聊岗位'
function harness() {
 let editor='', outgoing:string[]=[]
 const counts={type:0,send:0},state={contact:'合成甲',land:true,deliver:true,clear:true}
 const deps={snapshot:async()=>actionSnapshot({editor,outgoing,contact:state.contact}),sleep:async()=>{},clickAndType:async(_p:unknown,_v:unknown,value:string)=>{counts.type++;if(state.land)editor+=value},click:async()=>{counts.send++;if(state.deliver)outgoing.push(MSG);if(state.clear)editor=''}}
 return {deps,counts,state,setEditor:(s:string)=>{editor=s},outgoing}
}
test('same-conversation editor clears and a new outgoing exact message proves success even message remains in strings',async()=>{
 const h=harness(),executor=new ChatSendExecutor(h.deps),r=await executor.sendMessage({message:MSG})
 assert.equal(r.sent,true);assert.deepEqual(h.counts,{type:1,send:1});assert.equal(executor.state.businessEffect.status,'confirmed')
})
test('dry run verifies exact editor value and never clicks send',async()=>{
 const h=harness(),r=await new ChatSendExecutor(h.deps).sendMessage({message:MSG,dryRun:true});assert.equal(r.sent,false);assert.deepEqual(h.counts,{type:1,send:0})
})
test('old identical outgoing or incoming text never substitutes for a new outbound message',async()=>{
 const h=harness();h.outgoing.push(MSG);h.state.deliver=false
 const executor=new ChatSendExecutor(h.deps);await assert.rejects(executor.sendMessage({message:MSG}),/无法确认/)
 assert.equal(h.counts.send,1);assert.equal(executor.state.businessEffect.status,'unknown')
})
test('editor still populated after send is unknown and does not resend',async()=>{
 const h=harness();h.state.clear=false
 await assert.rejects(new ChatSendExecutor(h.deps).sendMessage({message:MSG}),/无法确认/);assert.equal(h.counts.send,1)
})
test('normal typing with readable empty editor retries once with fresh coordinates',async()=>{
 const h=harness();let types=0;h.deps.clickAndType=async(_p,_v,value)=>{types++;if(types===2)h.setEditor(value)}
 const executor=new ChatSendExecutor(h.deps);assert.equal((await executor.sendMessage({message:MSG,dryRun:true})).sent,false)
 assert.equal(types,2);assert.equal(executor.state.execution.ordinaryRetriesUsed,1)
})
test('two empty typing postconditions stop before external submission',async()=>{
 const h=harness();h.state.land=false;const executor=new ChatSendExecutor(h.deps)
 await assert.rejects(executor.sendMessage({message:MSG}),/未完整落地/);assert.deepEqual(h.counts,{type:2,send:0});assert.equal(executor.state.businessEffect.status,'not_started')
})
test('different draft is preserved and exact existing draft is not appended twice',async()=>{
 const h=harness();h.setEditor('用户的草稿');await assert.rejects(new ChatSendExecutor(h.deps).sendMessage({message:MSG}),/已有不同内容/);assert.equal(h.counts.type,0)
 h.setEditor(MSG);assert.equal((await new ChatSendExecutor(h.deps).sendMessage({message:MSG})).sent,true);assert.equal(h.counts.type,0)
})
test('conversation changing during preparation prevents the final click',async()=>{
 const h=harness(),type=h.deps.clickAndType;h.deps.clickAndType=async(...args)=>{await type(...args);h.state.contact='合成乙'}
 await assert.rejects(new ChatSendExecutor(h.deps).sendMessage({message:MSG}),/身份/);assert.equal(h.counts.send,0)
})
test('recovery guard rejects a different original conversation before any input',async()=>{
 const h=harness(),ref=chatContactRef(actionSnapshot({contact:'合成乙'}))!
 await assert.rejects(new ChatSendExecutor(h.deps).sendMessage({message:MSG,expectedContactRef:ref}),/绑定不一致/);assert.deepEqual(h.counts,{type:0,send:0})
})
test('activity/rejection labels do not alter stable contact binding; demographic changes do',()=>{
 assert.equal(chatContactRef(actionSnapshot({status:'在线'})),chatContactRef(actionSnapshot({status:'已标记不合适'})))
 assert.notEqual(chatContactRef(actionSnapshot({demographic:'28岁'})),chatContactRef(actionSnapshot({demographic:'35岁'})))
})
test('direct native typing error has zero ordinary retries',async()=>{
 const h=harness();let typed=0;h.deps.clickAndType=async()=>{typed++;throw new WinClickError('native input failed',2)}
 const executor=new ChatSendExecutor(h.deps);await assert.rejects(executor.sendMessage({message:MSG}));assert.equal(typed,1);assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})
test('missing or ambiguous send button never emits message input',()=>{
 assert.equal(locateSendButton(actionSnapshot({send:false})).count,0)
 assert.equal(locateSendButton(actionSnapshot({extra:[text('发送',1000,1000,80,20)]})).count,2)
 assert.equal(locateSendButton(actionSnapshot({extra:[text('发送',100,1000,80,20)]})).count,1)
})
test('lack of a uniquely scoped editor is unknown, never a full-strings fallback',async()=>{
 const h=harness();h.deps.snapshot=async()=>actionSnapshot({editor:null,friend:MSG})
 await assert.rejects(new ChatSendExecutor(h.deps).sendMessage({message:MSG}),/编辑区/);assert.deepEqual(h.counts,{type:0,send:0})
})
