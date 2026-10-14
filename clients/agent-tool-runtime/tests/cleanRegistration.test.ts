import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { beginCleanRegistration, pairCleanRegistration, readCleanRegistration, installCleanRegistration, finishCleanRegistration } from '../src/cleanRegistration.js'
import { dpapiProtect, dpapiUnprotect } from '../src/dpapi.js'

test('real protected registration survives a lost response with no saved OTP or duplicate identity', async () => {
  const root = mkdtempSync(join(tmpdir(), 'aid-clean-registration-'))
  const metadata = join(root, 'metadata'), home = join(root, 'new-home'), old = join(root, 'old-home')
  mkdirSync(metadata); mkdirSync(home); mkdirSync(old)
  const originalFetch = globalThis.fetch
  const device = randomUUID(); let created = 0, calls = 0; let stored: Record<string, unknown> | undefined
  try {
    writeFileSync(join(old, 'config.json'), JSON.stringify({ server: 'http://127.0.0.1:1', device_id: randomUUID() }))
    writeFileSync(join(old, 'credentials.bin'), await dpapiProtect('a'.repeat(64)))
    writeFileSync(join(old, 'management-secret.bin'), 'corrupt')
    writeFileSync(join(old, 'host-running.json'), '{bad')
    const r = await beginCleanRegistration(metadata, { server: 'http://127.0.0.1:1', device_name: '测试设备' }, [old])
    assert.ok(!readFileSync(join(metadata, 'clean-registration.bin'), 'utf8').includes('fixture-otp'))
    globalThis.fetch = async (_url, options) => {
      const request = JSON.parse(String(options?.body)); calls++
      if (!stored) { stored = request; created++ }
      const committed = stored!
      assert.ok(request.registration_id === committed.registration_id)
      assert.ok(request.registration_token === committed.registration_token)
      if (calls === 1) throw new Error('response dropped after server commit')
      assert.equal(request.code, undefined)
      return new Response(JSON.stringify({ success: true, pairing_protocol: 3, registration_completed: true,
        device_id: device, device_token: request.registration_token }), { status: 200 })
    }
    await assert.rejects(pairCleanRegistration(metadata, r, 'fixture-otp'))
    const restored = await readCleanRegistration(metadata)
    assert.equal(restored?.phase, 'requested')
    await pairCleanRegistration(metadata, restored!)
    await installCleanRegistration(metadata, restored!, home)
    assert.equal(created, 1)
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
    const r = await beginCleanRegistration(root, { server: 'http://127.0.0.1:1', device_name: '新设备' }, [join(root, 'missing-old')])
    assert.equal(r.phase, 'requested'); assert.equal(r.request.previous_device_token, undefined)
    assert.equal((await readCleanRegistration(root))?.request.registration_id, r.request.registration_id)
  } finally { rmSync(root, { recursive: true, force: true }) }
})
