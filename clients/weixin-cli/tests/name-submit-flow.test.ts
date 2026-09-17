import test from 'node:test'
import assert from 'node:assert/strict'
import { submitNameWithCapture, sendCaptureSchema, type NameSubmitDependencies, type SendCapture } from '../src/platform/nameSession.js'

const shot:SendCapture={width:1000,height:800,png:'local-image',handle:123,rect:[10,20,1000,800],title_region:{x0:300,y0:20,x1:750,y1:65},input_region:{x0:330,y0:650,x1:965,y1:710}}
const request={action:'submit',target_name:'test-contact',text:'test-text',deadline_ms:Date.now()+60_000}
function harness(){
  const calls:string[]=[]
  const submitted:Record<string,unknown>[]=[]
  const dependencies:NameSubmitDependencies={
    capture:async(signal,fn,action)=>{calls.push(action);return fn(shot,'capture.png')},
    title:async(c,path,name)=>{calls.push('title');assert.equal(c,shot);assert.equal(path,'capture.png');assert.equal(name,request.target_name)},
    primitive:async(payload)=>{calls.push(String(payload.action));submitted.push(payload);return {done:true}},
  }
  return {calls,submitted,dependencies}
}

test('production submit flow takes one capture, checks title once and reuses its coordinates for one submit',async()=>{
  const h=harness()
  assert.deepEqual(await submitNameWithCapture(request,undefined,undefined,h.dependencies),{submitted:true})
  assert.deepEqual(h.calls,['send_capture','title','submit'])
  assert.equal(h.submitted.length,1)
  assert.equal(h.submitted[0]!.handle,shot.handle)
  assert.equal(h.submitted[0]!.rect,shot.rect)
  assert.equal(h.submitted[0]!.input_region,shot.input_region)
  assert.equal(h.submitted[0]!.text,request.text)
})

test('title failure prevents any input operation',async()=>{
  const h=harness()
  h.dependencies.title=async()=>{h.calls.push('title');throw new Error('title unavailable')}
  await assert.rejects(submitNameWithCapture(request,undefined,undefined,h.dependencies),/title unavailable/)
  assert.deepEqual(h.calls,['send_capture','title'])
  assert.equal(h.submitted.length,0)
})

test('cancellation before capture, during title and after type diagnostic prevents submit',async()=>{
  for(const point of ['before','title','diagnostic']){
    const h=harness(),controller=new AbortController()
    if(point==='before')controller.abort()
    if(point==='title')h.dependencies.title=async()=>{h.calls.push('title');controller.abort()}
    const diagnostic=async(stage:string)=>{if(point==='diagnostic'&&stage==='type')controller.abort()}
    await assert.rejects(submitNameWithCapture(request,controller.signal,diagnostic,h.dependencies),{name:'AbortError'})
    assert.equal(h.submitted.length,0)
    if(point==='before')assert.deepEqual(h.calls,[])
  }
})

test('missing submit acknowledgement is not accepted and does not retry',async()=>{
  const h=harness()
  h.dependencies.primitive=async()=>{h.calls.push('submit');return {done:false}}
  await assert.rejects(submitNameWithCapture(request,undefined,undefined,h.dependencies),/未确认完成/)
  assert.deepEqual(h.calls,['send_capture','title','submit'])
})

// SendOnly excludes PrintWindow's fifth boolean from the wire coordinates.
test('send capture protocol accepts four numeric coordinates and excludes printed metadata',()=>{
  assert.deepEqual(sendCaptureSchema.parse(shot).rect,[10,20,1000,800])
  assert.equal(sendCaptureSchema.safeParse({...shot,rect:[10,20,1000,800,true]}).success,false)
})
