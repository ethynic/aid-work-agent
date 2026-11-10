import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import http from 'node:http'
import { randomUUID, createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'

// Independent integration checks: real HTTP and Windows DPAPI; the Host port
// below is a stateful boundary fixture, not evidence of packaged Host startup.
const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const host = process.env.AID_CLEAN_TEST_HOST_ENTRY ?? path.resolve(desktop, '../agent-tool-runtime/dist/src/managed-entry.js')
const { RuntimeCleanConnection } = await import(pathToFileURL(path.join(desktop, 'dist/electron/runtimeCleanConnection.js')))
const { RuntimeSupervisor } = await import(pathToFileURL(path.join(desktop, 'dist/electron/runtimeSupervisor.js')))
const { resumeRuntimeAtLogin } = await import(pathToFileURL(path.join(desktop, 'dist/electron/runtimeStartup.js')))
const installation = await import(pathToFileURL(path.join(desktop, 'dist/electron/runtimeInstallation.js')))
const registration = await import(pathToFileURL(path.join(path.dirname(host), 'cleanRegistration.js')))
const { dpapiProtect, dpapiUnprotect } = await import(pathToFileURL(path.join(path.dirname(host), 'dpapi.js')))
const skip = process.platform !== 'win32' ? 'requires real Windows CurrentUser DPAPI' : false
const sources = ['electron/runtimeCleanConnection.ts', 'electron/runtimeInstallation.ts', '../agent-tool-runtime/src/cleanRegistration.ts']
console.log('INDEPENDENT_SOURCE_DIGEST ' + createHash('sha256').update(sources.map(file => fs.readFileSync(path.resolve(desktop, file))).join('\n')).digest('hex'))

async function service(handler) {
  const calls = [], devices = new Map()
  const server = http.createServer(async (req, res) => {
    let raw = ''; for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw || '{}')
    calls.push({ url: req.url, body })
    if (handler && await handler(req, res, body, calls)) return
    res.setHeader('Content-Type', 'application/json')
    const receipt = { success: true, pairing_protocol: 4, registration_id: body.registration_id }
    if (req.url.endsWith('/pair/prepare')) {
      res.end(JSON.stringify({ ...receipt, preparation_id: randomUUID(), preparation_token: 'b'.repeat(64) }))
    } else if (req.url.endsWith('/pair/confirm')) {
      const id = randomUUID()
      devices.set(body.registration_id, id)
      res.end(JSON.stringify({ ...receipt, registration_completed: true, device_id: id }))
    } else if (req.url === '/api/local-tools/runtime/pair') {
      let id = devices.get(body.registration_id)
      if (!id) { id = randomUUID(); devices.set(body.registration_id, id) }
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ success: true, pairing_protocol: 3, registration_completed: true, device_id: id, device_token: body.registration_token }))
    } else {
      res.setHeader('Content-Type', 'application/json')
      res.end(JSON.stringify({ success: true, selected: true, replacement_completed: true }))
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return { url: `http://127.0.0.1:${server.address().port}`, calls, devices,
    close: () => new Promise(resolve => { server.close(resolve); server.closeAllConnections() }) }
}

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'aid-independent-clean-'))
  const metadata = path.join(root, 'metadata'), legacy = path.join(root, 'legacy')
  fs.mkdirSync(metadata); fs.mkdirSync(legacy)
  let home = legacy, starts = 0, stops = 0, state = 'stopped'
  const supervisors = new Map()
  const supervisor = () => {
    if (!supervisors.has(home)) {
      const target = home
      supervisors.set(target, { stopAndExit: async (_timeout, beforeStop) => { await beforeStop?.({ state }); state = 'stopped' }, suspend() {}, request: async ({ method }) => {
        if (method === 'getState') {
          let device = {}
          try { device = JSON.parse(fs.readFileSync(path.join(target, 'config.json'), 'utf8')) } catch {}
          return { code: 0, result: { state, connection: device.device_id ? 'disconnected' : 'unpaired', device } }
        }
        if (method === 'start') { starts++; state = 'running'; return { code: 0, result: { operation_id: randomUUID(), status: 'running' } } }
        if (method === 'operations.get') return { code: 0, result: { status: 'succeeded', code: 0 } }
        throw new Error('unexpected management method')
      } })
    }
    return supervisors.get(home)
  }
  const options = { metadataHome: metadata, hostEntry: host, home: () => home, supervisor,
    beforeChange: async () => { stops++; state = 'stopped' },
    restorePriorHome: async () => false,
    stageHome: async () => { const staged = path.join(root, 'new-' + randomUUID()); fs.mkdirSync(staged); return staged },
    validateHome() {},
    commitHome: async target => { home = target },
  }
  const instances = []
  return { root, metadata, legacy, get home() { return home }, get starts() { return starts }, get stops() { return stops },
    connection() { const c = new RuntimeCleanConnection(options); instances.push(c); return c },
    clean() { for (const c of instances) c.dispose(); assert.equal(path.dirname(root), path.resolve(os.tmpdir())); assert.match(path.basename(root), /^aid-independent-clean-/); fs.rmSync(root, { recursive: true, force: true }) } }
}

test('real loopback response loss ends the submission without an active failure record', { skip }, async () => {
  const f = fixture()
  const s = await service(async (req, _res) => {
    if (!req.url.endsWith('/pair/prepare')) return false
    req.socket.destroy()
    return true
  })
  try {
    fs.writeFileSync(path.join(f.metadata, 'registration-lock'), '{corrupt legacy lock bytes')
    for (const name of ['config.json','credentials.bin','management-secret.bin','host-running.json','journal','outbox','session-tasks','management-operations.json']) fs.writeFileSync(path.join(f.legacy, name), '{broken')
    const c = f.connection()
    const reply = await c.reconfigure({ mode: 'register_new', confirmed: true, server: s.url, device_name: 'Independent device', pairing_code: 'ISOLATED-OTP' })
    assert.equal(reply.status, 'action_required'); assert.equal(reply.diagnosis.category, 'REGISTRATION_UNCONFIRMED')
    assert.equal(f.home, f.legacy)
    assert.equal(await registration.readCleanRegistration(f.metadata), null)
    c.dispose()
    const resumed = f.connection(); await resumed.resume()
    assert.equal(await resumed.registrationBlocksExecution(), false); assert.equal(await resumed.registrationStatus(), null)
    assert.equal(f.starts, 0)
    assert.equal(s.calls.length, 1)
  } finally { f.clean(); await s.close() }
})

test('committed paired receipt survives exit before local installation and auto starts', { skip }, async () => {
  const f = fixture(), s = await service()
  try {
    const r = await registration.beginCleanRegistration(f.metadata, { server: s.url, device_name: 'Restart fixture' }, [f.legacy])
    await registration.pairCleanRegistration(f.metadata, r, 'ISOLATED-OTP')
    const c = f.connection(); await c.resume()
    assert.equal(s.devices.size, 1); assert.equal(s.calls.filter(c => c.url.endsWith('/pair')).length, 1); assert.equal(f.starts, 1)
    assert.equal(await registration.readCleanRegistration(f.metadata), null)
  } finally { f.clean(); await s.close() }
})

test('invalid code preserves old execution state while a new explicit submission can succeed', { skip }, async () => {
  const f = fixture(); let reject = true
  const s = await service(async (req, res) => {
    if (req.url.endsWith('/pair/prepare') && reject) { res.statusCode = 400; res.end(JSON.stringify({ detail: 'invalid fixture code' })); return true }
    return false
  })
  try {
    fs.writeFileSync(path.join(f.legacy, 'host-running.json'), 'broken old state')
    const c = f.connection(), input = { mode: 'new_device', confirmed: true, server: s.url, device_name: 'New code fixture', pairing_code: 'ISOLATED-OTP' }
    const first = await c.reconfigure(input)
    assert.equal(first.diagnosis.category, 'PAIRING_CODE_INVALID'); assert.equal(f.home, f.legacy)
    assert.equal(fs.readFileSync(path.join(f.home, 'host-running.json'), 'utf8'), 'broken old state')
    assert.equal(await registration.readCleanRegistration(f.metadata), null)
    assert.equal(f.starts, 0)
    reject = false
    // The next submission follows a verified stop of the original execution.
    fs.renameSync(path.join(f.legacy, 'host-running.json'), path.join(f.legacy, `host-interrupted-${randomUUID()}.json`))
    const second = await c.reconfigure({ ...input, pairing_code: 'NEW-ISOLATED-OTP' })
    assert.equal(second.status, 'complete'); assert.equal(f.starts, 1)
    assert.equal(await c.registrationBlocksExecution(), false)
  } finally { f.clean(); await s.close() }
})

test('legacy protocol 2 replacement is completed and selected without old resource journals', { skip }, async () => {
  const f = fixture(), s = await service(), id = randomUUID()
  try {
    fs.writeFileSync(path.join(f.legacy, 'config.json'), JSON.stringify({ server: s.url, device_id: id }))
    fs.writeFileSync(path.join(f.legacy, 'credentials.bin'), await dpapiProtect('a'.repeat(64)))
    fs.writeFileSync(path.join(f.metadata, 'legacy-replacement-pending.json'), JSON.stringify({ version: 1, device_id: id }))
    const c = f.connection(); await c.resume()
    assert.deepEqual(s.calls.map(c => c.url), ['/api/local-tools/runtime/replacement/complete', '/api/local-tools/runtime/activate'])
    assert.equal(f.starts, 1); assert.equal(fs.existsSync(path.join(f.metadata, 'legacy-replacement-pending.json')), false)
  } finally { f.clean(); await s.close() }
})

test('legacy activation resumes after a real HTTP outage and clears its previous diagnosis', { skip }, async () => {
  const f = fixture(); let unavailable = true
  const s = await service(async (_req, res) => {
    if (unavailable) { res.statusCode = 503; res.end('{}'); return true }
    return false
  }), id = randomUUID()
  try {
    fs.writeFileSync(path.join(f.legacy, 'config.json'), JSON.stringify({ server: s.url, device_id: id }))
    fs.writeFileSync(path.join(f.legacy, 'credentials.bin'), await dpapiProtect('a'.repeat(64)))
    fs.writeFileSync(path.join(f.metadata, 'legacy-replacement-pending.json'), JSON.stringify({ version: 1, device_id: id, replacement_pending: false }))
    const c = f.connection(); await c.resume()
    assert.equal((await c.registrationStatus()).category, 'NETWORK_FAILED'); assert.equal(f.starts, 0)
    unavailable = false; await c.resume()
    assert.equal(await c.registrationStatus(), null); assert.equal(f.starts, 1)
    assert.equal(s.calls.every(c => c.url.endsWith('/activate')), true)
  } finally { f.clean(); await s.close() }
})

test('explicit server switch never contacts an unavailable old pending server', { skip }, async () => {
  const f = fixture(), old = await service(async (_req, res) => { res.statusCode = 503; res.end('{}'); return true }), current = await service()
  try {
    const legacy = await registration.beginCleanRegistration(f.metadata, { server: old.url, device_name: 'Old pending fixture' }, [f.legacy])
    await registration.saveCleanRegistration(f.metadata, legacy)
    const c = f.connection()
    const reply = await c.reconfigure({ mode: 'new_device', confirmed: true, server: current.url, device_name: 'New service fixture', pairing_code: 'ISOLATED-OTP' })
    assert.equal(reply.status, 'complete', reply.diagnosis.summary)
    assert.equal(old.calls.length, 0)
    assert.equal(current.devices.size, 1); assert.equal(f.starts, 1)
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.home, 'config.json'), 'utf8')).server, current.url)
  } finally { f.clean(); await old.close(); await current.close() }
})

test('a received paired receipt is installed first and a later explicit submission uses its own code', { skip }, async () => {
  const f = fixture(), s = await service(async (req, res) => {
    if (req.url === '/api/local-tools/runtime/heartbeat') { res.statusCode = 401; res.end('{}'); return true }
    return false
  })
  try {
    const r = await registration.beginCleanRegistration(f.metadata, { server: s.url, device_name: 'Revoked paired receipt' }, [f.legacy])
    await registration.pairCleanRegistration(f.metadata, r, 'FIRST-ISOLATED-OTP')
    const oldId = r.device_id, c = f.connection()
    assert.equal(await c.resume(), true)
    assert.equal(s.calls.filter(call => call.url.endsWith('/heartbeat')).length, 0, 'Local receipt installation requires no network confirmation')
    const reply = await c.reconfigure({ mode: 'new_device', confirmed: true, server: s.url, device_name: 'Fresh receipt fixture', pairing_code: 'NEW-ISOLATED-OTP' })
    assert.equal(reply.status, 'complete', reply.diagnosis.summary)
    assert.equal(s.devices.size, 2); assert.equal(f.starts, 2)
    assert.notEqual(JSON.parse(fs.readFileSync(path.join(f.home, 'config.json'), 'utf8')).device_id, oldId)
    assert.equal(await c.registrationBlocksExecution(), false)
  } finally { f.clean(); await s.close() }
})

test('migration uses real DPAPI and loopback identity check but isolates damaged legacy metadata', { skip }, async () => {
  const f = fixture(), s = await service(), id = randomUUID()
  const entry = path.join(f.root, 'installed/resources/runtime/host/managed-entry.js')
  fs.mkdirSync(path.dirname(entry), { recursive: true }); fs.writeFileSync(entry, 'independent installation key')
  try {
    fs.writeFileSync(path.join(f.legacy, 'config.json'), JSON.stringify({ server: s.url, device_id: id, name: 'Legacy fixture', secret_setting: 'must not migrate' }))
    fs.writeFileSync(path.join(f.legacy, 'credentials.bin'), await dpapiProtect('b'.repeat(64)))
    for (const name of ['management-secret.bin','management-operations.json','host-running.json','journal','outbox']) fs.writeFileSync(path.join(f.legacy, name), '{broken')
    const result = await installation.prepareRuntimeInstallation({ legacyHome: f.legacy, userDataPath: path.join(f.root, 'user'), appId: 'cn.aidingyi.agent.runtime.acceptance', node: process.execPath, hostEntry: entry,
      stopLegacy: async () => {}, protect: dpapiProtect, unprotect: dpapiUnprotect })
    assert.equal(result.migrated, true); assert.deepEqual(fs.readdirSync(result.home).sort(), ['config.json','credentials.bin'])
    assert.equal(JSON.parse(fs.readFileSync(path.join(result.home, 'config.json'), 'utf8')).secret_setting, undefined)
    assert.equal(s.calls[0].url, '/api/local-tools/runtime/heartbeat')
    assert.equal(await dpapiUnprotect(fs.readFileSync(path.join(result.home, 'credentials.bin'), 'utf8')), 'b'.repeat(64))
  } finally { f.clean(); await s.close() }
})

test('real owned Node Host stops and the new paired Host starts through its actual IPC port', { skip }, async () => {
  const f = fixture(), s = await service(), children = []
  let home = f.legacy, supervisor
  fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ server: s.url, device_id: randomUUID() }))
  fs.writeFileSync(path.join(home, 'credentials.bin'), await dpapiProtect('e'.repeat(64)))
  const attach = () => supervisor = new RuntimeSupervisor({ nodeExecutable: process.env.AID_CLEAN_TEST_NODE ?? process.execPath,
    hostEntry: host, home, supervisor: 'runtime_app', takeSelectedPackage: async () => { throw new Error('unused fixture selection') },
    onEvent() {}, onDisconnect() {}, spawnChild(node, args, options) { const child = spawn(node, args, options); children.push(child); return child } })
  attach()
  const c = new RuntimeCleanConnection({ metadataHome: f.metadata, hostEntry: host,
    home: () => home, supervisor: () => supervisor, beforeChange: async () => {}, restorePriorHome: async () => false,
    stageHome: async () => { const staged = path.join(f.root, 'owned-new-' + randomUUID()); fs.mkdirSync(staged); return staged },
    validateHome() {},
    commitHome: async target => { home = target; attach() },
  })
  try {
    const original = await supervisor.request({ request_id: randomUUID(), method: 'describe', params: {} })
    assert.equal(original.code, 0); assert.equal(children.length, 1)
    await resumeRuntimeAtLogin(value => supervisor.request(value, 'startup'))
    assert.equal(fs.existsSync(path.join(f.legacy, 'host-running.json')), true)
    const oldPid = children[0].pid
    const reply = await c.reconfigure({ mode: 'new_device', confirmed: true, server: s.url, device_name: 'Real owned Host fixture', pairing_code: 'ISOLATED-OTP' })
    assert.equal(reply.status, 'complete', reply.diagnosis.summary)
    assert.notEqual(children[0].exitCode, null)
    assert.equal(fs.existsSync(path.join(f.legacy, 'host-running.json')), false, 'The real Host stop removes its active marker before confirmation')
    assert.equal(supervisor.instanceId.length > 0, true)
    const state = await supervisor.request({ request_id: randomUUID(), method: 'getState', params: {} })
    assert.equal(state.code, 0); assert.equal(state.result.state, 'running')
    assert.notEqual(children.at(-1).pid, oldPid)
    assert.equal(s.devices.size, 1)
    assert.equal(await c.registrationBlocksExecution(), false)
    await supervisor.exitForReplacement()
    assert.notEqual(children.at(-1).exitCode, null)
    console.log('OWNED_HOST_REPLACEMENT_AND_AUTOSTART_PASS')
  } finally {
    c.dispose()
    for (const child of children) if (child.exitCode === null) {
      child.kill(); await new Promise(resolve => child.once('exit', resolve))
    }
    f.clean(); await s.close()
  }
})

test('real Main IPC can resume the original device without falsely reporting old processes stopped', { skip, timeout: 30_000 }, async () => {
  const f = fixture(), s = await service()
  const id = randomUUID(), marker = '{"old":"unreadable supervision"}'
  fs.writeFileSync(path.join(f.home, 'config.json'), JSON.stringify({ server: s.url, device_id: id }))
  fs.writeFileSync(path.join(f.home, 'credentials.bin'), await dpapiProtect('c'.repeat(64)))
  fs.writeFileSync(path.join(f.home, 'host-running.json'), marker)
  const supervisor = new RuntimeSupervisor({ nodeExecutable: process.env.AID_CLEAN_TEST_NODE ?? process.execPath,
    hostEntry: host, home: f.home, supervisor: 'runtime_app', takeSelectedPackage: async () => { throw new Error('unused fixture selection') },
    onEvent() {}, onDisconnect() {} })
  const request = value => supervisor.request(value, 'startup')
  try {
    const before = await request({ request_id: randomUUID(), method: 'getState', params: {} })
    assert.equal(before.result.device.device_id, id)
    assert.equal(supervisor.interruptedExecution, true)
    assert.equal(supervisor.recoveredAfterInterruption, false)
    await resumeRuntimeAtLogin(request, () => new Promise(resolve => setTimeout(resolve, 15)))
    for (let attempt = 0; !s.calls.length && attempt < 200; attempt++) await new Promise(resolve => setTimeout(resolve, 15))
    assert.deepEqual(s.calls.map(call => call.url), ['/api/local-tools/runtime/heartbeat'])
    const pending = fs.readdirSync(f.home).find(name => name.startsWith('host-resource-pending-'))
    assert.equal(fs.readFileSync(path.join(f.home, pending), 'utf8'), marker)
  } finally { await supervisor.stopAndExit(); f.clean(); await s.close() }
})

test('real Main refuses pairing confirmation while execution evidence remains and keeps the original identity available', { skip, timeout: 40_000 }, async t => {
  for (const withChild of [false, true]) await t.test(withChild ? 'running restricted Host' : 'cold Main without a child', async () => {
    const f = fixture(), s = await service()
    const original = f.legacy, id = randomUUID(), marker = '{"old":"execution proof unavailable"}'
    const credentials = await dpapiProtect('d'.repeat(64))
    fs.writeFileSync(path.join(original, 'config.json'), JSON.stringify({ server: s.url, device_id: id }))
    fs.writeFileSync(path.join(original, 'credentials.bin'), credentials)
    fs.writeFileSync(path.join(original, 'host-running.json'), marker)
    let home = original, supervisor
    const attach = () => supervisor = new RuntimeSupervisor({ nodeExecutable: process.execPath, hostEntry: host, home,
      supervisor: 'runtime_app', takeSelectedPackage: async () => { throw new Error('unused fixture selection') }, onEvent() {}, onDisconnect() {} })
    attach()
    const connection = new RuntimeCleanConnection({ metadataHome: f.metadata, hostEntry: host,
      home: () => home, supervisor: () => supervisor, beforeChange: async () => {}, restorePriorHome: async () => false,
      stageHome: async () => { const staged = path.join(f.root, 'replacement'); fs.mkdirSync(staged); return staged },
      validateHome() {}, commitHome: async target => { home = target; attach() } })
    const request = value => supervisor.request(value, 'startup')
    const resume = () => resumeRuntimeAtLogin(request, () => new Promise(resolve => setTimeout(resolve, 15)))
    try {
      if (withChild) await resume()
      const previous = supervisor.instanceId
      const reply = await connection.reconfigure({ mode: 'new_device', confirmed: true, server: s.url,
        device_name: 'Isolated fixture', pairing_code: 'ISOLATED-OTP' })
      assert.equal(s.calls.filter(call => call.url.endsWith('/pair/confirm')).length, 0, 'Old execution evidence forbids revoking the original device')
      assert.equal(reply.diagnosis.category, 'PAIRING_STOP_FAILED')
      assert.equal(home, original)
      assert.equal(await registration.readCleanRegistration(f.metadata), null)
      assert.equal(fs.readFileSync(path.join(original, 'credentials.bin'), 'utf8'), credentials)
      if (!withChild) {
        assert.equal(fs.readFileSync(path.join(original, 'host-running.json'), 'utf8'), marker)
        await resume() // A post-exit rejection must reopen the same supervisor for startup.
      } else assert.equal(supervisor.instanceId, previous, 'A running restricted Host keeps its original management connection')
      const pending = fs.readdirSync(original).find(name => name.startsWith('host-resource-pending-'))
      assert.equal(fs.readFileSync(path.join(original, pending), 'utf8'), marker)
      let online = false
      for (let attempt = 0; !online && attempt < 150; attempt++) {
        const state = await request({ request_id: randomUUID(), method: 'getState', params: {} })
        assert.equal(state.result.device.device_id, id)
        online = state.result.connection === 'online'
        if (!online) await new Promise(resolve => setTimeout(resolve, 15))
      }
      assert.equal(online, true)
    } finally { connection.dispose(); await supervisor.stopAndExit(); f.clean(); await s.close() }
  })
})
