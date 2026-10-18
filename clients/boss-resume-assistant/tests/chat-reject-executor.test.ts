import assert from 'node:assert/strict'
import test from 'node:test'
import { ChatRejectExecutor } from '../src/main/boss/ChatRejectExecutor.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { REJECT_REASON_OPTIONS } from '../src/main/actions/reasonMapping.js'
import { chatContactRef } from '../src/main/boss/BusinessOperationState.js'
import { actionSnapshot, text } from './chatActionFixture.js'
const reject=text('不合适',1100,1000,80,36), confirm=text('确定',800,700,80,36)
function harness(after:ReturnType<typeof actionSnapshot>, middle?:ReturnType<typeof actionSnapshot>) {
 let clicks=0
 const before=actionSnapshot({extra:[reject]})
 const deps={snapshot:async()=>clicks===0?before:middle&&clicks===1?middle:after,sleep:async()=>{},click:async()=>{clicks++}}
 return {deps,clicks:()=>clicks}
}
test('original loaded conversation removed while other entries persist proves rejected',async()=>{
 const h=harness(actionSnapshot({contact:'合成乙',list:['合成乙'],extra:[reject]})),executor=new ChatRejectExecutor(h.deps)
 await executor.rejectCurrent();assert.equal(h.clicks(),1);assert.equal(executor.state.businessEffect.status,'confirmed')
})
test('original conversation hidden from layout is also a stable positive',async()=>{
 const h=harness(actionSnapshot({hidden:'合成甲',extra:[reject]}));await new ChatRejectExecutor(h.deps).rejectCurrent();assert.equal(h.clicks(),1)
})
test('confirmation completes only after original list entry is removed',async()=>{
 const h=harness(actionSnapshot({list:['合成乙']}),actionSnapshot({extra:[reject,confirm]}))
 await new ChatRejectExecutor(h.deps).rejectCurrent();assert.equal(h.clicks(),2)
})
test('a changed panel header alone never proves rejected',async()=>{
 const h=harness(actionSnapshot({contact:'合成乙',extra:[reject]})),executor=new ChatRejectExecutor(h.deps)
 await assert.rejects(executor.rejectCurrent());assert.equal(h.clicks(),1);assert.equal(executor.state.businessEffect.status,'unknown')
})
test('reject button disappearing alone never proves rejected',async()=>{
 const h=harness(actionSnapshot()),executor=new ChatRejectExecutor(h.deps)
 await assert.rejects(executor.rejectCurrent());assert.equal(h.clicks(),1);assert.equal(executor.state.businessEffect.status,'unknown')
})
test('missing or loading whole list is unknown rather than target removal',async()=>{
 for(const after of [actionSnapshot({noList:true}),actionSnapshot({list:['合成乙'],extra:[text('加载中',300,300)]})]) {
  const h=harness(after);await assert.rejects(new ChatRejectExecutor(h.deps).rejectCurrent());assert.equal(h.clicks(),1)
 }
})
test('loss of other previously loaded rows is not the target-removal signal',async()=>{
 const before=actionSnapshot({list:['合成甲','合成乙','合成丙'],extra:[reject]}),after=actionSnapshot({list:['合成丙']});let clicks=0
 await assert.rejects(new ChatRejectExecutor({snapshot:async()=>clicks?after:before,click:async()=>{clicks++},sleep:async()=>{}}).rejectCurrent());assert.equal(clicks,1)
})
test('reason selection is not guessed and original action is not replayed',async()=>{
 const h=harness(actionSnapshot({extra:[reject,text(REJECT_REASON_OPTIONS[0]!,800,700)]}))
 await assert.rejects(new ChatRejectExecutor(h.deps).rejectCurrent(),/原因选择/);assert.equal(h.clicks(),1)
})
test('ambiguous confirm never produces a second click',async()=>{
 const h=harness(actionSnapshot({extra:[reject,confirm,text('确定',950,750)]}))
 await assert.rejects(new ChatRejectExecutor(h.deps).rejectCurrent());assert.equal(h.clicks(),1)
})
test('direct native failure has no ordinary retry and records trusted zero-input refusal',async()=>{
 let clicks=0;const executor=new ChatRejectExecutor({snapshot:async()=>actionSnapshot({extra:[reject]}),click:async()=>{clicks++;throw new WinClickError('guard refused',2)},sleep:async()=>{}})
 await assert.rejects(executor.rejectCurrent());assert.equal(clicks,1);assert.equal(executor.state.businessEffect.status,'not_started');assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})
test('current-session recovery binding rejects changed identity before writing',async()=>{
 const h=harness(actionSnapshot({list:['合成乙']})),ref=chatContactRef(actionSnapshot({contact:'合成乙'}))!
 await assert.rejects(new ChatRejectExecutor(h.deps).rejectCurrent({expectedContactRef:ref}),/绑定不一致/);assert.equal(h.clicks(),0)
})
test('cancellation is checked before any input',async()=>{
 const h=harness(actionSnapshot()),c=new AbortController();c.abort()
 await assert.rejects(new ChatRejectExecutor({...h.deps,signal:c.signal}).rejectCurrent(),/取消/);assert.equal(h.clicks(),0)
})


test('insertion among original survivors is not proof of rejecting the original conversation',async()=>{
 const before=actionSnapshot({list:['合成甲','合成乙','合成丙'],extra:[reject]}),after=actionSnapshot({list:['合成乙','合成丁','合成丙']})
 let clicks=0
 const executor=new ChatRejectExecutor({snapshot:async()=>clicks?after:before,click:async()=>{clicks++},sleep:async()=>{}})
 await assert.rejects(executor.rejectCurrent())
 assert.equal(clicks,1);assert.equal(executor.state.businessEffect.status,'unknown')
})
