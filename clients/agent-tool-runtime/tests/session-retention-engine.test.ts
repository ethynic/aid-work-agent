import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync, readFileSync, openSync, ftruncateSync, closeSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { NetworkError, type ApiClient } from '../src/apiClient.js'
import { SessionTaskEngine } from '../src/sessionTasks/engine.js'
import { SessionStore, sessionTaskDir, type SessionCrypto } from '../src/sessionTasks/sessionStore.js'
import { RETENTION_DEFAULT_MAX_BYTES } from '../src/sessionTasks/retention.js'

function fixture(){
 const home=mkdtempSync(join(tmpdir(),'retention-engine-'))
 let decrypts=0
 const crypto:SessionCrypto={protect:async plain=>Buffer.from(plain).toString('base64'),unprotect:async cipher=>{decrypts++;return Buffer.from(cipher,'base64').toString()}}
 const engine=new SessionTaskEngine({api:{sessionTaskEvents:async()=>{throw new NetworkError('synthetic offline')}} as unknown as ApiClient,runtimeHome:home,crypto,runtimeInstanceId:'test',observer:async()=>{throw new Error('unexpected observer')}})
 return{home,crypto,engine,decrypts:()=>decrypts}
}

test('周期retention不解密旧日志，重复水位检查保留原字节',async()=>{
 const f=fixture()
 try{
  const store=new SessionStore({runtimeHome:f.home,assignmentId:'old',crypto:f.crypto})
  await store.appendEncrypted('e1','phase',{to:'waiting_peer'},1)
  const path=join(sessionTaskDir(f.home,'old'),'events.jsonl'),before=readFileSync(path)
  await f.engine['checkRetention'](100000)
  await f.engine['checkRetention'](160001)
  assert.equal(f.decrypts(),0)
  assert.deepEqual(readFileSync(path),before)
  assert.equal(f.engine['diskStopNew'],false)
 }finally{rmSync(f.home,{recursive:true,force:true})}
})

test('周期retention保留满额日志并关停新增，释放容量后恢复',async()=>{
 const f=fixture()
 try{
  const store=new SessionStore({runtimeHome:f.home,assignmentId:'old',crypto:f.crypto})
  await store.appendEncrypted('e1','phase',{to:'waiting_peer'},1)
  const path=join(sessionTaskDir(f.home,'old'),'capacity.bin'),fd=openSync(path,'w')
  try{ftruncateSync(fd,RETENTION_DEFAULT_MAX_BYTES)}finally{closeSync(fd)}
  await f.engine['checkRetention'](100000)
  assert.equal(f.engine['diskStopNew'],true);assert.equal(f.decrypts(),0)
  unlinkSync(path)
  await f.engine['checkRetention'](160001)
  assert.equal(f.engine['diskStopNew'],false)
  assert.ok(readFileSync(join(sessionTaskDir(f.home,'old'),'events.jsonl')).length>0)
 }finally{rmSync(f.home,{recursive:true,force:true})}
})

test('启动恢复仍解密回放未ACK旧日志并排入恢复补交',async()=>{
 const f=fixture()
 try{
  const store=new SessionStore({runtimeHome:f.home,assignmentId:'old',crypto:f.crypto})
  await store.appendEncrypted('e1','phase',{to:'waiting_peer'},1)
  await f.engine['recoverOrphanAssignments']()
  assert.equal(f.decrypts(),1)
  const recovery=f.engine['recoveryPending'].get('old')
  assert.equal(recovery?.events.length,1)
  assert.deepEqual(recovery?.events[0]?.payload,{to:'waiting_peer'})
 }finally{rmSync(f.home,{recursive:true,force:true})}
})
