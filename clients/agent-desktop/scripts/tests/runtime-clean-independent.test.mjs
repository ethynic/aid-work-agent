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
    if (req.url === '/api/local-tools/runtime/pair') {
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
      supervisors.set(target, { recoveryManagementRequest: async method => {
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
  const options = { metadataHome: metadata, hostEntry: host, legacyHome: legacy, previousHome: null, home: () => home, supervisor,
    beforeChange: async () => { stops++; state = 'stopped' },
    replaceHome: async target => {
      home = target ?? path.join(root, 'new-' + randomUUID()); fs.mkdirSync(home, { recursive: true }); return home
    } }
  const instances = []
  return { root, metadata, legacy, get home() { return home }, get starts() { return starts }, get stops() { return stops },
    connection() { const c = new RuntimeCleanConnection(options); instances.push(c); return c },
    clean() { for (const c of instances) c.dispose(); assert.equal(path.dirname(root), path.resolve(os.tmpdir())); assert.match(path.basename(root), /^aid-independent-clean-/); fs.rmSync(root, { recursive: true, force: true }) } }
}

test('real loopback response loss resumes after restart without OTP and starts exactly once', { skip }, async () => {
  const f = fixture(); let dropped = false, committed
  const s = await service(async (req, res, body) => {
    if (req.url !== '/api/local-tools/runtime/pair') return false
    if (!dropped) { dropped = true; committed = { ...body, id: randomUUID() }; req.socket.destroy(); return true }
    assert.equal(body.code, undefined)
    assert.equal(body.registration_id, committed.registration_id)
    assert.equal(body.registration_token, committed.registration_token)
    res.end(JSON.stringify({ success: true, pairing_protocol: 3, registration_completed: true, device_id: committed.id, device_token: body.registration_token })); return true
  })
  try {
    fs.writeFileSync(path.join(f.metadata, 'registration-lock'), '{corrupt legacy lock bytes')
    for (const name of ['config.json','credentials.bin','management-secret.bin','host-running.json','journal','outbox','session-tasks','management-operations.json']) fs.writeFileSync(path.join(f.legacy, name), '{broken')
    const c = f.connection()
    const reply = await c.reconfigure({ mode: 'register_new', confirmed: true, server: s.url, device_name: 'Independent device', pairing_code: 'ISOLATED-OTP' })
    assert.equal(reply.status, 'action_required'); assert.equal(reply.diagnosis.category, 'CONNECTING')
    assert.notEqual(f.home, f.legacy); assert.deepEqual(fs.readdirSync(f.home), [])
    const receipt = await registration.readCleanRegistration(f.metadata)
    assert.equal(receipt.phase, 'requested'); assert.equal(receipt.request.previous_device_token, undefined)
    assert.equal(JSON.stringify(receipt).includes('ISOLATED-OTP'), false)
    assert.equal(fs.readFileSync(path.join(f.metadata, 'clean-registration.bin'), 'utf8').includes('registration_token'), false)
    c.dispose()
    const resumed = f.connection(); await resumed.resume()
    assert.equal(await resumed.registrationBlocksExecution(), false); assert.equal(await resumed.registrationStatus(), null)
    assert.equal(f.starts, 1)
    assert.deepEqual(fs.readdirSync(f.home).sort(), ['config.json','credentials.bin'])
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.home, 'config.json'), 'utf8')).device_id, committed.id)
    assert.equal(await dpapiUnprotect(fs.readFileSync(path.join(f.home, 'credentials.bin'), 'utf8')), committed.registration_token)
    await resumed.resume(); assert.equal(f.starts, 1)
    assert.equal(s.calls.length, 2)
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

test('invalid code clears old execution state while a subsequent new code completes automatically', { skip }, async () => {
  const f = fixture(); let reject = true
  const s = await service(async (req, res) => {
    if (req.url.endsWith('/pair') && reject) { res.statusCode = 400; res.end(JSON.stringify({ detail: 'invalid fixture code' })); return true }
    return false
  })
  try {
    fs.writeFileSync(path.join(f.legacy, 'host-running.json'), 'broken old state')
    const c = f.connection(), input = { mode: 'new_device', confirmed: true, server: s.url, device_name: 'New code fixture', pairing_code: 'ISOLATED-OTP' }
    const first = await c.reconfigure(input)
    assert.equal(first.diagnosis.category, 'PAIRING_CODE_INVALID'); assert.notEqual(f.home, f.legacy)
    assert.deepEqual(fs.readdirSync(f.home), []); assert.equal(f.starts, 0)
    reject = false
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
    assert.equal((await c.registrationStatus()).category, 'CONNECTING'); assert.equal(f.starts, 0)
    unavailable = false; await c.resume()
    assert.equal(await c.registrationStatus(), null); assert.equal(f.starts, 1)
    assert.equal(s.calls.every(c => c.url.endsWith('/activate')), true)
  } finally { f.clean(); await s.close() }
})

test('explicit server switch never contacts an unavailable old pending server', { skip }, async () => {
  const f = fixture(), old = await service(async (_req, res) => { res.statusCode = 503; res.end('{}'); return true }), current = await service()
  try {
    await registration.beginCleanRegistration(f.metadata, { server: old.url, device_name: 'Old pending fixture' }, [f.legacy])
    const c = f.connection()
    const reply = await c.reconfigure({ mode: 'new_device', confirmed: true, server: current.url, device_name: 'New service fixture', pairing_code: 'ISOLATED-OTP' })
    assert.equal(reply.status, 'complete', reply.diagnosis.summary)
    assert.equal(old.calls.length, 0)
    assert.equal(current.devices.size, 1); assert.equal(f.starts, 1)
    assert.equal(JSON.parse(fs.readFileSync(path.join(f.home, 'config.json'), 'utf8')).server, current.url)
  } finally { f.clean(); await old.close(); await current.close() }
})

test('a revoked paired receipt can be replaced with a new code instead of blocking forever', { skip }, async () => {
  const f = fixture(), s = await service(async (req, res) => {
    if (req.url === '/api/local-tools/runtime/heartbeat') { res.statusCode = 401; res.end('{}'); return true }
    return false
  })
  try {
    const r = await registration.beginCleanRegistration(f.metadata, { server: s.url, device_name: 'Revoked paired receipt' }, [f.legacy])
    await registration.pairCleanRegistration(f.metadata, r, 'FIRST-ISOLATED-OTP')
    const oldId = r.device_id, c = f.connection()
    const reply = await c.reconfigure({ mode: 'new_device', confirmed: true, server: s.url, device_name: 'Fresh receipt fixture', pairing_code: 'NEW-ISOLATED-OTP' })
    assert.equal(reply.status, 'complete', reply.diagnosis.summary)
    assert.equal(s.devices.size, 2); assert.equal(f.starts, 1)
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

test('real owned Node Host is force replaced and the new paired Host starts through its actual IPC port', { skip }, async () => {
  const f = fixture(), s = await service(), children = []
  let home = f.legacy, supervisor
  const attach = () => supervisor = new RuntimeSupervisor({ nodeExecutable: process.env.AID_CLEAN_TEST_NODE ?? process.execPath,
    hostEntry: host, home, supervisor: 'runtime_app', takeSelectedPackage: async () => { throw new Error('unused fixture selection') },
    onEvent() {}, onDisconnect() {}, spawnChild(node, args, options) { const child = spawn(node, args, options); children.push(child); return child } })
  attach()
  const c = new RuntimeCleanConnection({ metadataHome: f.metadata, hostEntry: host, legacyHome: f.legacy, previousHome: null,
    home: () => home, supervisor: () => supervisor, beforeChange: async () => {}, replaceHome: async target => {
      await supervisor.exitForReplacement()
      home = target ?? path.join(f.root, 'owned-new-' + randomUUID()); fs.mkdirSync(home, { recursive: true }); attach(); return home
    } })
  try {
    const original = await supervisor.request({ request_id: randomUUID(), method: 'describe', params: {} })
    assert.equal(original.code, 0); assert.equal(children.length, 1)
    const oldPid = children[0].pid
    const reply = await c.reconfigure({ mode: 'new_device', confirmed: true, server: s.url, device_name: 'Real owned Host fixture', pairing_code: 'ISOLATED-OTP' })
    assert.equal(reply.status, 'complete', reply.diagnosis.summary)
    assert.notEqual(children[0].exitCode, null)
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
