import assert from 'node:assert/strict'
import test from 'node:test'
import { ResumeConsentExecutor, attachmentPreviewState } from '../src/main/boss/ResumeConsentExecutor.js'
import { chatContactRef } from '../src/main/boss/BusinessOperationState.js'
import { WinClickError } from '../src/main/input/WinMouseClicker.js'
import { actionSnapshot, text } from './chatActionFixture.js'
import { buildChatSnapshot } from './chatReadFixture.js'
const MARKER='对方想发送附件简历给您，您是否同意'
const request=text(MARKER,240,500,290,25),prompt=text(MARKER,650,1000,300,25),agree=text('同意',1050,1000,60,25),preview=text('点击预览附件简历',700,800,180,25)
function harness(opts:{unchanged?:boolean;previewFail?:boolean;switchContact?:boolean;encrypted?:boolean;loadingPolls?:number;loadingForever?:boolean;keepPreviewAfterEscape?:boolean}={}) {
 let phase=0, clicks=0,escapes=0,loadingReads=0
 const deps={snapshot:async()=>{
  if(phase===0)return actionSnapshot({extra:[opts.encrypted?{...request,text:MARKER.replace('附件','加密附件')}:request]})
  if(phase===1)return actionSnapshot({extra:[request,prompt,agree]})
  if(opts.unchanged)return actionSnapshot({extra:[request,prompt,agree]})
  if(phase===2)return actionSnapshot({contact:opts.switchContact?'合成乙':'合成甲',extra:[preview]})
  if(opts.previewFail)return actionSnapshot({extra:[preview]})
  if(phase===3)return pdfPreviewSnapshot({loading:opts.loadingForever || loadingReads++ < (opts.loadingPolls??0)})
  return opts.keepPreviewAfterEscape?pdfPreviewSnapshot():actionSnapshot({extra:[preview,text('个人优势',700,400),text('工作经历',700,450)]})
 },click:async()=>{clicks++;phase++},pressEscape:async()=>{escapes++;phase=4},sleep:async()=>{}}
 return {deps,counts:()=>({clicks,escapes})}
}

function pdfPreviewSnapshot(opts:{loading?:boolean;hidden?:boolean;offscreen?:boolean;ordinaryFrame?:boolean;unboundCanvas?:boolean;sections?:boolean;backgroundCanvas?:boolean;canvasOutside?:boolean}={}) {
 const cls=opts.ordinaryFrame?'recommend-iframe':'attachment-box attachment-iframe'
 const snap=actionSnapshot({extra:[preview,{tag:'DIV',cls:'resume-detail resume-detail-chat',children:[{tag:'DIV',cls:'attachment-view',children:[
  {tag:'IFRAME',cls,...(opts.hidden?{}:{bounds:[opts.offscreen?2000:248,50,765,894] as [number,number,number,number]})},
 ]}]},text('个人优势',700,400),text('工作经历',700,450),...(opts.backgroundCanvas?[{tag:'CANVAS',bounds:[10,50,1000,1200] as [number,number,number,number]}]:[])]})
 const child=buildChatSnapshot([{tag:'DIV',cls:'bzl-office-pdf',children:[{tag:'DIV',cls:'bzl-office-pdf-wrapper pdfViewer',children:
  opts.loading?[]:opts.sections?[text('个人优势',100,200),text('工作经历',100,300)]:[{tag:'DIV',cls:'page',children:[{tag:'DIV',cls:'canvasWrapper',children:[
   {tag:'CANVAS',attrs:{role:'presentation'},bounds:[opts.canvasOutside?900:0,0,749,1059]},
  ]}]}],
 }]}])
 const indexes=child.strings.map(value=>{let index=snap.strings.indexOf(value);if(index<0){index=snap.strings.length;snap.strings.push(value)}return index})
 const doc=child.documents[0]!,nodes=doc.nodes
 nodes.nodeValue=(nodes.nodeValue as number[]).map(index=>index<0?index:indexes[index]!)
 nodes.nodeName=(nodes.nodeName as number[]).map(index=>indexes[index]!)
 nodes.attributes=(nodes.attributes as number[][]).map(pairs=>pairs.map(index=>indexes[index]!))
 const main=snap.documents[0]!,frame=(main.nodes.nodeName as number[]).findIndex(index=>snap.strings[index]==='IFRAME')
 main.nodes.contentDocumentIndex={index:[frame],value:[1]}
 snap.documents.push(doc)
 if(opts.unboundCanvas){main.nodes.contentDocumentIndex={index:[],value:[]}}
 return snap
}
test('accept same conversation only after processing banner removed and new attachment appears',async()=>{
 const h=harness(),executor=new ResumeConsentExecutor(h.deps),r=await executor.acceptAll({limit:1})
 assert.equal(r.accepted,1);assert.deepEqual(h.counts(),{clicks:2,escapes:0});assert.equal(executor.state.businessEffect.completed_count,1)
})
test('encrypted request uses the same explicit acceptance postcondition',async()=>{
 const h=harness({encrypted:true});assert.equal((await new ResumeConsentExecutor(h.deps).acceptAll({limit:1})).accepted,1)
})
test('accept preview verifies open and closed states after confirmed business effect',async()=>{
 const h=harness(),r=await new ResumeConsentExecutor(h.deps).acceptAll({limit:1,preview:true})
 assert.equal(r.accepted,1);assert.equal(r.previewed,1);assert.deepEqual(h.counts(),{clicks:3,escapes:1})
})
test('preview failure never erases an already confirmed acceptance',async()=>{
 const h=harness({previewFail:true}),executor=new ResumeConsentExecutor(h.deps)
 await assert.rejects(executor.acceptAll({limit:1,preview:true}),/预览/)
 assert.equal(executor.state.businessEffect.completed_count,1);assert.equal(executor.state.businessEffect.status,'confirmed');assert.equal(executor.state.confirmedItems[0]?.accepted,true)
})
test('unchanged banner is unknown and never clicks agree twice',async()=>{
 const h=harness({unchanged:true}),executor=new ResumeConsentExecutor(h.deps)
 await assert.rejects(executor.acceptAll({limit:1}),/无法确认/);assert.equal(h.counts().clicks,2);assert.equal(executor.state.businessEffect.status,'unknown')
})
test('attachment appearing in a different conversation does not prove acceptance',async()=>{
 const h=harness({switchContact:true}),executor=new ResumeConsentExecutor(h.deps)
 await assert.rejects(executor.acceptAll({limit:1}),/身份/);assert.equal(executor.state.businessEffect.status,'unknown')
})
test('no pending left-list request means no action, even with a right-panel agree marker',async()=>{
 let clicks=0;const r=await new ResumeConsentExecutor({snapshot:async()=>actionSnapshot({extra:[prompt,agree]}),click:async()=>{clicks++},sleep:async()=>{}}).acceptAll()
 assert.equal(r.accepted,0);assert.equal(clicks,0)
})
test('missing processing banner only polls read-only, without submitting an arbitrary agree',async()=>{
 let clicks=0;const executor=new ResumeConsentExecutor({snapshot:async()=>actionSnapshot({extra:[request]}),click:async()=>{clicks++},sleep:async()=>{}})
 await assert.rejects(executor.acceptAll(),/未找到/);assert.equal(clicks,1);assert.equal(executor.state.businessEffect.status,'not_started')
})
test('direct native agree error does not retry and preserves final unknown',async()=>{
 const h=harness(),click=h.deps.click;let inputs=0;h.deps.click=async()=>{inputs++;if(inputs===2)throw new WinClickError('[BOSS_INPUT_UNKNOWN] input may be sent');await click()}
 const executor=new ResumeConsentExecutor(h.deps);await assert.rejects(executor.acceptAll());assert.equal(inputs,2);assert.equal(executor.state.businessEffect.status,'unknown');assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})


test('recovery binds acceptance to the original contact before final agree', async()=>{
 const h=harness(), expectedContactRef=chatContactRef(actionSnapshot({contact:'合成乙'}))!
 await assert.rejects(new ResumeConsentExecutor(h.deps).acceptAll({limit:1,expectedContactRef}),/绑定不一致/)
 assert.equal(h.counts().clicks,1)
})

test('unchanged historical acceptance notice cannot confirm a new request', async()=>{
 let phase=0,clicks=0
 const oldNotice=text('已同意接收附件简历',700,700,200,25)
 const executor=new ResumeConsentExecutor({snapshot:async()=>actionSnapshot({extra:phase===0?[request,oldNotice]:phase===1?[request,prompt,agree,oldNotice]:[oldNotice]}),
 click:async()=>{phase++;clicks++},sleep:async()=>{}})
 await assert.rejects(executor.acceptAll({limit:1}),/无法确认/)
 assert.equal(clicks,2);assert.equal(executor.state.businessEffect.status,'unknown')
})

const historicalPrompt=text(MARKER,695,342,168,36),historicalAgree=text('同意',805,415,26,15)

function historicalCardHarness(opts:{keepProcessingBar?:boolean;oldPreviewOnly?:boolean;messageCardOnly?:boolean;removeMessageCard?:boolean}={}) {
 let phase=0,clicks=0
 const card=[historicalPrompt,historicalAgree]
 const before=[request,...card,...(opts.messageCardOnly?[]:[prompt,agree]),...(opts.oldPreviewOnly?[preview]:[])]
 const after=[...(opts.removeMessageCard?[]:card),...(opts.keepProcessingBar?[prompt,agree]:[]),preview]
 const executor=new ResumeConsentExecutor({snapshot:async()=>actionSnapshot({extra:phase===0?[request]:phase===1?before:after}),
 click:async()=>{clicks++;phase++},sleep:async()=>{}})
 return {executor,clicks:()=>clicks}
}

test('processing bar acceptance tolerates the historical grey agree remaining beside the new attachment',async()=>{
 const h=historicalCardHarness()
 assert.equal((await h.executor.acceptAll({limit:1})).accepted,1)
 assert.equal(h.clicks(),2);assert.equal(h.executor.state.businessEffect.status,'confirmed')
})

test('new attachment does not confirm acceptance while the original processing bar remains',async()=>{
 const h=historicalCardHarness({keepProcessingBar:true})
 await assert.rejects(h.executor.acceptAll({limit:1}),/无法确认/)
 assert.equal(h.clicks(),2);assert.equal(h.executor.state.businessEffect.status,'unknown')
})

test('a historical preview cannot confirm processing bar acceptance even when the bar disappears',async()=>{
 const h=historicalCardHarness({oldPreviewOnly:true})
 await assert.rejects(h.executor.acceptAll({limit:1}),/无法确认/)
 assert.equal(h.clicks(),2);assert.equal(h.executor.state.businessEffect.status,'unknown')
})

test('message card fallback still requires its own agree control to disappear',async()=>{
 const h=historicalCardHarness({messageCardOnly:true})
 await assert.rejects(h.executor.acceptAll({limit:1}),/无法确认/)
 assert.equal(h.clicks(),2);assert.equal(h.executor.state.businessEffect.status,'unknown')
})

test('message card fallback still accepts disappearance accompanied by a new attachment',async()=>{
 const h=historicalCardHarness({messageCardOnly:true,removeMessageCard:true})
 assert.equal((await h.executor.acceptAll({limit:1})).accepted,1)
 assert.equal(h.clicks(),2);assert.equal(h.executor.state.businessEffect.status,'confirmed')
})

test('PDF preview readiness comes from its associated visible canvas without any section text',()=>{
 const snap=pdfPreviewSnapshot()
 snap.strings=snap.strings.map(value=>value==='个人优势'||value==='工作经历'?'背景标签':value)
 assert.equal(attachmentPreviewState(snap),'ready')
})

test('preview sections are useful only inside the visible attachment document',()=>{
 assert.equal(attachmentPreviewState(pdfPreviewSnapshot({sections:true})),'ready')
 assert.equal(attachmentPreviewState(pdfPreviewSnapshot({loading:true})),'loading')
 assert.equal(attachmentPreviewState(actionSnapshot({extra:[text('个人优势',700,400),text('工作经历',700,450)]})),'closed')
})

test('hidden and offscreen attachment frames cannot expose their canvas as a ready preview',()=>{
 assert.equal(attachmentPreviewState(pdfPreviewSnapshot({hidden:true})),'closed')
 assert.equal(attachmentPreviewState(pdfPreviewSnapshot({offscreen:true})),'closed')
})

test('ordinary recommendation frames and unattached canvases cannot prove attachment readiness',()=>{
 assert.equal(attachmentPreviewState(pdfPreviewSnapshot({ordinaryFrame:true})),'closed')
 assert.equal(attachmentPreviewState(pdfPreviewSnapshot({unboundCanvas:true})),'loading')
 assert.equal(attachmentPreviewState(pdfPreviewSnapshot({loading:true,backgroundCanvas:true})),'loading')
 assert.equal(attachmentPreviewState(pdfPreviewSnapshot({canvasOutside:true})),'loading')
})

test('a loading PDF only polls for its canvas, then Escape verifies the frame closes',async()=>{
 const h=harness({loadingPolls:2}),executor=new ResumeConsentExecutor(h.deps)
 const result=await executor.acceptAll({limit:1,preview:true})
 assert.equal(result.previewed,1);assert.deepEqual(h.counts(),{clicks:3,escapes:1})
 assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})

test('a PDF that remains loading is never clicked again and preserves confirmed acceptance',async()=>{
 const h=harness({loadingForever:true}),executor=new ResumeConsentExecutor(h.deps)
 await assert.rejects(executor.acceptAll({limit:1,preview:true}),/画布已加载/)
 assert.deepEqual(h.counts(),{clicks:3,escapes:0});assert.equal(executor.state.businessEffect.status,'confirmed')
 assert.equal(executor.state.execution.ordinaryRetriesUsed,0)
})

test('Escape that leaves the actual PDF frame open is not reported as closed',async()=>{
 const h=harness({keepPreviewAfterEscape:true}),executor=new ResumeConsentExecutor(h.deps)
 await assert.rejects(executor.acceptAll({limit:1,preview:true}),/Escape 后未关闭/)
 assert.deepEqual(h.counts(),{clicks:3,escapes:2});assert.equal(executor.state.businessEffect.status,'confirmed')
})
