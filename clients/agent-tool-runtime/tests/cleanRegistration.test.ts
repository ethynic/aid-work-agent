import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, readdirSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { beginCleanRegistration, pairCleanRegistration, readCleanRegistration, installCleanRegistration, finishCleanRegistration, retireCleanRegistration } from '../src/cleanRegistration.js'
import { dpapiProtect, dpapiUnprotect } from '../src/dpapi.js'

test('only a confirmed response creates a protected registration and no OTP is saved', async () => {
  const root = mkdtempSync(join(tmpdir(), 'aid-clean-registration-'))
  const metadata = join(root, 'metadata'), home = join(root, 'new-home'), old = join(root, 'old-home')
  mkdirSync(metadata); mkdirSync(home); mkdirSync(old)
  const originalFetch = globalThis.fetch
  const device = randomUUID()
  const calls: Record<string, unknown>[] = []
  try {
    writeFileSync(join(old, 'config.json'), JSON.stringify({ server: 'http://127.0.0.1:1', device_id: randomUUID() }))
    writeFileSync(join(old, 'credentials.bin'), await dpapiProtect('a'.repeat(64)))
    writeFileSync(join(old, 'management-secret.bin'), 'corrupt')
    writeFileSync(join(old, 'host-running.json'), '{bad')
    const r = await beginCleanRegistration(metadata, { server: 'http://127.0.0.1:1', device_name: '测试设备' }, [old])
    assert.deepEqual(readdirSync(metadata), [])
    globalThis.fetch = async (_url, options) => {
      const request = JSON.parse(String(options?.body)); calls.push(request)
      if (calls.length === 1) throw new Error('response dropped after server commit')
      return new Response(JSON.stringify({ success: true, pairing_protocol: 3, registration_completed: true,
        device_id: device, device_token: request.registration_token }), { status: 200 })
    }
    await assert.rejects(pairCleanRegistration(metadata, r, 'fixture-otp'))
    assert.equal(await readCleanRegistration(metadata), null)
    assert.deepEqual(readdirSync(metadata), [])
    const explicitlyResubmitted = await beginCleanRegistration(metadata, { server: r.server, device_name: '测试设备' }, [old])
    await pairCleanRegistration(metadata, explicitlyResubmitted, 'new-fixture-otp')
    const restored = await readCleanRegistration(metadata)
    assert.equal(restored?.phase, 'paired')
    assert.equal(Object.hasOwn(restored!.request, 'code'), false)
    assert.notEqual(calls[0]?.registration_id, calls[1]?.registration_id)
    await installCleanRegistration(metadata, restored!, home)
    assert.equal(JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')).device_id, device)
    assert.ok(await dpapiUnprotect(readFileSync(join(home, 'credentials.bin'), 'utf8')) === restored!.request.registration_token)
    assert.equal(readFileSync(join(old, 'host-running.json'), 'utf8'), '{bad')
    finishCleanRegistration(metadata)
    assert.equal(await readCleanRegistration(metadata), null)
  } finally { globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true }) }
})

test('old corrupted registration and invalid identity cannot block an explicit clean registration', async () => {
  const root = mkdtempSync(join(tmpdir(), 'aid-clean-damage-'))
  try {
    writeFileSync(join(root, 'clean-registration.bin'), 'unreadable')
    await assert.rejects(readCleanRegistration(root))
    retireCleanRegistration(root)
    const r = await beginCleanRegistration(root, { server: 'http://127.0.0.1:1', device_name: '新设备' }, [join(root, 'missing-old')])
    assert.equal(r.phase, 'requested'); assert.equal(r.request.previous_device_token, undefined)
    assert.equal(await readCleanRegistration(root), null)
  } finally { rmSync(root, { recursive: true, force: true }) }
})

test('a real protocol 3 redirect is distinguishable from a lost response and never forwards the request', async () => {
  const root = mkdtempSync(join(tmpdir(), 'aid-clean-redirect-'))
  let forwarded = 0
  const server = createServer((request, response) => {
    request.resume()
    if (request.url === '/api/local-tools/runtime/pair') response.writeHead(301, { Location: '/different-origin' }).end()
    else { forwarded++; response.writeHead(200).end() }
  })
  server.listen(0, '127.0.0.1')
  try {
    await once(server, 'listening')
    const address = server.address()
    assert.ok(address && typeof address !== 'string')
    const record = await beginCleanRegistration(root, { server: `http://127.0.0.1:${address.port}`, device_name: '测试设备' }, [])
    await assert.rejects(pairCleanRegistration(root, record, 'fixture-otp'), { status: 301, code: 'PAIRING_REDIRECT' })
    assert.equal(forwarded, 0)
  } finally { server.close(); rmSync(root, { recursive: true, force: true }) }
})
