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
function lifecycleDeferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
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

test('import snapshot cleanup retains in-flight inputs and runs only after a durable terminal outcome', async t => {
  const a = adapter(); const snapshots = new Set(); const cleanup = []; const gate = lifecycleDeferred()
  const snapshot = '11111111-1111-4111-8111-111111111111'
  a.preparePluginImport = async () => { snapshots.add(snapshot); return { snapshot_id: snapshot, sha256: 'b'.repeat(64) } }
  a.mutatePlugin = async () => { await gate.promise; assert.ok(snapshots.has(snapshot), 'Accepted installation must retain its frozen input while running') }
  let home
  a.cleanupPluginImports = retained => {
    cleanup.push([...retained])
    if (!home) return
    const persisted = JSON.parse(readFileSync(join(home, 'management-operations.json'), 'utf8'))
    if (!retained.includes(snapshot) && snapshots.has(snapshot)) {
      assert.equal(persisted.install.operation.status, 'succeeded', 'Persist the outcome before releasing bytes needed to execute it')
      snapshots.delete(snapshot)
    }
  }
  const opened = await open(t, a); home = opened.home
  const accepted = await request(opened.host, 'plugins.import', { request_key: 'install', selection_ref: 'package' })
  assert.ok(snapshots.has(snapshot))
  gate.resolve()
  assert.equal((await done(opened.host, accepted)).status, 'succeeded')
  assert.equal(snapshots.size, 0)
  assert.ok(cleanup.some(kept => kept.includes(snapshot)), 'Cached input cannot be collected just because the caller already received an operation ID')
  const repeated = await request(opened.host, 'plugins.import', { request_key: 'install', selection_ref: 'package' })
  assert.equal(repeated.result.operation_id, accepted.result.operation_id, 'The light operation record still deduplicates after deleting its large ZIP')
})

test('reopen keeps unresolved import snapshots while cleanup failures do not poison an installed host', async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-core-import-cleanup-'))
  writeFileSync(join(home, 'management-secret.bin'), Buffer.from('b'.repeat(64)).toString('base64'))
  const snapshot = '11111111-1111-4111-8111-111111111111'
  writeFileSync(join(home, 'management-operations.json'), JSON.stringify({ old: { digest: 'a'.repeat(64),
    operation: { operation_id: 'old-import', operation: 'import', status: 'running', code: 0, error: '' },
    prepared_import: { snapshot_id: snapshot, sha256: 'b'.repeat(64) } } }))
  const a = adapter(); a.cleanupPluginImports = retained => { assert.deepEqual(retained, [snapshot]); throw new Error('isolated cleanup denied') }
  const { host } = await open(t, a, home)
  assert.equal(host.getState().state, 'stopped')
  assert.equal((await request(host, 'operations.get', { operation_id: 'old-import' })).result.status, 'reconciling')
})

test('passive historical Host dispose releases its OS lease without settling or rewriting old execution facts', async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-core-history-exit-'))
  const marker = JSON.stringify({ instance_id: '8dd360ac-bcda-4d25-9280-b9659e91cd65' })
  writeFileSync(join(home, 'host-running.json'), marker)
  const a = adapter(); const { host } = await open(t, a, home)
  assert.equal(host.getState().state, 'reconciling')
  const stop = await done(host, await request(host, 'stop', { request_key: 'historical-stop' }))
  assert.equal(stop.status, 'reconciling')
  assert.equal(stop.code, 0)
  assert.equal(stop.error, '')
  assert.equal(stop.message, '旧执行尚未确认停止，请停止原Runtime后重试；不会重发旧任务（代码 11）')
  const files = [join(home, 'host-running.json'), join(home, 'management-operations.json')]
  for (const directory of ['journal', 'result-outbox', 'session-tasks']) {
    mkdirSync(join(home, directory)); const file = join(home, directory, 'old-fact.bin'); writeFileSync(file, `untouched historical ${directory}`); files.push(file)
  }
  const before = files.map(file => readFileSync(file))
  await host.dispose()
  assert.equal(a.starts, 0); assert.equal(a.stops, 0)
  for (let i = 0; i < files.length; i++) assert.deepEqual(readFileSync(files[i]), before[i])
  const { acquireHostLease } = await import('../dist/instanceLease.js')
  const lease = await acquireHostLease(home); assert.ok(lease); await lease.release()
  const reopened = await RuntimeHost.open({ home, version: 'test', supervisor: 'cli', adapter: adapter() })
  try {
    const query = { request_id: 'reopened-stop', method: 'operations.get', params: { operation_id: stop.operation_id } }
    const consumer = createManagementConsumer(); consumer.expect(query)
    const response = await reopened.request(query)
    assert.equal(response.code, 0)
    assert.equal(consumer.receive(response).ignored, undefined)
    assert.deepEqual(response.result, stop)
  } finally { await reopened.dispose() }
  assert.equal((await request(host, 'start', { request_key: 'after-close' })).code, 4)
})

test('dispose waits an already accepted start and refuses owned running or reconciling execution until normal stop', async t => {
  const a = adapter(); a.identity = { device_id: 'isolated-device' }
  const started = lifecycleDeferred(); const stopped = lifecycleDeferred(); let ended
  a.start = async (_connection, executionEnded) => { a.starts++; ended = executionEnded; await started.promise }
  a.stop = async () => { a.stops++; await stopped.promise }
  const home = mkdtempSync(join(tmpdir(), 'aid-core-owned-exit-'))
  const host = await RuntimeHost.open({ home, version: 'test', supervisor: 'cli', adapter: a })
  t.after(async () => { started.resolve(); stopped.resolve(); await request(host, 'stop', { request_key: 'cleanup-stop' }); await host.dispose(); rmSync(home, { recursive: true, force: true }) })
  const accepted = await request(host, 'start', { request_key: 'owned-start' })
  let settled = false; const closing = assert.rejects(host.dispose(), /尚未安全停止/).then(() => { settled = true })
  await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false)
  assert.equal((await request(host, 'start', { request_key: 'late-start' })).code, 4)
  const { acquireHostLease } = await import('../dist/instanceLease.js')
  assert.equal(await acquireHostLease(home), null)
  started.resolve(); await closing; assert.equal((await done(host, accepted)).status, 'succeeded'); assert.equal(a.starts, 1)
  ended(); assert.equal(host.getState().state, 'reconciling'); await assert.rejects(host.dispose(), /尚未安全停止/)
  assert.ok(readFileSync(join(home, 'host-running.json')).length)
  const stop = await request(host, 'stop', { request_key: 'owned-stop' })
  const final = host.dispose(); await new Promise(resolve => setImmediate(resolve)); assert.equal(await acquireHostLease(home), null)
  stopped.resolve(); await final; assert.equal(a.stops, 1)
  const lease = await acquireHostLease(home); assert.ok(lease); await lease.release()
  assert.equal(JSON.parse(readFileSync(join(home, 'management-operations.json'), 'utf8'))['owned-stop'].operation.operation_id, stop.result.operation_id)
})

test('dispose closes admission while accepted plugin snapshot preparation drains without a late mutation', async t => {
  const a = adapter(); const prepared = lifecycleDeferred(); let mutated = 0
  a.preparePluginImport = () => prepared.promise
  a.mutatePlugin = async () => { mutated++ }
  const home = mkdtempSync(join(tmpdir(), 'aid-core-preparing-exit-'))
  const host = await RuntimeHost.open({ home, version: 'test', supervisor: 'cli', adapter: a })
  t.after(async () => { prepared.resolve({ snapshot_id: '00000000-0000-0000-0000-000000000000', sha256: 'a'.repeat(64) }); await request(host, 'stop', { request_key: 'cleanup-stop' }); await host.dispose(); rmSync(home, { recursive: true, force: true }) })
  const imported = request(host, 'plugins.import', { request_key: 'preparing', selection_ref: 'isolated-ref' })
  let settled = false; const closed = host.dispose().then(() => { settled = true })
  await new Promise(resolve => setImmediate(resolve)); assert.equal(settled, false)
  const { acquireHostLease } = await import('../dist/instanceLease.js'); assert.equal(await acquireHostLease(home), null)
  prepared.resolve({ snapshot_id: '00000000-0000-0000-0000-000000000000', sha256: 'a'.repeat(64) })
  assert.equal((await imported).code, 4); await closed; assert.equal(mutated, 0)
  const lease = await acquireHostLease(home); assert.ok(lease); await lease.release()
})

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

test('plugin list reads the installed snapshot without running a new readiness probe', async t => {
  const a = adapter(); let changed
  a.observePlugins = fn => { changed = fn }
  a.refreshPlugins = async () => { assert.fail('Listing installed tools must not run package or login diagnostics') }
  a.plugins = () => [{ installation_id: 'i', plugin_id: 'ai.aidwork.weixin', display_name: '微信', release_id: 'r', enabled: true, ready: false }]
  const { host } = await open(t, a)
  const before = (await request(host, 'getState')).result.revision
  const list = (await request(host, 'plugins.list')).result
  assert.equal(list.revision, before)
  assert.equal(list.plugins[0].ready, false, 'An offline app remains installed and visible')
  changed()
  assert.ok((await request(host, 'plugins.list')).result.revision > before)
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

test('an adapter refusal preserves the existing installation after the normal drain', async t => {
  const a = adapter(); a.identity = { device_id: 'isolated-device' }; let mutations = 0
  a.canChangePlugins = async () => false
  a.mutatePlugin = async () => { mutations++ }
  const { host } = await open(t, a)
  await done(host, await request(host, 'start', { request_key: 'old-running' }))
  const result = await done(host, await request(host, 'plugins.disable', { request_key: 'blocked-switch', installation_id: 'old' }))
  assert.equal(result.status, 'failed'); assert.equal(result.code, 11)
  assert.equal(mutations, 0, 'An adapter may still reject a switch after draining')
  assert.equal(a.starts, 2, 'Rejected change resumes the existing active release')
  assert.equal((await request(host, 'getState')).result.state, 'running')
})

test('list does not wait for diagnostics and publishes later observed readiness at its revision', async t => {
  const a = adapter(); let changed; let ready = false
  a.observePlugins = callback => { changed = callback }
  a.refreshPlugins = async () => { assert.fail('List is a cached query, not a diagnostics command') }
  a.plugins = () => [{ installation_id: 'i', plugin_id: 'ai.aidwork.weixin', display_name: '微信', release_id: 'r', enabled: true, ready }]
  const { host } = await open(t, a)
  const before = (await request(host, 'plugins.list')).result
  assert.equal(before.plugins[0].ready, false)
  ready = true; changed()
  const list = (await request(host, 'plugins.list')).result
  assert.equal(list.plugins[0].ready, true); assert.equal(list.revision, (await request(host, 'getState')).result.revision)
  assert.ok(list.revision > before.revision)
})

test('historical execution can be checked again before plugin maintenance without replaying its operation', async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-core-maintenance-history-'))
  const marker = JSON.stringify({ instance_id: '8dd360ac-bcda-4d25-9280-b9659e91cd65' })
  writeFileSync(join(home, 'host-running.json'), marker)
  mkdirSync(join(home, 'journal'))
  const oldFact = join(home, 'journal', 'old.jsonl'); writeFileSync(oldFact, 'original unknown send')
  const a = adapter(); let stopped = false, switches = 0, checks = 0
  a.recoverPreviousExecution = async value => { assert.equal(value, marker); checks++; return stopped }
  a.mutatePlugin = async () => { switches++ }
  const { host } = await open(t, a, home)
  const first = await done(host, await request(host, 'plugins.disable', { request_key: 'blocked-by-live-execution', installation_id: 'i' }))
  assert.equal(first.status, 'reconciling'); assert.equal(switches, 0)
  assert.equal(readFileSync(join(home, 'host-running.json'), 'utf8'), marker)
  stopped = true
  assert.equal((await done(host, await request(host, 'plugins.disable', { request_key: 'retry-after-stop', installation_id: 'i' }))).status, 'succeeded')
  assert.equal(host.getState().state, 'stopped'); assert.equal(switches, 1)
  assert.ok(checks >= 3, 'Retry must obtain fresh process evidence, not treat the absence of ownership as proof')
  assert.equal((await request(host, 'plugins.disable', { request_key: 'blocked-by-live-execution', installation_id: 'i' })).result.operation_id, first.operation_id)
  assert.equal(switches, 1, 'A retry of the old operation only returns its original outcome')
  assert.equal(a.stops, 0, 'A newly opened Host does not claim ownership of old execution')
  assert.equal(readFileSync(oldFact, 'utf8'), 'original unknown send')
})

test('plugin persistence failures remain local and permit an explicit repair attempt', async t => {
  const a = adapter(); let attempts = 0
  a.mutatePlugin = async () => { if (++attempts === 1) throw new ManagementError(11, '插件目录无法写入，请检查目录权限后重新安装') }
  const { host } = await open(t, a)
  const failed = await done(host, await request(host, 'plugins.enable', { request_key: 'failed', installation_id: 'i' }))
  assert.equal(failed.status, 'failed'); assert.equal(failed.code, 11)
  assert.match(failed.error, /目录权限/)
  assert.equal(host.getState().state, 'stopped', 'An installation failure does not imply a running business process')
  assert.equal((await done(host, await request(host, 'plugins.enable', { request_key: 'repair', installation_id: 'i' }))).status, 'succeeded')
  assert.equal(attempts, 2)
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
