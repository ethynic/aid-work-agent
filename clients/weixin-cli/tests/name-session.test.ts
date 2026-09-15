import test from 'node:test'
import { CodedOperationError } from '../src/operations/types.js'
import assert from 'node:assert/strict'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { readNameSession, sendNameSessionOnce, newNameObjects, splitClippedTop, isNameTimestamp, type NameDriver, type NameWindow } from '../src/platform/nameSession.js'

const object = (text: string, sender: 'peer' | 'self' = 'peer', y = 20): NameWindow['objects'][number] => ({ type:'text',sender,text,bounds:{x0:10,y0:y,x1:90,y1:y+15},clipped:false,evidence_hash:'c'.repeat(64) })
const baseline: NameWindow = { unchanged:false,title:'test-contact',exact_name:true,unique_match:true,complete:true,input_empty:true,evidence_ref:'dpapi:test_evidence',frame:'a'.repeat(64),objects:[object('old')] }

test('read要求精确标题、唯一名称、完整顶层对象；媒体正文不得接纳', async () => {
  for (const change of [{title:'other'}, {unique_match:false}, {complete:false}, {objects:[{...object('fake'),type:'image'}]}]) {
    await assert.rejects(readNameSession('test-contact',{driver:async()=>({...baseline,...change})}))
  }
  assert.deepEqual(await readNameSession('test-contact',{previousFrame:baseline.frame,driver:async()=>({unchanged:true,frame:baseline.frame})}),{unchanged:true,frame:baseline.frame})
})

test('媒体是结构对象，不递归文字；同文重叠歧义拒绝', () => {
  const media={...object(''),type:'image' as const}
  const before={...baseline,objects:[media,object('unique','peer',50)]}
  const after={...baseline,objects:[...before.objects,object('new','self',80)]}
  assert.equal(newNameObjects(before,after)?.length,1)
  assert.equal(newNameObjects({...baseline,objects:[object('x'),object('x','peer',50)]},{...baseline,objects:[object('x'),object('x','peer',50),object('x','peer',80)]}),null)
  assert.equal(newNameObjects({...baseline,objects:[{...object('partial'),clipped:true}]},{...baseline,objects:[object('new')]}),null)
})

test('only flat clipped top with crossing OCR is opaque; interior crossing stays a gap',()=>{
 const width=200,rgba=new Uint8Array(width*100*4),viewport={x0:0,y0:10,x1:200,y1:100}
 const region={sender:'self' as const,bounds:{x0:50,y0:12,x1:150,y1:40}}
 for(let x=50;x<150;x++)rgba.set([157,242,159,255],(12*width+x)*4)
 const boxes=[{text:'partial',score:1,x0:60,y0:11,x1:140,y1:22}]
 const clipped=splitClippedTop([region],boxes,rgba,width,viewport)
 assert.ok(clipped.clipped);assert.equal(clipped.regions.length,0);assert.equal(clipped.boxes.length,0)
 assert.equal(splitClippedTop([region],boxes,rgba,width,{...viewport,y0:0}).clipped,undefined)
 rgba.fill(240);assert.equal(splitClippedTop([region],boxes,rgba,width,viewport).clipped,undefined)
})

test('centered weekday timestamps remain metadata across OCR bounds drift',()=>{
 const viewport={x0:613,y0:150,x1:1914,y1:1570}
 const a={text:'星期一12:30',score:.99,x0:1188,y0:487,x1:1331,y1:514}
 assert.equal(isNameTimestamp(a,viewport),true)
 assert.equal(isNameTimestamp({...a,x1:1332,y0:293,y1:320},viewport),true)
 assert.equal(isNameTimestamp({...a,x0:744,x1:887},viewport),false)
 assert.equal(isNameTimestamp({...a,text:'正文星期一12:30'},viewport),false)
 assert.equal(isNameTimestamp({...a,score:.5},viewport),false)
})

test('timestamp exemption rejects invalid time/date, multiline and bubble intersections',()=>{
 const viewport={x0:613,y0:150,x1:1914,y1:1570}
 const box={text:'星期一12:30',score:.99,x0:1188,y0:487,x1:1331,y1:514}
 for(const text of ['99:99','24:00','12:60','0月1日12:30','13月1日12:30','4月31日12:30','2025年2月29日12:30','0000年1月1日12:30','星期一\n12:30','12:30\r','12:30\u2028'])assert.equal(isNameTimestamp({...box,text},viewport),false)
 for(const text of ['23:59','2024年2月29日12:30','星期一12:30'])assert.equal(isNameTimestamp({...box,text},viewport),true)
 assert.equal(isNameTimestamp({...box,y1:600},viewport),false)
 assert.equal(isNameTimestamp({...box,y0:140,y1:167},viewport),false)
 assert.equal(isNameTimestamp({...box,x0:NaN},viewport),false)
 assert.equal(isNameTimestamp(box,viewport,[{bounds:{x0:1100,y0:480,x1:1400,y1:520}}]),false)
 assert.equal(isNameTimestamp(box,viewport,[{bounds:{x0:1300,y0:500,x1:1400,y1:530}}]),false)
 assert.equal(isNameTimestamp(box,viewport,[{bounds:{x0:1400,y0:480,x1:1500,y1:520}}]),true)
})

test('输入及Enter确认即submitted，不调用任何发送后读取；重复请求不重发',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'name-submit-'))
 try{for(const objects of [[],[object('old')],[{...object(''),type:'image' as const}],[{...object('partial'),clipped:true}]]){
  const actions:string[]=[]
  const driver:NameDriver=async req=>{actions.push(String(req.action));assert.equal(req.action,'submit');return{submitted:true}}
  const args={targetName:'test-contact',text:'old',requestId:randomUUID(),baseline:{...baseline,input_empty:false,objects},deadlineMs:Date.now()+60000}
  const result=await sendNameSessionOnce(args,{stateDir:dir,driver})
  assert.deepEqual(result,{status:'submitted',evidence_ref:`weixin-submission:${args.requestId}:1`})
  assert.equal(JSON.parse(await readFile(join(dir,args.requestId+'.json'),'utf8')).status,'submitted')
  const journal=await readFile(join(dir,args.requestId+'.json.stages.jsonl'),'utf8')
  assert.ok(journal.includes('submitted'));assert.equal(journal.includes('post_read'),false)
  assert.equal((await sendNameSessionOnce(args,{stateDir:dir,driver})).status,'unknown')
  await assert.rejects(sendNameSessionOnce({...args,text:'changed'},{stateDir:dir,driver}))
  assert.deepEqual(actions,['submit'])
 }}finally{await rm(dir,{recursive:true,force:true})}
})

test('动作ack缺失、false、字符串或异常均unknown且不重发',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'name-ack-'))
 try{for(const ack of [{},{submitted:false},{submitted:'true'},null]){
  let calls=0
  const driver:NameDriver=async req=>{calls++;assert.equal(req.action,'submit');if(ack===null)throw new CodedOperationError('UI_CHANGED','title');return ack}
  const args={targetName:'test-contact',text:'reply',requestId:randomUUID(),baseline,deadlineMs:Date.now()+60000}
  assert.equal((await sendNameSessionOnce(args,{stateDir:dir,driver})).status,'unknown')
  assert.equal(JSON.parse(await readFile(join(dir,args.requestId+'.json'),'utf8')).status,'may_have_started')
  await sendNameSessionOnce(args,{stateDir:dir,driver});assert.equal(calls,1)
 }}finally{await rm(dir,{recursive:true,force:true})}
})

test('动作期间取消或绝对期限到达后ack成功也不记submitted',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'name-late-ack-'))
 try{for(const mode of ['cancel','deadline','precancel'] as const){
  const controller=new AbortController();let calls=0
  const args={targetName:'test-contact',text:'reply',requestId:randomUUID(),baseline,deadlineMs:Date.now()+60000}
  if(mode==='precancel')controller.abort()
  const driver:NameDriver=async req=>{calls++;assert.equal(req.action,'submit');if(mode==='cancel')controller.abort();else args.deadlineMs=Date.now()-1;return{submitted:true}}
  assert.equal((await sendNameSessionOnce(args,{stateDir:dir,driver,signal:controller.signal})).status,'unknown')
  assert.equal(calls,mode==='precancel'?0:1)
  assert.equal(JSON.parse(await readFile(join(dir,args.requestId+'.json'),'utf8')).status,'may_have_started')
 }}finally{await rm(dir,{recursive:true,force:true})}
})

test('过期及非法正文在动作前拒绝',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'name-invalid-'));let calls=0
 try{const args={targetName:'test-contact',text:'reply',requestId:randomUUID(),baseline,deadlineMs:Date.now()+60000}
  for(const change of [{deadlineMs:0},{text:'a\nb'},{text:' '},{text:'x'.repeat(501)}])await assert.rejects(sendNameSessionOnce({...args,...change},{stateDir:dir,driver:async()=>{calls++;return{submitted:true}}}))
  assert.equal(calls,0)
 }finally{await rm(dir,{recursive:true,force:true})}
})


test('真实期限取消传入driver，迟到的成功ack保持unknown且不重发',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'name-timed-ack-'))
 try{
  let calls=0,sawAbort=false
  const args={targetName:'test-contact',text:'reply',requestId:randomUUID(),baseline,deadlineMs:Date.now()+300}
  const driver:NameDriver=async(req,signal)=>{
   calls++;assert.equal(req.action,'submit')
   await new Promise(resolve=>setTimeout(resolve,350))
   sawAbort=signal?.aborted===true
   return{submitted:true}
  }
  assert.equal((await sendNameSessionOnce(args,{stateDir:dir,driver})).status,'unknown')
  assert.equal(sawAbort,true);assert.equal(calls,1)
  assert.equal(JSON.parse(await readFile(join(dir,args.requestId+'.json'),'utf8')).status,'may_have_started')
  await sendNameSessionOnce({...args,deadlineMs:Date.now()+10000},{stateDir:dir,driver});assert.equal(calls,1)
 }finally{await rm(dir,{recursive:true,force:true})}
})


test('顶部截断文字行允许40%墨迹抗锯齿，背景圆角及非截断候选仍拒绝',()=>{
 const width=200,viewport={x0:0,y0:10,x1:200,y1:100}
 const region={sender:'self' as const,bounds:{x0:50,y0:12,x1:150,y1:40}}
 const crossing={text:'synthetic',score:.99,x0:60,y0:11,x1:140,y1:22}
 const rgba=new Uint8Array(width*100*4).fill(240)
 const paint=(x:number,rgb:number[])=>rgba.set([...rgb,255],(12*width+x)*4)
 for(let x=50;x<150;x++)paint(x,[157,242,159])
 for(let x=70;x<110;x++)paint(x,x%2?[0,0,0]:[47,73,48])
 const result=splitClippedTop([region],[crossing],rgba,width,viewport)
 assert.deepEqual(result.clipped,{...region.bounds,y0:10});assert.equal(result.regions.length,0);assert.equal(result.boxes.length,0)
 assert.equal(splitClippedTop([region],[crossing],rgba,width,{...viewport,y0:0}).clipped,undefined)
 assert.equal(splitClippedTop([region],[{...crossing,y0:13}],rgba,width,viewport).clipped,undefined)
 for(let x=50;x<70;x++)paint(x,[240,240,240])
 assert.equal(splitClippedTop([region],[crossing],rgba,width,viewport).clipped,undefined)
 rgba.fill(240)
 assert.equal(splitClippedTop([region],[crossing],rgba,width,viewport).clipped,undefined)
 rgba.fill(0)
 assert.equal(splitClippedTop([region],[crossing],rgba,width,viewport).clipped,undefined)
})
