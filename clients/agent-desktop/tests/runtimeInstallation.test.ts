import test from 'node:test'
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { prepareRuntimeInstallation, rotateRuntimeConnection, selectRuntimeConnection } from '../electron/runtimeInstallation.js'

function fixture() {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-installation-'))
  const legacyHome = path.join(root, 'legacy'), userDataPath = path.join(root, 'user'), hostEntry = path.join(root, 'installed/resources/runtime/host/managed-entry.js')
  mkdirSync(legacyHome); mkdirSync(path.dirname(hostEntry), { recursive: true }); writeFileSync(hostEntry, 'trusted fixture')
  let stops = 0
  const protect = async (v: string) => Buffer.from(v).toString('base64')
  const unprotect = async (v: string) => { if (v === 'corrupt') throw new Error('DPAPI invalid'); return Buffer.from(v, 'base64').toString() }
  const config = { server: 'https://enterprise.example/', device_id: randomUUID(), name: 'test' }
  const options = { legacyHome, userDataPath, hostEntry, appId: 'cn.aidingyi.agent.runtime.acceptance', node: process.execPath,
    stopLegacy: async () => { stops++ }, protect, unprotect, verifyIdentity: async () => true, migratePlugins: async () => {} }
  return { root, options, config, protect, get stops() { return stops }, clean() { rmSync(root, { recursive: true, force: true }) } }
}
test('valid credentials migrate once, while ten types of legacy state remain isolated', async () => {
  const f = fixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify({ ...f.config, providers: { unknown: { entry: 'untrusted' } }, sessionTasks: true }))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    for (const name of ['management-secret.bin', 'management-operations.json', 'management-retired-request-keys.bin', 'host-running.json', 'journal', 'outbox', 'session-tasks', 'process-supervision', 'pairing-pending.bin', 'invocation-fences']) writeFileSync(path.join(f.options.legacyHome, name), 'corrupt')
    const installed = await prepareRuntimeInstallation(f.options)
    assert.equal(installed.migrated, true)
    assert.equal(installed.fresh, true)
    assert.deepEqual(readdirSync(installed.home).sort(), ['config.json','credentials.bin'])
    assert.deepEqual(JSON.parse(readFileSync(path.join(installed.home, 'config.json'), 'utf8')), f.config)
    assert.equal(f.stops, 1)
    const reopened = await prepareRuntimeInstallation(f.options)
    assert.equal(reopened.home, installed.home)
    assert.equal(reopened.fresh, false)
    assert.equal(f.stops, 1)
  } finally { f.clean() }
})
test('broken config or DPAPI and authentication rejection produce a clean usable home', async () => {
  for (const fault of ['config','token','decrypted_bad_token','revoked']) {
    const f = fixture()
    try {
      writeFileSync(path.join(f.options.legacyHome, 'config.json'), fault === 'config' ? '{corrupt' : JSON.stringify(f.config))
      writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), fault === 'token' ? 'corrupt' : await f.protect(fault === 'decrypted_bad_token' ? 'bad\nheader' : 'a'.repeat(64)))
      f.options.verifyIdentity = async () => fault !== 'revoked'
      const installed = await prepareRuntimeInstallation(f.options)
      assert.equal(installed.migrated, false)
      assert.deepEqual(readdirSync(installed.home), [])
      assert.equal(existsSync(f.options.legacyHome), false)
      if (fault === 'revoked') assert.equal(existsSync(path.join(installed.metadataHome, 'previous-identity.bin')), true)
    } finally { f.clean() }
  }
})
test('previous paired registration credentials win without activating old resource recovery', async () => {
  const f = fixture()
  try {
    const staging = f.options.legacyHome + '.connection-registration'; mkdirSync(staging)
    writeFileSync(path.join(staging, 'state.bin'), await f.protect(JSON.stringify({ version: 1, home: f.options.legacyHome, phase: 'paired', config: f.config, token: 'b'.repeat(64), replacement_pending: true })))
    const installed = await prepareRuntimeInstallation(f.options)
    assert.equal(installed.migrated, true)
    assert.deepEqual(JSON.parse(readFileSync(path.join(installed.home, 'config.json'), 'utf8')), f.config)
    assert.equal(existsSync(path.join(installed.metadataHome, 'legacy-replacement-pending.json')), true)
  } finally { f.clean() }
})
test('bad staged token falls back to the valid old identity without marking replacement pending', async () => {
  const f = fixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    const staging = f.options.legacyHome + '.connection-registration'; mkdirSync(staging)
    writeFileSync(path.join(staging, 'state.bin'), await f.protect(JSON.stringify({ version: 1, home: f.options.legacyHome, phase: 'paired', config: { ...f.config, device_id: randomUUID() }, token: 'bad-token', replacement_pending: true })))
    const installed = await prepareRuntimeInstallation(f.options)
    assert.equal(installed.migrated, true)
    assert.deepEqual(JSON.parse(readFileSync(path.join(installed.home, 'config.json'), 'utf8')), f.config)
    assert.equal(existsSync(path.join(installed.metadataHome, 'legacy-replacement-pending.json')), false)
  } finally { f.clean() }
})
test('new pair rotates connection, retains stable pending transaction and rejects foreign homes', async () => {
  const f = fixture()
  try {
    const installed = await prepareRuntimeInstallation(f.options)
    writeFileSync(path.join(installed.metadataHome, 'clean-registration.bin'), 'protected unknown request')
    writeFileSync(path.join(installed.home, 'journal'), 'corrupt')
    const home = await rotateRuntimeConnection(f.options, installed.home)
    assert.notEqual(home, installed.home); assert.deepEqual(readdirSync(home), [])
    assert.equal(readFileSync(path.join(installed.metadataHome, 'clean-registration.bin'), 'utf8'), 'protected unknown request')
    assert.equal(await selectRuntimeConnection(f.options, home), home)
    await assert.rejects(selectRuntimeConnection(f.options, f.options.legacyHome), /登记无效/)
    await assert.rejects(rotateRuntimeConnection(f.options, installed.home), /另一个客户端/)
  } finally { f.clean() }
})
test('installer generation changes invalidate previously registered executable tickets', async () => {
  const f = fixture()
  try {
    const first = await prepareRuntimeInstallation(f.options)
    const secondId = randomUUID(), secondHome = path.join(first.metadataHome, 'generations', secondId, 'connections', randomUUID())
    mkdirSync(secondHome, { recursive: true })
    writeFileSync(path.join(first.metadataHome, 'current.json'), JSON.stringify({ version: 2, id: secondId, home: secondHome, priorHome: first.home, initialized: false }))
    await assert.rejects(prepareRuntimeInstallation(f.options), /已被新版替代/)
    writeFileSync(path.resolve(path.dirname(f.options.hostEntry), '../../../runtime-installation.id'), secondId)
    const second = await prepareRuntimeInstallation(f.options)
    assert.equal(second.home, secondHome); assert.equal(f.stops, 2)
    await assert.rejects(selectRuntimeConnection(f.options, first.home), /登记无效/)
  } finally { f.clean() }
})
test('corrupt readable pointer is rebuilt using the trusted installation id and protected previous identity', async () => {
  const f = fixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    const first = await prepareRuntimeInstallation(f.options)
    writeFileSync(path.resolve(path.dirname(f.options.hostEntry), '../../../runtime-installation.id'), first.installationId)
    writeFileSync(path.join(first.metadataHome, 'current.json'), 'null')
    const rebuilt = await prepareRuntimeInstallation(f.options)
    assert.notEqual(rebuilt.home, first.home)
    assert.equal(rebuilt.installationId, first.installationId)
    assert.equal(rebuilt.migrated, true)
    assert.deepEqual(JSON.parse(readFileSync(path.join(rebuilt.home, 'config.json'), 'utf8')), f.config)
  } finally { f.clean() }
})
