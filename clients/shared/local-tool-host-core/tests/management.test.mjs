import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { test } from 'node:test'
import { RuntimeHost, ManagementError } from '../dist/index.js'
import { createManagementConsumer } from '../../../../contracts/runtime-host/v1/fixtures/consumer-fake.mjs'

// Isolated test protection; production composition always supplies CurrentUser DPAPI.
function adapter() {
  return {
    identity: undefined, pairs: 0, starts: 0, stops: 0, initialized: 0,
    initialize() { this.initialized++ },
    device() { return this.identity },
    async protect(value) { return Buffer.from(value).toString('base64') },
    async unprotect(value) { return Buffer.from(value, 'base64').toString() },
    async canReplaceIdentity() { return true },
    async pair() { this.pairs++; this.identity = { device_id: 'isolated-device' } },
    async start(connection) { this.starts++; connection('online') },
    async stop() { this.stops++ },
  }
}
async function open(t, a = adapter(), home = mkdtempSync(join(tmpdir(), 'aid-core-test-'))) {
  const host = await RuntimeHost.open({ home, version: 'test', supervisor: 'cli', adapter: a })
  t.after(async () => {
    await request(host, 'stop', { request_key: 'cleanup-stop' })
    await host.dispose()
    rmSync(home, { recursive: true, force: true })
  })
  return { host, a, home }
}
let sequence = 0
function request(host, method, params = {}) { return host.request({ request_id: `r-${++sequence}`, method, params }) }
async function done(host, response) {
  assert.equal(response.code, 0)
  let op = response.result
  for (let i = 0; op.status === 'running' && i < 200; i++) {
    await new Promise(resolve => setTimeout(resolve, 2))
    op = (await request(host, 'operations.get', { operation_id: op.operation_id })).result
  }
  assert.notEqual(op.status, 'running', 'management operation must settle')
  return op
}

test('compiled empty Host shares instance/revision across actual consumer snapshots', async t => {
  const { host } = await open(t)
  const consumer = createManagementConsumer()
  for (const method of ['describe', 'getState', 'plugins.list']) {
    const req = { request_id: method, method, params: {} }
    consumer.expect(req)
    const reply = await host.request(req)
    assert.equal(consumer.receive(reply).ignored, undefined)
  }
  assert.equal(consumer.snapshot.state, 'stopped')
  assert.equal(consumer.snapshot.connection, 'unpaired')
  assert.deepEqual(consumer.pluginsSnapshot.plugins, [])
  assert.equal(consumer.snapshot.instance_id, consumer.pluginsSnapshot.instance_id)
  assert.equal((await done(host, await request(host, 'start', { request_key: 'unpaired-start' }))).code, 5)
})

test('managed plugin intent drains accepted execution, mutates once and preserves H1 dedup across retries', async t => {
  const a = adapter(); a.identity = { device_id: 'isolated-device' }
  let releaseDrain; const drained = new Promise(resolve => { releaseDrain = resolve })
  const transitions = []; let imported = 0
  a.stop = async () => { transitions.push('drain'); await drained; transitions.push('stopped') }
  a.mutatePlugin = async (method, params, context) => { imported++; transitions.push('mutate'); assert.equal(method, 'plugins.import'); assert.equal(params.selection_ref, 'opaque-ref'); assert.ok(context.instance_id) }
  a.preparePluginImport = async () => ({ snapshot_id: '00000000-0000-0000-0000-000000000000', sha256: 'a'.repeat(64) })
  const { host } = await open(t, a)
  assert.ok((await request(host, 'describe')).result.features.includes('first_party_plugins'))
  await done(host, await request(host, 'start', { request_key: 'start' }))
  const accepted = await request(host, 'plugins.import', { request_key: 'import-key', selection_ref: 'opaque-ref' })
  const repeated = await request(host, 'plugins.import', { request_key: 'import-key', selection_ref: 'opaque-ref' })
  assert.equal(repeated.result.operation_id, accepted.result.operation_id)
  await new Promise(resolve => setTimeout(resolve, 5))
  assert.equal(imported, 0, 'Active release cannot change before every accepted lane drains')
  assert.equal((await request(host, 'getState')).result.state, 'draining')
  releaseDrain()
  assert.equal((await done(host, accepted)).status, 'succeeded')
  assert.equal(imported, 1); assert.deepEqual(transitions, ['drain', 'stopped', 'mutate'])
  assert.equal((await request(host, 'getState')).result.state, 'running')
  assert.equal(accepted.result.operation, 'import')
})

test('async plugin readiness facts advance the shared revision before list visibility', async t => {
  const a = adapter(); let changed
  a.observePlugins = fn => { changed = fn }
  a.refreshPlugins = async () => { changed() }
  a.plugins = () => [{ installation_id: 'i', plugin_id: 'ai.aidwork.weixin', display_name: '微信', release_id: 'r', enabled: true, ready: false }]
  const { host } = await open(t, a)
  const before = (await request(host, 'getState')).result.revision
  const list = (await request(host, 'plugins.list')).result
  assert.ok(list.revision > before); assert.equal((await request(host, 'getState')).result.revision, list.revision)
})

test('import acceptance freezes one snapshot before durable operation and concurrent retries do not consume again', async t => {
  const a = adapter(); let release; let preparations = 0; let mutations = 0
  const snapshot = new Promise(resolve => { release = resolve })
  a.preparePluginImport = async () => { preparations++; return snapshot }
  a.mutatePlugin = async (_method, _params, context) => { mutations++; assert.equal(context.prepared_import.sha256, 'b'.repeat(64)) }
  const { host, home } = await open(t, a)
  const input = { request_key: 'frozen-import', selection_ref: 'original-ref' }
  const first = request(host, 'plugins.import', input); const retry = request(host, 'plugins.import', input)
  await new Promise(resolve => setTimeout(resolve, 5))
  assert.equal(preparations, 1); assert.equal(mutations, 0)
  assert.equal((await request(host, 'plugins.import', { ...input, selection_ref: 'different' })).code, 10)
  release({ snapshot_id: '00000000-0000-0000-0000-000000000000', sha256: 'b'.repeat(64) })
  const accepted = await first; assert.equal((await retry).result.operation_id, accepted.result.operation_id)
  const persisted = JSON.parse(readFileSync(join(home, 'management-operations.json'), 'utf8'))['frozen-import']
  assert.equal(persisted.prepared_import.sha256, 'b'.repeat(64))
  assert.equal((await done(host, accepted)).status, 'succeeded'); assert.equal(mutations, 1)
})

test('unfinished old session work prevents plugin changes and preserves the original running release', async t => {
  const a = adapter(); a.identity = { device_id: 'isolated-device' }; let mutations = 0
  a.canChangePlugins = async () => false
  a.mutatePlugin = async () => { mutations++ }
  const { host } = await open(t, a)
  await done(host, await request(host, 'start', { request_key: 'old-running' }))
  const result = await done(host, await request(host, 'plugins.disable', { request_key: 'blocked-switch', installation_id: 'old' }))
  assert.equal(result.status, 'failed'); assert.equal(result.code, 11)
  assert.equal(mutations, 0, 'A stopped Provider process does not terminate its assigned session task')
  assert.equal(a.starts, 2, 'Rejected change resumes the existing active release')
  assert.equal((await request(host, 'getState')).result.state, 'running')
})

test('list reads fresh synchronous facts after asynchronous diagnostics, with the same revision', async t => {
  const a = adapter(); let release; const pending = new Promise(resolve => { release = resolve }); let changed; let ready = false
  a.observePlugins = callback => { changed = callback }
  a.refreshPlugins = async () => { await pending }
  a.plugins = () => [{ installation_id: 'i', plugin_id: 'ai.aidwork.weixin', display_name: '微信', release_id: 'r', enabled: true, ready }]
  const { host } = await open(t, a)
  const query = request(host, 'plugins.list'); ready = true; changed(); release()
  const list = (await query).result
  assert.equal(list.plugins[0].ready, true); assert.equal(list.revision, (await request(host, 'getState')).result.revision)
})

test('same normalized home is exclusive before initialization or credential access', async t => {
  const { home } = await open(t)
  const second = adapter()
  await assert.rejects(RuntimeHost.open({ home: resolve(home, '.'), version: 'test', supervisor: 'runtime_app', adapter: second }),
    error => error instanceof ManagementError && error.code === 4)
  assert.equal(second.initialized, 0)
})

test('pair intent dedup survives restart without persisting sensitive input', async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-core-restart-'))
  const a = adapter()
  let host = await RuntimeHost.open({ home, version: 'test', supervisor: 'cli', adapter: a })
  const params = { request_key: 'persistent-pair', server: 'https://isolated.invalid', device_name: 'office', pairing_code: 'SENSITIVE-PAIR-CODE' }
  const original = await done(host, await request(host, 'pair', params))
  await host.dispose()
  host = await RuntimeHost.open({ home, version: 'test', supervisor: 'runtime_app', adapter: a })
  t.after(async () => { await host.dispose(); rmSync(home, { recursive: true, force: true }) })
  const again = await request(host, 'pair', params)
  assert.equal(again.result.operation_id, original.operation_id)
  assert.equal(a.pairs, 1, 'retry must not consume a second pairing code')
  assert.equal((await request(host, 'pair', { ...params, device_name: 'different' })).code, 10)
  const disk = readFileSync(join(home, 'management-operations.json'), 'utf8')
  for (const input of [params.pairing_code, params.server, params.device_name]) assert.ok(!disk.includes(input))
})

test('stop remains draining and holds lease until accepted work ends', async t => {
  const a = adapter(); a.identity = { device_id: 'isolated-device' }
  let release
  a.stop = () => new Promise(resolve => { release = resolve })
  const { host, home } = await open(t, a)
  await done(host, await request(host, 'start', { request_key: 'start' }))
  const stop = await request(host, 'stop', { request_key: 'stop' })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(host.getState().state, 'draining')
  await assert.rejects(RuntimeHost.open({ home, version: 'test', supervisor: 'cli', adapter: adapter() }), e => e.code === 4)
  const pendingDispose = host.dispose()
  assert.equal((await request(host, 'pair', { request_key: 'late', server: 'https://isolated.invalid', device_name: 'office', pairing_code: 'CODE' })).code, 4)
  release()
  await pendingDispose
  assert.equal(host.getState().state, 'stopped')
  assert.equal(stop.result.status, 'running')
})

test('identity replacement is refused while running and with retained old evidence', async t => {
  const a = adapter(); a.identity = { device_id: 'old-device' }
  const { host } = await open(t, a)
  const params = { request_key: 'replace-running', server: 'https://isolated.invalid', device_name: 'office', pairing_code: 'CODE' }
  await done(host, await request(host, 'start', { request_key: 'start' }))
  assert.equal((await done(host, await request(host, 'pair', params))).code, 11)
  assert.equal(a.pairs, 0)
  await done(host, await request(host, 'stop', { request_key: 'stop' }))
  a.canReplaceIdentity = async () => false
  await assert.rejects(host.runLocalIdentityChange(() => { a.identity = undefined }), e => e.code === 11)
  assert.equal((await done(host, await request(host, 'pair', { ...params, request_key: 'replace-pending-ack' }))).code, 11)
  assert.equal(a.identity.device_id, 'old-device')
})

test('schema shape rejects extra paths/prototype method and unsupported imports do nothing', async t => {
  const { host, a } = await open(t)
  for (const bad of [
    { method: 'start', params: { request_key: 'x', executable: '/arbitrary' } },
    { method: 'constructor', params: {} },
    { method: 'plugins.import', params: { request_key: 'x', path: '/arbitrary' } },
  ]) assert.equal((await host.request({ request_id: 'bad', ...bad })).code, 1)
  assert.equal((await request(host, 'plugins.import', { request_key: 'no-import', selection_ref: 'opaque' })).code, 3)
  assert.equal(a.starts, 0); assert.equal(a.pairs, 0)
})

test('missing stable secret does not silently regenerate dedup identity', async () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-core-missing-secret-'))
  writeFileSync(join(home, 'management-operations.json'), '{}')
  try {
    await assert.rejects(RuntimeHost.open({ home, version: 'test', supervisor: 'cli', adapter: adapter() }), e => e.code === 11)
    assert.equal(readFileSync(join(home, 'management-operations.json'), 'utf8'), '{}')
  } finally { rmSync(home, { recursive: true, force: true }) }
})

test('failed durable acceptance cannot masquerade as a successful deduplicated request', async t => {
  const { host, home, a } = await open(t)
  mkdirSync(join(home, 'management-operations.json'))
  const params = { request_key: 'disk-failure' }
  for (let i = 0; i < 2; i++) assert.equal((await request(host, 'start', params)).code, 11)
  assert.equal(a.starts, 0)
  rmSync(join(home, 'management-operations.json'), { recursive: true })
})

test('observer exceptions cannot interrupt execution or leak pairing fields', async t => {
  const { host } = await open(t)
  const events = []
  host.observe(() => { throw new Error('test observer') })
  host.observe(event => events.push(event))
  await done(host, await request(host, 'pair', { request_key: 'pair', server: 'https://isolated.invalid', device_name: 'office', pairing_code: 'CODE' }))
  assert.ok(events.length > 1)
  assert.ok(events.every(event => Object.keys(event).sort().join(',') === 'event,instance_id,revision'))
  assert.ok(events.every((event, i) => i === 0 || event.revision >= events[i - 1].revision))
})
