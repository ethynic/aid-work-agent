import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash, createHmac, randomUUID } from 'node:crypto'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { createNameOperations } from '../src/operations/nameOperations.js'
import { verifyBridgeEnvelope } from '../src/platform/liveBridge.js'
import type { NameDriver, NameWindow } from '../src/platform/nameSession.js'
const key='ac'.repeat(32)
const ctx={signal:new AbortController().signal,progress:()=>{}}
const window:NameWindow={unchanged:false,title:'test',exact_name:true,unique_match:true,complete:true,frame:'a'.repeat(64),evidence_ref:'dpapi:test_evidence',input_empty:true,objects:[]}
const envelope=(value:unknown)=>{const context=JSON.stringify(value);return {context,signature:createHmac('sha256',Buffer.from(key,'hex')).update(context).digest('hex')}}
test('签名校验拒绝篡改、过期、未来和超过90秒窗口',()=>{
  const now=Date.now(), base={version:1,kind:'weixin.send.v2',request_id:randomUUID(),issued_at_ms:now,expires_at_ms:now+1000}
  assert.equal(verifyBridgeEnvelope(envelope(base),now,key).kind,'weixin.send.v2')
  for(const changed of [{...base,issued_at_ms:now+1},{...base,expires_at_ms:now},{...base,expires_at_ms:now+90001}])assert.throws(()=>verifyBridgeEnvelope(envelope(changed),now,key))
  assert.throws(()=>verifyBridgeEnvelope({...envelope(base),signature:'0'.repeat(64)},now,key))
})
test('resolver不返回历史，observer缓存基线后仅签名对应frame可发送',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'name-ops-'))
  try{
    let sent=false;const actions:string[]=[]
    const driver:NameDriver=async req=>{
      actions.push(String(req.action))
      if(req.action==='submit'){sent=true;return{submitted:true}}
      if(req.previous_frame)return{unchanged:true,frame:window.frame}
      return sent?{...window,frame:'b'.repeat(64),objects:[{type:'text',sender:'self',text:'reply',bounds:{x0:10,y0:20,x1:100,y1:40},clipped:false,evidence_hash:'d'.repeat(64)}]}:window
    }
    const ops=createNameOperations({driver,stateDir:dir,bridgeKey:key})
    const resolved=await ops.resolve.execute({target_name:'test'},ctx)
    assert.equal(resolved.success,true);assert.equal('objects' in resolved.data,false);assert.equal('ordered_messages' in resolved.data,false)
    const binding=randomUUID()
    const observed=await ops.observe.execute({target_name:'test',conversation_binding_id:binding,binding_version:1,account_identity_version:0,watermark:null},ctx)
    assert.equal(observed.data.frame,window.frame)
    const now=Date.now(),context={version:1,kind:'weixin.send.v2',request_id:randomUUID(),invocation_id:randomUUID(),permit_id:'p',issued_at_ms:now,expires_at_ms:now+60000,target_name:'test',conversation_binding_id:binding,binding_version:1,account_identity_version:0,expected_frame:window.frame,text:'reply',payload_hash:createHash('sha256').update('reply').digest('hex')}
    const rejected=await ops.send.execute(envelope({...context,expected_frame:'f'.repeat(64)}),ctx)
    assert.equal(rejected.success,false);assert.equal(sent,false)
    actions.length=0
    const result=await ops.send.execute(envelope(context),ctx)
    assert.equal(result.success,true);assert.equal(result.effect,'applied');assert.equal(sent,true)
    assert.equal((result as unknown as {phase:string}).phase,'submitted');assert.match(String(result.data.evidence_ref),/^weixin-submission:/)
    assert.deepEqual(actions,['submit'])
  }finally{await rm(dir,{recursive:true,force:true})}
})

test('wall clock rollback cannot extend provider elapsed lease',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'name-clock-'))
  const realNow=Date.now
  let submitted=false
  try {
    const driver:NameDriver=async (req,signal)=>{
      if(req.action==='submit'){
        Date.now=()=>realNow()-60_000
        await new Promise<void>(resolve=>{if(signal?.aborted)resolve();else signal?.addEventListener('abort',()=>resolve(),{once:true})})
        if(!signal?.aborted)submitted=true
        throw new Error('expired')
      }
      return req.previous_frame?{unchanged:true,frame:window.frame}:window
    }
    const ops=createNameOperations({driver,stateDir:dir,bridgeKey:key}),binding=randomUUID()
    await ops.resolve.execute({target_name:'test'},ctx)
    await ops.observe.execute({target_name:'test',conversation_binding_id:binding,binding_version:1,account_identity_version:0,watermark:null},ctx)
    const now=realNow(),text='reply'
    const value={version:1,kind:'weixin.send.v2',request_id:randomUUID(),invocation_id:randomUUID(),permit_id:'p',issued_at_ms:now,expires_at_ms:now+150,target_name:'test',conversation_binding_id:binding,binding_version:1,account_identity_version:0,expected_frame:window.frame,text,payload_hash:createHash('sha256').update(text).digest('hex')}
    const keepAlive=setTimeout(()=>{},1000)
    const result=await ops.send.execute(envelope(value),ctx)
    clearTimeout(keepAlive)
    assert.equal(result.success,false);assert.equal(result.effect,'unknown');assert.equal(submitted,false)
  } finally {Date.now=realNow;await rm(dir,{recursive:true,force:true})}
})
