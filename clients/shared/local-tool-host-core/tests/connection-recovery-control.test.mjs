import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { RuntimeHost } from '../dist/index.js'

function adapter() {
  return { starts: 0, initialized: 0, pauses: 0, device: () => ({ device_id: 'isolated' }),
    async protect(value) { return Buffer.from(value).toString('base64') },
    async unprotect(value) { return Buffer.from(value, 'base64').toString() },
    async initialize() { this.initialized++ }, async canReplaceIdentity() { return true }, async pair() {},
    async start() { this.starts++ }, async stop() {}, pauseForConnectionRecovery() { this.pauses++ } }
}
const request = (host, method, params = {}) => host.request({ request_id: crypto.randomUUID(), method, params })
test('invalid retired protection fails before identity/plugin initializer and retains bytes', async () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-recovery-control-'))
  const a = adapter(); const file = join(home, 'management-retired-request-keys.bin'); writeFileSync(file, 'bad protected fixture')
  const before = readFileSync(file)
  const host = await RuntimeHost.open({ home, version: 'test', supervisor: 'runtime_app', adapter: a, readOnlyOnFailure: true })
  try { assert.equal(a.initialized, 0); assert.equal(host.getState().state, 'blocked'); assert.deepEqual(readFileSync(file), before); assert.equal((await request(host, 'start', { request_key: 'fresh' })).code, 11) }
  finally { await host.dispose(); rmSync(home, { recursive: true, force: true }) }
})
test('retired request keys are refused before executing a new management intent', async () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-recovery-retired-')); const a = adapter()
  writeFileSync(join(home, 'management-retired-request-keys.bin'), await a.protect(JSON.stringify({ version: 1, keys: [createHash('sha256').update('old-start').digest('hex')] })))
  const host = await RuntimeHost.open({ home, version: 'test', supervisor: 'runtime_app', adapter: a })
  try { assert.equal((await request(host, 'start', { request_key: 'old-start' })).code, 10); assert.equal(a.starts, 0) }
  finally { await host.dispose(); rmSync(home, { recursive: true, force: true }) }
})
test('reboot registration bypasses a hung drain, freezes queued/new starts, and retains ownership until stop completes', async () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-recovery-drain-')); const a = adapter()
  let unblock; let entered
  const drain = new Promise(resolve => { unblock = resolve }); const stopping = new Promise(resolve => { entered = resolve })
  a.stop = async () => { entered(); await drain }
  const host = await RuntimeHost.open({ home, version: 'test', supervisor: 'runtime_app', adapter: a })
  try {
    await request(host, 'start', { request_key: 'initial' }); await new Promise(resolve => setImmediate(resolve))
    await request(host, 'stop', { request_key: 'hung-stop' }); await stopping
    const queued = await request(host, 'start', { request_key: 'queued-start' })
    const registered = await host.inspectConnectionRecovery(async owns => ({ owns }), true, true)
    assert.equal(registered.owns, true); assert.equal(a.pauses, 1)
    assert.equal((await request(host, 'start', { request_key: 'late-start' })).code, 11)
    unblock(); await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve))
    const op = (await request(host, 'operations.get', { operation_id: queued.result.operation_id })).result
    assert.equal(op.status, 'failed'); assert.equal(a.starts, 1)
  } finally { unblock(); await host.dispose(); rmSync(home, { recursive: true, force: true }) }
})
