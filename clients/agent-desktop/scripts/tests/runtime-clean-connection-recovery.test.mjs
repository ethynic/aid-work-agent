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
import { beginCleanRegistration, prepareCleanRegistration, readCleanRegistration, saveCleanRegistration } from '../../../agent-tool-runtime/dist/src/cleanRegistration.js'
import { dpapiProtect } from '../../../agent-tool-runtime/dist/src/dpapi.js'

const entry = fileURLToPath(new URL('../../../agent-tool-runtime/dist/src/managed-entry.js', import.meta.url))
const oldToken = 'a'.repeat(64)
const windows = { skip: process.platform !== 'win32' }
const begin = (server, otp = 'synthetic-code') => ({ mode: 'register_new', confirmed: true, server, device_name: 'Fixture', pairing_code: otp })
const pairPath = '/api/local-tools/runtime/pair/prepare'
const confirmPath = '/api/local-tools/runtime/pair/confirm'
const statusPath = '/api/local-tools/runtime/pair/status'
const heartbeatPath = '/api/local-tools/runtime/heartbeat'
const receipt = request => ({ success: true, pairing_protocol: 4, registration_id: request.registration_id })
const prepared = request => ({ ...receipt(request), preparation_id: randomUUID(), preparation_token: 'b'.repeat(64) })
const paired = (request, device) => ({ ...receipt(request), registration_completed: true, device_id: device })
const success = call => ({ body: call.path === pairPath ? prepared(call.body) : call.path === confirmPath
  ? paired(call.body, randomUUID()) : { success: true, selected: true } })

async function fixture(t, respond) {
  const root = await mkdtemp(path.join(tmpdir(), 'aid-recovery-pair-'))
  const calls = []
  const diagnostics = []
  const server = createServer(async (req, res) => {
    const bytes = []
    for await (const chunk of req) bytes.push(chunk)
    const call = { path: req.url, token: req.headers.authorization, body: JSON.parse(Buffer.concat(bytes).toString() || '{}'),
      disconnect: () => req.socket.destroy() }
    calls.push(call)
    const result = await respond(call)
    if (req.socket.destroyed) return
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
  let home = original, stageCount = 0, starts = 0, stopCount = 0, stopFails = false, startFails = false, commitFails = false, state = 'stopped'
  let connection
  const supervisor = {
    async stopAndExit(_timeout, beforeStop) { stopCount++; if (stopFails) throw new Error('isolated stop rejection'); await beforeStop?.({ state }); state = 'stopped' },
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
      recordDiagnostic: (event, details) => diagnostics.push({ event, ...details }),
    })
    return connection
  }
  t.after(async () => {
    connection?.dispose()
    server.closeAllConnections()
    server.close()
    await rm(root, { recursive: true, force: true })
  })
  return { origin, original, originalId, open, metadata, calls, diagnostics,
    get home() { return home }, get stages() { return stageCount }, get stops() { return stopCount }, get starts() { return starts },
    failStart() { startFails = true },
    failStop() { stopFails = true },
    failCommitOnce() { commitFails = true },
    setRunning() { state = 'running' },
    async setOriginal(address = origin, token = oldToken) {
      await writeFile(path.join(original, 'config.json'), JSON.stringify({ server: address, device_id: originalId }))
      await writeFile(path.join(original, 'credentials.bin'), await dpapiProtect(token))
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
  await writeFile(path.join(f.home, 'host-running.json'), 'retained history')
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
  assert.equal(f.stops, 0, 'A rejected prepare does not interrupt the already running original Host')
  assert.equal(f.stages, 0)
  assert.equal(redirectTarget.calls.length, 0)
  assert.deepEqual(f.diagnostics, [
    { event: 'runtime-pair-prepare-started', protocol: 'http' },
    { event: 'runtime-pair-prepare-failed', protocol: 'http', reason: 'redirect', httpStatus: 301 },
    { event: 'runtime-diagnostic-heartbeat-started', protocol: 'http' },
    { event: 'runtime-diagnostic-heartbeat-succeeded', protocol: 'http' },
  ])
})

test('a lost prepare response leaves the original identity intact and is never resubmitted', windows, async t => {
  const f = await fixture(t, call => {
    if (call.path === heartbeatPath) return { body: { success: true, selected: true } }
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
  assert.equal((await recovery.diagnose()).service_verified, true)
  assert.equal(f.calls.filter(call => call.path === pairPath).length, 1)
  assert.equal(JSON.parse(await readFile(path.join(f.home, 'config.json'), 'utf8')).device_id, f.originalId)
})

test('a confirmation redirect cannot leak candidate proof or cause status replay', windows, async t => {
  const redirect = await fixture(t, () => ({ status: 500 }))
  const f = await fixture(t, call => call.path === confirmPath
    ? { status: 301, headers: { Location: redirect.origin + confirmPath } }
    : success(call))
  await f.setOriginal()
  const result = await f.open().reconfigure(begin(f.origin))
  assert.equal(result.diagnosis.category, 'PAIRING_REDIRECT')
  assert.equal(f.home, f.original)
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.equal(await f.open().resume(), false)
  assert.deepEqual(f.calls.map(call => call.path), [pairPath, confirmPath])
  assert.equal(redirect.calls.length, 0)
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
  const ready = await fixture(t, success)
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
  assert.deepEqual(ready.calls.map(c => c.path), [pairPath, confirmPath], 'A committed device is never paired again to retry Host start')
})

test('a confirmed reply survives the first local receipt write failing without another pairing POST', windows, async t => {
  const device = randomUUID()
  let receipt
  const f = await fixture(t, async call => {
    if (call.path === pairPath) return success(call)
    assert.equal((await readCleanRegistration(f.metadata)).phase, 'confirming')
    await rm(receipt)
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
  assert.equal(f.calls.filter(call => call.path === confirmPath).length, 1)
  const finished = await recovery.reconfigure({ mode: 'continue_registration', confirmed: true })
  assert.equal(finished.status, 'complete', finished.diagnosis.summary)
  assert.equal(JSON.parse(await readFile(path.join(f.home, 'config.json'), 'utf8')).device_id, device)
  assert.equal(f.calls.filter(call => call.path === pairPath).length, 1)
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.equal(await recovery.registrationBlocksExecution(), false)
})

test('only a new explicit submission can pair after a failed request', windows, async t => {
  const f = await fixture(t, call => call.path === pairPath && call.body.code !== 'retry-code' ? { status: 400 } : success(call))
  await f.setOriginal()
  const recovery = f.open()
  assert.equal((await recovery.reconfigure(begin(f.origin))).diagnosis.category, 'PAIRING_CODE_INVALID')
  assert.equal(await recovery.resume(), false)
  assert.equal(f.calls.length, 1)
  assert.equal((await recovery.reconfigure(begin(f.origin, 'retry-code'))).new_device, true)
  assert.deepEqual(f.calls.map(c => c.path), [pairPath, pairPath, confirmPath])
  assert.notEqual(f.calls[0].body.registration_id, f.calls[1].body.registration_id)
  assert.equal(f.calls[1].body.code, 'retry-code')
})

test('a candidate write failure, rejected stop, or retained resource sends no confirmation and keeps the original home', windows, async t => {
  for (const failedAt of ['candidate-write', 'stop', 'pending-resources']) {
    const f = await fixture(t, async call => {
      if (failedAt === 'candidate-write') await mkdir(file)
      return success(call)
    })
    await f.setOriginal()
    const file = path.join(f.metadata, 'clean-registration.bin')
    if (failedAt === 'stop') f.failStop()
    if (failedAt === 'pending-resources') await writeFile(path.join(f.home, `host-resource-pending-${randomUUID()}.json`), 'old resource evidence')
    const result = await f.open().reconfigure(begin(f.origin))
    assert.equal(result.diagnosis.category, failedAt === 'candidate-write' ? 'LOCAL_SYSTEM_ERROR' : 'PAIRING_STOP_FAILED')
    if (failedAt === 'candidate-write') await rmdir(file)
    assert.equal(await readCleanRegistration(f.metadata), null)
    assert.equal(f.home, f.original)
    assert.equal(f.stages, 0)
    await f.open().resume()
    assert.deepEqual(f.calls.map(c => c.path), [pairPath])
  }
})

test('lost confirmation is only queried, retains candidate while pending, and installs the exact committed device', windows, async t => {
  let outcome = 'prepared'
  const device = randomUUID()
  const f = await fixture(t, async call => {
    if (call.path === pairPath) return success(call)
    if (call.path === confirmPath) {
      const stored = await readCleanRegistration(f.metadata)
      assert.equal(stored.phase, 'confirming')
      assert.equal(stored.request.registration_token, call.body.registration_token)
      assert.equal(Object.hasOwn(stored.request, 'code'), false)
      assert.equal(Object.hasOwn(stored.request, 'previous_device_token'), false)
      assert.equal(Object.hasOwn(call.body, 'code'), false)
      assert.equal(f.home, f.original)
      return null
    }
    assert.equal(call.path, statusPath)
    return { body: { ...receipt(call.body), state: outcome, ...(outcome === 'committed' ? { device_id: device } : {}) } }
  })
  await f.setOriginal()
  assert.equal((await f.open().reconfigure(begin(f.origin))).diagnosis.category, 'REGISTRATION_PENDING')
  const reopened = f.open()
  assert.equal(await reopened.registrationBlocksExecution(), true)
  assert.equal(await reopened.resume(), false)
  assert.equal((await reopened.reconfigure(begin(f.origin, 'ignored-code'))).diagnosis.category, 'REGISTRATION_PENDING')
  assert.equal((await readCleanRegistration(f.metadata)).phase, 'confirming')
  assert.equal(f.home, f.original)
  assert.equal(f.stages, 0)
  outcome = 'committed'
  assert.equal((await reopened.reconfigure({ mode: 'continue_registration', confirmed: true })).status, 'complete')
  assert.equal(JSON.parse(await readFile(path.join(f.home, 'config.json'), 'utf8')).device_id, device)
  assert.deepEqual(f.calls.map(c => c.path), [pairPath, confirmPath, statusPath, statusPath])
  assert.deepEqual(f.diagnostics.map(event => event.event), [
    'runtime-pair-prepare-started', 'runtime-pair-prepare-succeeded', 'runtime-pair-confirm-started', 'runtime-pair-confirm-failed',
    'runtime-pair-status-started', 'runtime-pair-status-prepared', 'runtime-pair-status-started', 'runtime-pair-status-committed',
  ])
})

test('confirm may still commit after its reply breaks, so old execution remains gated', windows, async t => {
  let releaseCommit, finishCommit
  const holdCommit = new Promise(resolve => { releaseCommit = resolve })
  const committed = new Promise(resolve => { finishCommit = resolve })
  let device
  const f = await fixture(t, async call => {
    if (call.path === pairPath) return success(call)
    if (call.path === confirmPath) {
      call.disconnect()
      await holdCommit
      device = randomUUID()
      finishCommit()
      return null
    }
    assert.equal(call.path, statusPath)
    return { body: { ...receipt(call.body), ...(device ? { state: 'committed', device_id: device } : { state: 'prepared' }) } }
  })
  await f.setOriginal()
  f.setRunning()
  const original = f.open()
  try {
    assert.equal((await original.reconfigure(begin(f.origin))).diagnosis.category, 'REGISTRATION_PENDING')
    assert.equal(f.stops, 1)
    assert.equal(f.starts, 0, 'The old Host must not resume while a disconnected request can still commit')
    assert.equal(await original.registrationBlocksExecution(), true, 'Manual start must remain gated')
    const reopened = f.open()
    assert.equal(await reopened.registrationBlocksExecution(), true, 'Startup gate must survive restarting Main')
    assert.equal(await reopened.resume(), false)
    assert.equal(await reopened.registrationBlocksExecution(), true)
    assert.equal(f.starts, 0)
    assert.equal(f.home, f.original)
    releaseCommit()
    await committed
    assert.equal(await reopened.resume(), true)
    assert.equal(await reopened.registrationBlocksExecution(), false)
    assert.notEqual(f.home, f.original)
    assert.equal(JSON.parse(await readFile(path.join(f.home, 'config.json'), 'utf8')).device_id, device)
    assert.equal(f.starts, 1, 'Only the committed replacement starts')
    assert.deepEqual(f.calls.map(call => call.path), [pairPath, confirmPath, statusPath, statusPath])
  } finally { releaseCommit() }
})

test('restart discards a candidate never sent for confirmation, and expiry clears a submitted candidate', windows, async t => {
  const f = await fixture(t, call => {
    if (call.path === pairPath) return success(call)
    if (call.path === confirmPath) return null
    assert.equal(call.path, statusPath)
    return { body: { ...receipt(call.body), state: 'expired' } }
  })
  await f.setOriginal()
  const r = await beginCleanRegistration(f.metadata, { server: f.origin, device_name: 'Prepared before exit' }, [f.original])
  await prepareCleanRegistration(f.metadata, r, 'fixture-code')
  assert.equal((await readCleanRegistration(f.metadata)).phase, 'prepared')
  assert.equal(await f.open().registrationBlocksExecution(), false)
  assert.equal(await f.open().resume(), false)
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.deepEqual(f.calls.map(c => c.path), [pairPath])

  assert.equal((await f.open().reconfigure(begin(f.origin))).diagnosis.category, 'REGISTRATION_PENDING')
  const recovered = f.open()
  assert.equal(await recovered.registrationBlocksExecution(), true)
  assert.equal(await recovered.resume(), false)
  assert.equal((await recovered.registrationStatus()).category, 'PAIRING_PREPARATION_ENDED')
  assert.equal(await recovered.registrationBlocksExecution(), false, 'Expired status must allow manual start after reopening')
  assert.equal(await readCleanRegistration(f.metadata), null)
  assert.equal(f.home, f.original)
  assert.deepEqual(f.calls.map(c => c.path), [pairPath, pairPath, confirmPath, statusPath])
})

test('a terminal rejection resumes the previously running Host unless the user stopped it', windows, async t => {
  for (const userStopped of [false, true]) {
    let terminal = false
    const f = await fixture(t, call => {
      if (call.path === pairPath) return success(call)
      if (call.path === confirmPath) return null
      assert.equal(call.path, statusPath)
      return terminal ? { body: { ...receipt(call.body), state: 'rejected' } }
        : { status: 403, body: { detail: { code: 'PAIRING_PREPARATION_INVALID' } } }
    })
    await f.setOriginal()
    f.setRunning()
    const recovery = f.open()
    assert.equal((await recovery.reconfigure(begin(f.origin))).diagnosis.category, 'REGISTRATION_PENDING')
    assert.equal(await recovery.resume(), false)
    assert.equal(await recovery.registrationBlocksExecution(), true, 'An invalid status proof cannot rule out commit')
    assert.equal(f.starts, 0)
    if (userStopped) recovery.cancelOriginalResume()
    terminal = true
    assert.equal(await recovery.resume(), false)
    assert.equal((await recovery.registrationStatus()).category, 'PAIRING_PREPARATION_ENDED')
    assert.equal(await recovery.registrationBlocksExecution(), false)
    assert.equal(await readCleanRegistration(f.metadata), null)
    assert.equal(f.home, f.original)
    assert.equal(f.starts, userStopped ? 0 : 1)
    assert.deepEqual(f.calls.map(call => call.path), [pairPath, confirmPath, statusPath, statusPath])
  }
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
