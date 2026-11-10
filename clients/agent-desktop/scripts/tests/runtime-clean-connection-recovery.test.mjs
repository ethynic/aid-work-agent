import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { mkdir, mkdtemp, rm, rmdir, writeFile, readFile, readdir } from 'node:fs/promises'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { RuntimeCleanConnection } from '../../dist/electron/runtimeCleanConnection.js'
import { prepareRuntimeInstallation, restoreRuntimePriorConnection, rotateRuntimeConnection,
  selectRuntimeConnection, stageRuntimeConnection, validateRuntimeConnectionTarget } from '../../dist/electron/runtimeInstallation.js'
import { beginCleanRegistration, readCleanRegistration, saveCleanRegistration } from '../../../agent-tool-runtime/dist/src/cleanRegistration.js'
import { dpapiProtect } from '../../../agent-tool-runtime/dist/src/dpapi.js'

const entry = fileURLToPath(new URL('../../../agent-tool-runtime/dist/src/managed-entry.js', import.meta.url))
const oldToken = 'a'.repeat(64)
const windows = { skip: process.platform !== 'win32' }
const begin = (server, otp = 'synthetic-code') => ({ mode: 'register_new', confirmed: true, server, device_name: 'Fixture', pairing_code: otp })
const pairPath = '/api/local-tools/runtime/pair'
const heartbeatPath = '/api/local-tools/runtime/heartbeat'
const paired = (request, device) => ({ success: true, pairing_protocol: 3, registration_completed: true, device_id: device, device_token: request.registration_token })

async function fixture(t, respond) {
  const root = await mkdtemp(path.join(tmpdir(), 'aid-recovery-pair-'))
  const calls = []
  const server = createServer(async (req, res) => {
    const bytes = []
    for await (const chunk of req) bytes.push(chunk)
    const call = { path: req.url, token: req.headers.authorization, body: JSON.parse(Buffer.concat(bytes).toString() || '{}') }
    calls.push(call)
    const result = await respond(call)
    if (!result) { req.socket.destroy(); return }
    res.writeHead(result.status ?? 200, { 'Content-Type': 'application/json', ...result.headers }).end(JSON.stringify(result.body ?? {}))
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const origin = 'http://127.0.0.1:' + server.address().port
  const installOptions = { node: process.execPath, hostEntry: entry, userDataPath: path.join(root, 'user'), legacyHome: path.join(root, 'legacy'),
    appId: 'cn.aidingyi.agent.runtime.acceptance', stopLegacy: async () => {}, migratePlugins: async () => {} }
  const installed = await prepareRuntimeInstallation(installOptions)
  const metadata = installed.metadataHome
  const original = installed.home
  const originalId = randomUUID()
  let home = original, stageCount = 0, starts = 0, stopCount = 0, startFails = false, commitFails = false, state = 'stopped'
  let connection
  const supervisor = {
    async stopAndExit(_timeout, beforeStop) { stopCount++; await beforeStop?.({ state }); state = 'stopped' },
    suspend() {},
    async request(request) {
      if (request.method === 'getState') return { code: 0, result: { state: startFails ? 'reconciling' : state, device: { device_id: originalId } } }
      if (request.method === 'start') { starts++; state = 'running'; return { code: 0, result: { operation_id: 'fixture-op' } } }
      return { code: 0, result: { status: 'succeeded', code: 0 } }
    },
  }
  function open() {
    connection?.dispose()
    connection = new RuntimeCleanConnection({
      metadataHome: metadata, hostEntry: entry, home: () => home, supervisor: () => supervisor,
      restorePriorHome: async requested => {
        const restored = await restoreRuntimePriorConnection(installOptions, home, requested)
        if (restored) home = restored
        return !!restored
      },
      stageHome: async () => { stageCount++; return stageRuntimeConnection(installOptions, home) },
      validateHome: target => validateRuntimeConnectionTarget(installOptions, target, home),
      commitHome: async target => {
        const saved = await readCleanRegistration(metadata)
        assert.equal(saved.phase, 'installed', 'The pointer must not move before durable identity installation')
        const config = JSON.parse(await readFile(path.join(target, 'config.json'), 'utf8'))
        assert.equal(config.device_id, saved.device_id)
        await readFile(path.join(target, 'credentials.bin'))
        if (commitFails) { commitFails = false; throw new Error('isolated pointer interruption') }
        home = await selectRuntimeConnection(installOptions, target, home)
      },
      beforeChange: async () => {},
    })
    return connection
  }
  t.after(async () => {
    connection?.dispose()
    server.closeAllConnections()
    server.close()
    await rm(root, { recursive: true, force: true })
  })
  return { origin, original, originalId, open, metadata, calls,
    get home() { return home }, get stages() { return stageCount }, get stops() { return stopCount }, get starts() { return starts },
    failStart() { startFails = true },
    failCommitOnce() { commitFails = true },
    setRunning() { state = 'running' },
    async setOriginal(address = origin, token = oldToken) {
      await writeFile(path.join(original, 'config.json'), JSON.stringify({ server: address, device_id: originalId }))
      await writeFile(path.join(original, 'credentials.bin'), await dpapiProtect(token))
      await writeFile(path.join(original, 'host-running.json'), 'retained history')
    },
    async legacy(address, alreadyRotated = false) {
      if (alreadyRotated) home = await rotateRuntimeConnection(installOptions, home)
      const record = await beginCleanRegistration(metadata, { server: address, device_name: 'Fixture' }, [])
      const old = { ...record, home: alreadyRotated ? home : null }
      await saveCleanRegistration(metadata, old)
      return old
    },
  }
}

test('a legacy HTTP transaction cannot gate the original HTTPS home, including an old empty-home switch', windows, async t => {
  const f = await fixture(t, () => ({ status: 500 }))
  await f.setOriginal('https://127.0.0.1:1')
  await f.legacy(f.origin)
  let recovery = f.open()
  assert.equal(await recovery.registrationBlocksExecution(), false)
  assert.equal(await recovery.resume(), false)
  assert.equal(f.home, f.original)
  assert.equal(f.calls.length, 0)
  assert.equal(await readCleanRegistration(f.metadata), null, 'A legacy request must leave the active entry without being sent')

  await f.legacy(f.origin, true)
  assert.notEqual(f.home, f.original)
  recovery = f.open()
  assert.equal(await recovery.resume(), false)
  assert.equal(recovery.restoredOriginal, true)
  assert.equal(await recovery.registrationBlocksExecution(), false)
  assert.equal(f.home, f.original)
  assert.equal(f.calls.length, 0, 'Restoration must not require the original server to be reachable')
  assert.equal(await readFile(path.join(f.home, 'host-running.json'), 'utf8'), 'retained history')
  assert.equal(await readCleanRegistration(f.metadata), null)
})

test('HTTP 301 never follows a redirect, changes the pointer, or resends across restart and UI checks', windows, async t => {
  const redirectTarget = await fixture(t, () => ({ status: 500 }))
  const f = await fixture(t, call => call.path === pairPath
    ? { status: 301, headers: { Location: redirectTarget.origin + pairPath } }
    : { body: { success: true, selected: true } })
  await f.setOriginal()
  f.setRunning()
  const metadataBefore = await readdir(f.metadata)
  let recovery = f.open()
  const result = await recovery.reconfigure(begin(f.origin))
  assert.equal(result.diagnosis.category, 'PAIRING_REDIRECT')
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.deepEqual(await readdir(f.metadata), metadataBefore, 'A failed request must not write a failed record or archive')
  recovery = f.open()
  await recovery.resume()
  await recovery.diagnose()
  const manual = await recovery.reconfigure({ mode: 'continue_registration', confirmed: true })
  assert.equal(manual.status, 'action_required')
  assert.equal(await recovery.registrationBlocksExecution(), false)
  assert.equal(f.calls.filter(call => call.path === pairPath).length, 1)
  assert.equal(f.home, f.original)
  assert.equal(f.starts, 1, 'An already running original Host can resume after the explicit submission fails')
  assert.equal(f.stages, 0)
  assert.equal(redirectTarget.calls.length, 0)
})

test('lost response is reported once even if the original token was revoked by a cloud commit', windows, async t => {
  const f = await fixture(t, call => {
    if (call.path === heartbeatPath) return { status: call.token === 'Bearer ' + oldToken ? 401 : 200, body: { success: true, selected: true } }
    return null
  })
  await f.setOriginal()
  let recovery = f.open()
  const result = await recovery.reconfigure(begin(f.origin))
  assert.equal(result.status, 'action_required')
  assert.equal(result.diagnosis.category, 'REGISTRATION_UNCONFIRMED')
  assert.equal(f.home, f.original)
  assert.equal(f.stages, 0)
  assert.equal(f.calls[0].body.previous_device_token, oldToken)
  assert.equal(await readCleanRegistration(f.metadata), null)
  recovery = f.open()
  await recovery.resume()
  assert.equal((await recovery.diagnose()).category, 'CREDENTIALS_REVOKED')
  assert.equal(f.calls.filter(call => call.path === pairPath).length, 1)
  assert.equal(JSON.parse(await readFile(path.join(f.home, 'config.json'), 'utf8')).device_id, f.originalId)
})

test('server errors and invalid success replies never create failed transaction files or allocate a home', windows, async t => {
  for (const reply of [{ status: 503 }, { body: { success: true } }]) {
    const f = await fixture(t, () => reply)
    await f.setOriginal()
    const before = await readdir(f.metadata)
    assert.equal((await f.open().reconfigure(begin(f.origin))).diagnosis.category, 'REGISTRATION_UNCONFIRMED')
    await f.open().resume()
    assert.equal(f.calls.length, 1)
    assert.equal(f.stages, 0)
    assert.equal(await readCleanRegistration(f.metadata), null)
    assert.deepEqual(await readdir(f.metadata), before)
    assert.equal(f.home, f.original)
  }
})

test('interrupted local commit can resume without pairing even when Host start fails', windows, async t => {
  const ready = await fixture(t, call => call.path === pairPath ? { body: paired(call.body, randomUUID()) } : { body: { success: true, selected: true } })
  await ready.setOriginal()
  ready.failStart()
  ready.failCommitOnce()
  let recovery = ready.open()
  assert.equal((await recovery.reconfigure(begin(ready.origin))).diagnosis.category, 'REGISTRATION_CLEANUP_PENDING')
  assert.equal(ready.home, ready.original)
  assert.equal((await readCleanRegistration(ready.metadata)).phase, 'installed')
  ready.failCommitOnce()
  assert.equal((await recovery.diagnose()).category, 'REGISTRATION_CLEANUP_PENDING', 'A healthy original server must not hide a failed local commit')
  recovery = ready.open()
  assert.equal(await recovery.resume(), true)
  assert.notEqual(ready.home, ready.original)
  assert.equal((await recovery.registrationStatus()).category, 'EXECUTION_START_FAILED')
  assert.equal(await readCleanRegistration(ready.metadata), null)
  recovery = ready.open()
  await recovery.resume()
  assert.equal(ready.calls.length, 1, 'A committed device is never paired again to retry Host start')
})

test('a confirmed reply survives the first local receipt write failing without another pairing POST', windows, async t => {
  const device = randomUUID()
  let receipt
  const f = await fixture(t, async call => {
    await mkdir(receipt)
    return { body: paired(call.body, device) }
  })
  receipt = path.join(f.metadata, 'clean-registration.bin')
  await f.setOriginal()
  const recovery = f.open()
  assert.equal((await recovery.reconfigure(begin(f.origin))).diagnosis.category, 'REGISTRATION_CLEANUP_PENDING')
  assert.equal(f.home, f.original)
  assert.equal(f.stages, 0)
  assert.equal((await recovery.reconfigure({ mode: 'continue_registration', confirmed: true })).diagnosis.category, 'REGISTRATION_CLEANUP_PENDING')
  await rmdir(receipt)
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.equal(await recovery.registrationBlocksExecution(), true)

  const second = await recovery.reconfigure(begin(f.origin, 'second-synthetic-code'))
  assert.equal(second.diagnosis.category, 'REGISTRATION_CLEANUP_PENDING')
  assert.equal(f.calls.filter(call => call.path === pairPath).length, 1)
  const finished = await recovery.reconfigure({ mode: 'continue_registration', confirmed: true })
  assert.equal(finished.status, 'complete', finished.diagnosis.summary)
  assert.equal(JSON.parse(await readFile(path.join(f.home, 'config.json'), 'utf8')).device_id, device)
  assert.equal(f.calls.filter(call => call.path === pairPath).length, 1)
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.equal(await recovery.registrationBlocksExecution(), false)
})

test('only a new explicit submission can pair after a failed request', windows, async t => {
  const f = await fixture(t, call => call.body.code === 'retry-code' ? { body: paired(call.body, randomUUID()) } : { status: 400 })
  await f.setOriginal()
  const recovery = f.open()
  assert.equal((await recovery.reconfigure(begin(f.origin))).diagnosis.category, 'PAIRING_CODE_INVALID')
  assert.equal(await recovery.resume(), false)
  assert.equal(f.calls.length, 1)
  assert.equal((await recovery.reconfigure(begin(f.origin, 'retry-code'))).new_device, true)
  assert.equal(f.calls.length, 2)
  assert.notEqual(f.calls[0].body.registration_id, f.calls[1].body.registration_id)
  assert.equal(f.calls[1].body.code, 'retry-code')
})

test('a confirmed receipt already present in the active home only finishes the local commit', windows, async t => {
  const f = await fixture(t, () => ({ status: 500 }))
  const r = await beginCleanRegistration(f.metadata, { server: f.origin, device_name: 'Fixture' }, [])
  r.phase = 'paired'; r.device_id = f.originalId
  await saveCleanRegistration(f.metadata, r)
  await f.setOriginal(f.origin, r.request.registration_token)
  assert.equal(await f.open().resume(), true)
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.equal(f.home, f.original)
  assert.equal(f.stages, 0)
  assert.equal(f.calls.length, 0)
})

test('an in-flight user submission rejects a concurrent submission before the network', windows, async t => {
  let arrived, release
  const firstRequest = new Promise(resolve => { arrived = resolve })
  const hold = new Promise(resolve => { release = resolve })
  const f = await fixture(t, async () => { arrived(); await hold; return { status: 400 } })
  const recovery = f.open()
  const running = recovery.reconfigure(begin(f.origin))
  try {
    await firstRequest
    const other = await recovery.reconfigure(begin(f.origin, 'second-synthetic-code'))
    assert.equal(other.status, 'action_required')
    assert.equal(other.diagnosis.category, 'CONNECTING')
    assert.equal(f.calls.length, 1)
  } finally { release(); await running }
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.equal(f.calls.length, 1)
})
