import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, readFileSync, chmodSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { RuntimeHost } from '../dist/index.js'
import { acquireHostLease } from '../dist/instanceLease.js'

function fixture(t) {
  const home = mkdtempSync(join(tmpdir(), 'aid-startup-recovery-'))
  const operations = join(home, 'management-operations.json')
  t.after(() => { if (existsSync(operations)) chmodSync(operations, 0o600); rmSync(home, { recursive: true, force: true }) })
  const a = { initialized: 0, prepares: 0, writes: 0, identity: { device_id: 'isolated-device' },
    initialize() { this.initialized++ }, device() { return this.identity },
    async protect(value) { return Buffer.from(value).toString('base64') }, async unprotect(value) { return Buffer.from(value, 'base64').toString() },
    async start() { this.writes++ }, async stop() { this.writes++ }, async pair() { this.writes++ },
    async canReplaceIdentity() { return true }, async preparePluginImport() { this.prepares++; throw new Error('should not prepare') }, async mutatePlugin() { this.writes++ },
  }
  const old = { key: { digest: 'a'.repeat(64), operation: { operation_id: 'known-operation', operation: 'start', status: 'running', code: 0, error: '' } } }
  const bytes = JSON.stringify(old)
  writeFileSync(operations, bytes)
  return { home, operations, a, bytes }
}
const request = (host, method, params = {}) => host.request({ request_id: 'isolated-request', method, params })
const open = (f, overrides = {}) => RuntimeHost.open({ home: f.home, version: 'test', supervisor: 'runtime_app', adapter: f.a, readOnlyOnFailure: true, ...overrides })

test('existing readonly operation file opens normally and running projection never rewrites old bytes', async t => {
  const f = fixture(t); writeFileSync(join(f.home, 'management-secret.bin'), Buffer.from('b'.repeat(64)).toString('base64'))
  chmodSync(f.operations, 0o444)
  const host = await open(f)
  try {
    assert.equal((await request(host, 'describe')).code, 0)
    assert.equal(host.getState().state, 'stopped')
    assert.equal((await request(host, 'operations.get', { operation_id: 'known-operation' })).result.status, 'reconciling')
    assert.equal(readFileSync(f.operations, 'utf8'), f.bytes)
  } finally { await host.dispose() }
})

test('missing secret with valid history gives readonly H1 connection, rejects every mutation before preparation, preserves lease and bytes', async t => {
  const f = fixture(t); const host = await open(f)
  try {
    assert.equal(f.a.initialized, 0)
    assert.equal((await request(host, 'describe')).code, 0)
    assert.equal(host.getState().state, 'blocked')
    assert.match(host.getState().blocked_reason, /管理密钥缺失/)
    assert.equal((await request(host, 'operations.get', { operation_id: 'known-operation' })).result.status, 'reconciling')
    assert.equal(await acquireHostLease(f.home), null)
    const mutations = [['start', { request_key: 'key' }], ['stop', { request_key: 'stop' }],
      ['pair', { request_key: 'pair', server: 'https://isolated.invalid', device_name: 'isolated', pairing_code: 'synthetic' }],
      ['plugins.import', { request_key: 'import', selection_ref: 'unused' }],
      ...['enable', 'disable', 'uninstall'].map(method => ['plugins.' + method, { request_key: method, installation_id: 'unused' }])]
    for (const [method, params] of mutations) assert.equal((await request(host, method, params)).code, 11)
    await assert.rejects(host.runLocalIdentityChange(() => { f.a.writes++ }), /管理密钥缺失/)
    assert.equal(f.a.writes, 0); assert.equal(f.a.prepares, 0)
    assert.equal(readFileSync(f.operations, 'utf8'), f.bytes)
    assert.equal(existsSync(join(f.home, 'management-secret.bin')), false)
  } finally { await host.dispose() }
  const lease = await acquireHostLease(f.home); assert.ok(lease); await lease.release()
})

test('invalid operation store is not replaced by empty history and CLI failure still releases its lease', async t => {
  const f = fixture(t); writeFileSync(f.operations, 'invalid-opaque-record');
  const host = await open(f)
  try {
    assert.equal(host.getState().state, 'blocked')
    assert.equal((await request(host, 'operations.get', { operation_id: 'known-operation' })).code, 11)
    assert.equal(f.a.initialized, 0)
    assert.equal(readFileSync(f.operations, 'utf8'), 'invalid-opaque-record')
  } finally { await host.dispose() }
  await assert.rejects(open(f, { supervisor: 'cli' }), error => error.code === 11)
  const lease = await acquireHostLease(f.home); assert.ok(lease); await lease.release()
})

test('initialization failure and missing prior device identity stay readonly without reset or implicit replay', async t => {
  for (const mode of ['initialize', 'identity', 'identity-running', 'identity-reconciling']) await t.test(mode, async t => {
    const f = fixture(t); writeFileSync(join(f.home, 'management-secret.bin'), Buffer.from('b'.repeat(64)).toString('base64'))
    if (mode === 'initialize') f.a.initialize = () => { f.a.initialized++; throw new Error('arbitrary-private-detail') }
    else { f.a.identity = undefined; const history = JSON.parse(f.bytes); history.key.operation.status = mode === 'identity' ? 'succeeded' : mode === 'identity-running' ? 'running' : 'reconciling'; f.bytes = JSON.stringify(history); writeFileSync(f.operations, f.bytes) }
    const host = await open(f)
    try {
      assert.equal((await request(host, 'describe')).code, 0)
      assert.equal(host.getState().state, 'blocked')
      assert.doesNotMatch(host.getState().blocked_reason, /arbitrary-private-detail/)
      assert.equal((await request(host, 'start', { request_key: 'new' })).code, 11)
      assert.equal((await request(host, 'pair', { request_key: 'new-pair', server: 'https://isolated.invalid', device_name: 'synthetic', pairing_code: 'synthetic' })).code, 11)
      await assert.rejects(host.runLocalIdentityChange(() => { f.a.writes++ }))
      assert.equal(f.a.writes, 0); assert.equal(readFileSync(f.operations, 'utf8'), f.bytes)
    } finally { await host.dispose() }
  })
})

test('malformed optional operation data never crosses the H1 read-only boundary', async t => {
  for (const extra of [{ private_payload: 'synthetic-private-sentinel' }, { message: [] }, { message: '' }, { code: Number.MAX_SAFE_INTEGER + 1 }]) await t.test(JSON.stringify(Object.keys(extra)), async t => {
    const f = fixture(t); const data = JSON.parse(f.bytes); Object.assign(data.key.operation, extra); writeFileSync(f.operations, JSON.stringify(data));
    writeFileSync(join(f.home, 'management-secret.bin'), Buffer.from('b'.repeat(64)).toString('base64'))
    f.a.identity.name = ''
    const host = await open(f)
    try {
      assert.equal(host.getState().state, 'blocked')
      assert.equal(Object.hasOwn(host.getState().device, 'name'), false)
      const reply = await request(host, 'operations.get', { operation_id: 'known-operation' })
      assert.equal(reply.code, 11); assert.doesNotMatch(JSON.stringify(reply), /synthetic-private-sentinel/)
    } finally { await host.dispose() }
  })
})
