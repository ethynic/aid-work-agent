import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { RuntimeSupervisor, parseRuntimeRequest } from '../electron/runtimeSupervisor.js'

const host = `
let state='stopped', importing; const ops=new Map();
process.on('message',m=>{
 if(m.kind==='runtime_platform_response'){process.send({request_id:importing.request_id,method:'plugins.import',code:m.code,error:m.error,result:m.code?null:{operation_id:'install',operation:'import',status:'succeeded',code:0,error:''}});return}
 if(m.method==='plugins.import'){importing=m;process.send({kind:'runtime_platform_request',id:'platform-selection',method:'takeSelectedPackage',params:{...m.params,instance_id:'fixture-instance'}});return}
 let result;
 if(m.method==='describe')result={api_major:1,host_version:'fixture',features:['events'],instance_id:'fixture-instance',revision:0};
 if(m.method==='getState')result={instance_id:'fixture-instance',revision:0,supervisor:'desktop',state,connection:'unpaired'};
 if(m.method==='stop'){const op={operation_id:'stop-id',operation:'stop',status:'succeeded',code:0,error:''};ops.set(op.operation_id,op);result=op;state='stopped'}
 if(m.method==='operations.get')result=ops.get(m.params.operation_id);
 process.send({request_id:m.request_id,method:m.method,code:0,error:'',result});
});process.on('disconnect',()=>process.exit(0));
`

function controlledHost() {
  const child = new EventEmitter() as ChildProcess & { sent: Record<string, unknown>[] }
  let connected = true
  Object.defineProperty(child, 'connected', { get: () => connected }); child.sent = []
  child.send = ((message: Record<string, unknown>, callback?: (error: Error | null) => void) => {
    child.sent.push(message); callback?.(null); return true
  }) as ChildProcess['send']
  child.disconnect = () => { connected = false; child.emit('disconnect'); child.emit('exit', 0) }
  const respond = (result: unknown, index = child.sent.length - 1) => {
    const request = child.sent[index]!
    child.emit('message', { request_id: request.request_id, method: request.method, code: 0, error: '', result })
  }
  return { child, respond }
}
const description = { api_major: 1, host_version: 'fixture', features: ['events'], instance_id: 'fixture-instance', revision: 0 }
const flush = () => new Promise<void>(resolve => setImmediate(resolve))

test('trusted supervisor handshakes through normal H1 and waits for stop plus actual exit', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'h3-supervisor-'))
  const entry = path.join(root, 'host.cjs'); await writeFile(entry, host)
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: entry, home: root, supervisor: 'desktop', takeSelectedPackage: async () => { throw new Error('no selection') }, onEvent: () => {}, onDisconnect: () => {} })
  try {
    const reply = await supervisor.request({ request_id: 'ui-description', method: 'describe', params: {} })
    assert.equal(reply.request_id, 'ui-description')
    assert.equal(supervisor.instanceId, 'fixture-instance')
    await supervisor.stopAndExit()
    assert.equal(supervisor.instanceId, '')
    await assert.rejects(supervisor.request({ request_id: 'late-start', method: 'start', params: { request_key: 'late' } }), /正在退出/,
      'Final window cleanup must not let the renderer recreate a consumer after a confirmed shutdown')
  } finally { await supervisor.stopAndExit(); await rm(root, { recursive: true, force: true }) }
})

test('startup error stays associated with caller describe instead of being inferred from stderr', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'h3-supervisor-'))
  const entry = path.join(root, 'host.cjs')
  await writeFile(entry, `process.on('message',m=>process.send({request_id:m.request_id,method:m.method,code:4,error:'已有Runtime实例',result:null},()=>process.exit(1)))`)
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: entry, home: root, supervisor: 'desktop', takeSelectedPackage: async () => { throw new Error('no selection') }, onEvent: () => {}, onDisconnect: () => {} })
  try {
    const result = await supervisor.request({ request_id: 'caller', method: 'describe', params: {} })
    assert.deepEqual(result, { request_id: 'caller', method: 'describe', code: 4, error: '已有Runtime实例', result: null })
    await supervisor.stopAndExit()
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('renderer cannot turn the management channel into shell or filesystem access', () => {
  for (const value of [
    { request_id: 'r', method: 'spawn', params: { command: 'anything' } },
    { request_id: 'r', method: 'plugins.import', params: { request_key: 'k', selection_ref: 'ref', path: 'C:/private' } },
    { request_id: 'r', method: 'describe', params: {}, kind: 'runtime_platform_request' },
  ]) assert.throws(() => parseRuntimeRequest(value), /无效/)
})

test('package callbacks use the private frame and only the bound current instance', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'h3-supervisor-'))
  const entry = path.join(root, 'host.cjs'); await writeFile(entry, host)
  let calls = 0
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: entry, home: root, supervisor: 'desktop', onEvent: () => {}, onDisconnect: () => {},
    takeSelectedPackage: async input => {
      calls++; assert.deepEqual(input, { instance_id: 'fixture-instance', selection_ref: 'ref', request_key: 'key' })
      return { staged_path: path.join(root, 'frozen.zip'), size: 10, sha256: 'a'.repeat(64) }
    } })
  try {
    const result = await supervisor.request({ request_id: 'import', method: 'plugins.import', params: { selection_ref: 'ref', request_key: 'key' } })
    assert.equal(result.code, 0); assert.equal(calls, 1)
    assert.equal(Object.hasOwn(result.result as object, 'staged_path'), false, 'Private filesystem capabilities must not escape through the renderer result')
  } finally { await supervisor.stopAndExit(); await rm(root, { recursive: true, force: true }) }
})

test('retrying a timed out bootstrap reuses the living Host rather than creating another consumer', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'h3-supervisor-'))
  const entry = path.join(root, 'host.cjs')
  await writeFile(entry, `let first=true; ${host.replace("if(m.method==='describe')result=", "if(m.method==='describe'&&first){first=false;return} if(m.method==='describe')result=")}`)
  let spawned = 0
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: entry, home: root, supervisor: 'desktop', requestTimeoutMs: 500,
    spawnChild: (node, args, options) => { spawned++; return spawn(node, args, options) },
    takeSelectedPackage: async () => { throw new Error('no selection') }, onEvent: () => {}, onDisconnect: () => {} })
  try {
    await assert.rejects(supervisor.request({ request_id: 'first', method: 'describe', params: {} }), /超时/)
    const retry = await supervisor.request({ request_id: 'retry', method: 'describe', params: {} })
    assert.equal(retry.code, 0)
    assert.equal(spawned, 1, 'A timeout is not proof that the original consumer exited')
  } finally { await supervisor.stopAndExit(); await rm(root, { recursive: true, force: true }) }
})

test('uncertain stop preserves its historical operation and requires a new explicit stop check before exiting', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'h3-supervisor-'))
  const entry = path.join(root, 'host.cjs')
  await writeFile(entry, `let stops=0; ${host
    .replace("if(m.method==='stop'){const op={operation_id:'stop-id',operation:'stop',status:'succeeded',code:0,error:''};", "if(m.method==='stop'){stops++;const op={operation_id:'stop-'+stops,operation:'stop',status:stops===1?'reconciling':'succeeded',code:0,error:''};")}`)
  let disconnected = 0
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: entry, home: root, supervisor: 'desktop',
    takeSelectedPackage: async () => { throw new Error('no selection') }, onEvent: () => {}, onDisconnect: () => { disconnected++ } })
  try {
    await supervisor.request({ request_id: 'describe', method: 'describe', params: {} })
    await assert.rejects(supervisor.stopAndExit(), /需要核对/)
    assert.equal(disconnected, 0, 'Uncertain business effects must not be turned into process termination')
    assert.equal(supervisor.instanceId, 'fixture-instance')
    assert.equal((await supervisor.request({ request_id: 'state', method: 'getState', params: {} })).code, 0)
    await supervisor.stopAndExit()
    assert.equal(disconnected, 1, 'One closed management connection should invalidate consumers once')
    assert.equal(supervisor.instanceId, '')
  } finally { await supervisor.stopAndExit(); await rm(root, { recursive: true, force: true }) }
})

test('slow local bootstrap and list can exceed fifteen seconds within the bounded management budget', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { child, respond } = controlledHost()
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: 'controlled', home: os.tmpdir(), supervisor: 'desktop', spawnChild: () => child,
    takeSelectedPackage: async () => { throw new Error('no selection') }, onEvent: () => {}, onDisconnect: () => {} })
  try {
    let completed = false
    const pending = supervisor.request({ request_id: 'slow-bootstrap', method: 'describe', params: {} }).then(reply => { completed = true; return reply })
    t.mock.timers.tick(16_000); await flush()
    assert.equal(completed, false, 'Initial resource checks may legitimately exceed the obsolete fifteen-second budget')
    respond(description); await flush(); respond(description)
    assert.equal((await pending).code, 0)
    const list = supervisor.request({ request_id: 'slow-list', method: 'plugins.list', params: {} })
    await flush(); t.mock.timers.tick(45_000); await flush()
    respond({ instance_id: 'fixture-instance', revision: 0, plugins: [] })
    assert.equal((await list).code, 0, 'Signed package refresh under scanning pressure must keep its current-instance result')
  } finally { child.disconnect(); await supervisor.stopAndExit(); t.mock.timers.reset() }
})

test('budget exhaustion finishes waiting while a late bootstrap cannot establish the old request', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { child, respond } = controlledHost(); let spawned = 0
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: 'controlled', home: os.tmpdir(), supervisor: 'desktop', spawnChild: () => { spawned++; return child },
    takeSelectedPackage: async () => { throw new Error('no selection') }, onEvent: () => {}, onDisconnect: () => {} })
  try {
    const expired = assert.rejects(supervisor.request({ request_id: 'expired', method: 'describe', params: {} }), /超时/)
    t.mock.timers.tick(120_001); await expired
    respond(description, 0); assert.equal(supervisor.instanceId, '', 'Expired correlation IDs cannot refresh the connection')
    const retry = supervisor.request({ request_id: 'fresh-query', method: 'describe', params: {} })
    respond(description); await flush(); respond(description)
    assert.equal((await retry).code, 0)
    assert.equal(spawned, 1, 'A waiting budget is not proof of process exit or permission to create a second consumer')
  } finally { child.disconnect(); await supervisor.stopAndExit(); t.mock.timers.reset() }
})

test('disconnect finishes pending queries immediately rather than keeping the full management budget', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { child, respond } = controlledHost(); let disconnected = 0
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: 'controlled', home: os.tmpdir(), supervisor: 'desktop', spawnChild: () => child,
    takeSelectedPackage: async () => { throw new Error('no selection') }, onEvent: () => {}, onDisconnect: () => { disconnected++ } })
  try {
    const connected = supervisor.request({ request_id: 'connected', method: 'describe', params: {} })
    respond(description); await flush(); respond(description); await connected
    const cancelled = assert.rejects(supervisor.request({ request_id: 'pending-list', method: 'plugins.list', params: {} }), /断开|退出/)
    await flush(); child.disconnect(); await cancelled
    respond({ instance_id: 'fixture-instance', revision: 99, plugins: [] })
    assert.equal(supervisor.instanceId, '')
    assert.equal(disconnected, 1)
  } finally { await supervisor.stopAndExit(); t.mock.timers.reset() }
})
