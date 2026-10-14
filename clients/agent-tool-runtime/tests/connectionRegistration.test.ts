import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync, mkdirSync, rmSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { acquireHostLease } from '@aid/local-tool-host-core'
import { dpapiUnprotect, dpapiProtect } from '../src/dpapi.js'
import { withConnectionRegistration, registerConnection, registrationStatus, rememberRegistrationAction,
  registrationBlocksExecution, verifyRegisteredConnection, installRegisteredConnection, finishRegisteredConnection } from '../src/connectionRegistration.js'
import { diagnosis, identityDigest } from '../src/connectionDiagnosis.js'

const windows = { skip: process.platform !== 'win32' }
function fixture(t: test.TestContext) {
  const root = mkdtempSync(join(tmpdir(), 'aid-registration-test-')); const home = join(root, 'runtime'); mkdirSync(home)
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const device = randomUUID(); const secret = randomUUID(); const calls: string[] = []
  let pairFailure: 'lost' | 'invalid' | null = null; let cleanupFailure = false; let revoked = false; let cleanupCode = ''
  t.mock.method(globalThis, 'fetch', async (url: string, options: RequestInit) => {
    calls.push(url); assert.equal(options.redirect, 'error')
    if (url.endsWith('/pair')) {
      assert.equal(JSON.parse(String(options.body)).pairing_protocol, 2)
      if (pairFailure === 'lost') throw new Error('fixture response lost')
      if (pairFailure === 'invalid') return new Response(JSON.stringify({ detail: { error: 'invalid' } }), { status: 400 })
      revoked = false
      return Response.json({ success: true, device_id: device, device_token: secret, replacement_pending: true })
    }
    assert.equal((options.headers as Record<string, string>).Authorization, `Bearer ${secret}`)
    if (url.endsWith('/complete')) {
      if (cleanupCode) return new Response(JSON.stringify({ detail: { code: cleanupCode } }), { status: 409 })
      if (cleanupFailure) return new Response('{}', { status: 503 })
      assert.deepEqual(JSON.parse(String(options.body)), {})
      return Response.json({ success: true, replacement_completed: true })
    }
    assert.ok(url.endsWith('/heartbeat'))
    if (revoked) return new Response('{}', { status: 401 })
    return Response.json({ success: true, selected: false })
  })
  const input = { server: 'https://registration.example', device_name: '注册测试设备', pairing_code: 'fixture-code' }
  const register = () => withConnectionRegistration(home, () => registerConnection(home, input))
  return { root, home, input, calls, secret, device, register, pairFailure: (v: typeof pairFailure) => { pairFailure = v }, cleanupFailure: (v: boolean) => { cleanupFailure = v }, revoked: (v: boolean) => { revoked = v }, cleanupCode: (v: string) => { cleanupCode = v } }
}

test('mixed old history does not block HTTP pairing; protected pending step survives diagnosis and repeat input', windows, async t => {
  const f = fixture(t)
  const history = JSON.stringify(Array.from({ length: 18 }, (_, i) => ({ operation: i < 2 ? 'start' : 'stop', status: 'reconciling' })))
  writeFileSync(join(f.home, 'management-operations.json'), history)
  const first = await f.register()
  assert.equal(first.category, 'REGISTRATION_PENDING'); assert.equal(first.service_verified, true)
  assert.equal(readFileSync(join(f.home, 'management-operations.json'), 'utf8'), history)
  assert.equal(readdirSync(f.home).length, 1); assert.equal(await registrationBlocksExecution(f.home), true)
  const protectedBytes = readFileSync(join(f.home + '.connection-registration', 'state.bin'), 'utf8')
  assert.ok(!protectedBytes.includes(f.secret)); assert.ok(!protectedBytes.includes(f.input.pairing_code))
  const staged = JSON.parse(await dpapiUnprotect(protectedBytes)); assert.equal(staged.token, f.secret)
  await withConnectionRegistration(f.home, () => rememberRegistrationAction(f.home, diagnosis('CONNECTION_RESOURCE_REBOOT_PREPARED', '需重启', ['正常重启Windows后继续。'], { requires_reboot: true })))
  assert.equal((await registrationStatus(f.home))?.requires_reboot, true)
  assert.equal((await verifyRegisteredConnection(f.home)).requires_reboot, true)
  await f.register()
  assert.equal(f.calls.filter(s => s.endsWith('/pair')).length, 1)
  assert.equal(f.calls.filter(s => s.endsWith('/complete')).length, 0)
  assert.equal(readFileSync(join(f.home, 'management-operations.json'), 'utf8'), history)
})

test('unknown pair response never resends by status or verification; only explicit new code supersedes retained transaction', windows, async t => {
  const f = fixture(t); f.pairFailure('lost')
  await f.register()
  const old = readFileSync(join(f.home + '.connection-registration', 'state.bin'), 'utf8')
  const transaction = JSON.parse(await dpapiUnprotect(old))
  assert.equal(transaction.phase, 'requested'); assert.equal(transaction.token, null)
  assert.ok(!JSON.stringify(transaction).includes(f.input.pairing_code))
  await registrationStatus(f.home); await verifyRegisteredConnection(f.home)
  assert.equal(f.calls.length, 1)
  f.pairFailure(null); await f.register()
  assert.equal(f.calls.filter(s => s.endsWith('/pair')).length, 2)
  assert.equal(readFileSync(join(f.home + '.connection-registration', `${transaction.id}.unconfirmed.bin`), 'utf8'), old)
})

test('registration OS lease prevents parallel pairing before ticket consumption', windows, async t => {
  const f = fixture(t); mkdirSync(f.home + '.connection-registration')
  const lease = await acquireHostLease(f.home + '.connection-registration'); assert.ok(lease)
  try { await assert.rejects(f.register(), /另一客户端/); assert.equal(f.calls.length, 0) }
  finally { await lease.release() }
})

for (const code of ['REPLACEMENT_ALREADY_COMPLETED', 'REPLACEMENT_TARGET_NOT_FOUND']) {
  test(`terminal replacement ${code} preserves new connection and avoids generic endless retry`, windows, async t => {
    const f = fixture(t); const id = randomUUID()
    const config = { server: f.input.server, device_id: f.device, name: f.input.device_name }
    writeFileSync(join(f.home, 'config.json'), JSON.stringify(config))
    writeFileSync(join(f.home, 'credentials.bin'), await dpapiProtect(f.secret))
    mkdirSync(f.home + '.connection-registration')
    writeFileSync(join(f.home + '.connection-registration', 'state.bin'), await dpapiProtect(JSON.stringify({ version: 1, home: f.home,
      id, archive_id: id, phase: 'active', config, token: f.secret, replacement_pending: true, installed_digest: identityDigest(f.home), action: null })))
    f.cleanupCode(code)
    const result = await withConnectionRegistration(f.home, () => finishRegisteredConnection(f.home))
    assert.equal(result.service_verified, true)
    assert.notEqual(result.category, 'REGISTRATION_CLEANUP_PENDING')
    assert.equal(await registrationBlocksExecution(f.home), false)
    if (code === 'REPLACEMENT_ALREADY_COMPLETED') assert.equal(await registrationStatus(f.home), null)
    else assert.equal((await registrationStatus(f.home))?.category, 'REPLACEMENT_TARGET_UNAVAILABLE')
    assert.equal(f.calls.filter(s => s.endsWith('/pair')).length, 0)
    assert.equal(JSON.parse(readFileSync(join(f.home, 'config.json'), 'utf8')).device_id, f.device)
  })
}

test('revoked interrupted activation retains archive lineage through both lost response and another revoked paired identity', windows, async t => {
  const f = fixture(t); await f.register()
  const file = join(f.home + '.connection-registration', 'state.bin')
  const original = JSON.parse(await dpapiUnprotect(readFileSync(file, 'utf8')))
  original.phase = 'activating'; writeFileSync(file, await dpapiProtect(JSON.stringify(original)))
  f.revoked(true); f.pairFailure('lost'); await f.register()
  const requested = JSON.parse(await dpapiUnprotect(readFileSync(file, 'utf8')))
  assert.equal(requested.phase, 'requested'); assert.equal(requested.archive_id, original.id)
  f.pairFailure(null); await f.register()
  const paired = JSON.parse(await dpapiUnprotect(readFileSync(file, 'utf8')))
  assert.equal(paired.phase, 'paired'); assert.equal(paired.archive_id, original.id)
  f.revoked(true); await f.register()
  const next = JSON.parse(await dpapiUnprotect(readFileSync(file, 'utf8')))
  assert.equal(next.archive_id, original.id); assert.notEqual(next.id, paired.id)
  assert.equal(await registrationBlocksExecution(f.home), true)
  assert.deepEqual(readdirSync(f.home), [])
})

test('safe activation persists new credentials before revoke; failed cleanup retries only complete and never archives twice', windows, async t => {
  const f = fixture(t); await f.register()
  assert.equal(f.calls.filter(s => s.endsWith('/complete')).length, 0)
  const lease = await acquireHostLease(f.home); assert.ok(lease)
  try { await withConnectionRegistration(f.home, () => installRegisteredConnection(f.home, process.execPath)) }
  finally { await lease.release() }
  assert.equal(JSON.parse(readFileSync(join(f.home, 'config.json'), 'utf8')).device_id, f.device)
  assert.equal(await dpapiUnprotect(readFileSync(join(f.home, 'credentials.bin'), 'utf8')), f.secret)
  assert.equal(await registrationBlocksExecution(f.home), false)
  const original = readFileSync(join(f.home, 'credentials.bin'), 'utf8')
  f.cleanupFailure(true)
  assert.equal((await withConnectionRegistration(f.home, () => finishRegisteredConnection(f.home))).category, 'REGISTRATION_CLEANUP_PENDING')
  f.cleanupFailure(false)
  assert.equal((await withConnectionRegistration(f.home, () => finishRegisteredConnection(f.home))).category, 'DEVICE_NOT_SELECTED')
  assert.equal(await registrationStatus(f.home), null)
  assert.equal(f.calls.filter(s => s.endsWith('/pair')).length, 1)
  assert.equal(f.calls.filter(s => s.endsWith('/complete')).length, 2)
  assert.equal(readFileSync(join(f.home, 'credentials.bin'), 'utf8'), original)
  assert.equal(readdirSync(f.home + '.connection-history').length, 1)
})
