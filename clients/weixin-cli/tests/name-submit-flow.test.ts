import test from 'node:test'
import assert from 'node:assert/strict'
import { defaultNameDriver, submitNameWithCapture, sendCaptureSchema, type SendCapture } from '../src/platform/nameSession.js'

const shot:SendCapture={title:'test-contact',handle:123,rect:[10,20,1000,800],input_region:{x0:330,y0:650,x1:965,y1:710}}
const request={action:'submit',target_name:'test-contact',text:'test-text',deadline_ms:Date.now()+60_000}
function harness(){
  const calls:string[]=[]
  const submitted:Record<string,unknown>[]=[]
  const dependencies={
    capture:async ():Promise<Record<string,unknown>>=>shot,
    submit:async ():Promise<Record<string,unknown>>=>({done:true}),
  }
  const primitive=async(payload:Record<string,unknown>)=>{
    calls.push(String(payload.action))
    if(payload.action==='send_capture'){
      assert.deepEqual(payload,{action:'send_capture'})
      return dependencies.capture()
    }
    submitted.push(payload)
    return dependencies.submit()
  }
  return {calls,submitted,dependencies,primitive}
}

test('matches the native title against the permit and sends only local composer data to one submit',async(t)=>{
  const h=harness()
  const logs:string[]=[]
  t.mock.method(process.stderr,'write',(line:unknown)=>{logs.push(String(line));return true})
  assert.deepEqual(await submitNameWithCapture(request,undefined,undefined,h.primitive),{submitted:true})
  assert.deepEqual(h.calls,['send_capture','submit'])
  assert.deepEqual(h.submitted,[{...request,expected_native_title:request.target_name,handle:shot.handle,rect:shot.rect,input_region:shot.input_region}])
  assert.equal(logs.length,1)
  assert.equal(JSON.parse(logs[0]!).event,'name_send_timing')
  for(const privateField of [request.target_name,request.text,'png','title_ocr_ms']){
    assert.equal(logs[0]!.includes(privateField),false)
  }
})

test('missing native title and near-but-different title both prevent input',async()=>{
  for(const [title,code] of [['','UI_CHANGED'],['test-contaCt','TARGET_AMBIGUOUS']]){
    const h=harness()
    h.dependencies.capture=async()=>({...shot,title})
    await assert.rejects(submitNameWithCapture(request,undefined,undefined,h.primitive),{code})
    assert.deepEqual(h.calls,['send_capture'])
    assert.deepEqual(h.submitted,[])
  }
})

test('cancellation before or during native capture and at type diagnostic prevents submit',async()=>{
  for(const point of ['before','capture','diagnostic']){
    const h=harness(),controller=new AbortController()
    if(point==='before')controller.abort()
    if(point==='capture')h.dependencies.capture=async()=>{controller.abort();return shot}
    const diagnostic=async(stage:string)=>{if(point==='diagnostic'&&stage==='type')controller.abort()}
    await assert.rejects(submitNameWithCapture(request,controller.signal,diagnostic,h.primitive),{name:'AbortError'})
    assert.equal(h.submitted.length,0)
    if(point==='before')assert.deepEqual(h.calls,[])
  }
})

test('missing submit acknowledgement is not accepted and does not retry',async()=>{
  const h=harness()
  h.dependencies.submit=async()=>({done:false})
  await assert.rejects(submitNameWithCapture(request,undefined,undefined,h.primitive),/未确认完成/)
  assert.deepEqual(h.calls,['send_capture','submit'])
})

test('native send capture protocol rejects screenshots and malformed coordinates',()=>{
  assert.deepEqual(sendCaptureSchema.parse(shot).rect,[10,20,1000,800])
  assert.equal(sendCaptureSchema.safeParse({...shot,rect:[10,20,1000,800,true]}).success,false)
  assert.equal(sendCaptureSchema.safeParse({...shot,png:'image'}).success,false)
})

test('an observed native target reaches the send precheck without an OCR resolver cache entry', async()=>{
  await assert.rejects(defaultNameDriver({
    ...request, target_name:'never-resolved-by-ocr', expected_native_title:'实际原生标题',
  },AbortSignal.abort()),{name:'AbortError'})
})
