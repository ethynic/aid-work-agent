import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, test } from 'node:test'
import { configureRuntimeHome } from '../src/config.js'
import { atomicWrite, clearPairing, recoverPairing } from '../src/pairingStore.js'

let home: string
beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), 'runtime-identity-'))
  configureRuntimeHome(home)
  writeFileSync(join(home, 'credentials.bin'), 'old-encrypted-credential')
  writeFileSync(join(home, 'config.json'), JSON.stringify({ server: 'https://old.example', device_id: 'old-device' }))
})
afterEach(() => {
  configureRuntimeHome(undefined)
  rmSync(home, { recursive: true, force: true })
})

test('interrupted pairing rolls both files forward together, retaining locally configured providers', async () => {
  const cipher = Buffer.from('test encrypted bytes').toString('base64')
  const config = { server: 'https://new.example', device_id: 'new-device', providers: { weixin: { entry: 'C:\\installed\\weixin.js', v2Send: true } } }
  atomicWrite(join(home, 'pairing.pending.json'), JSON.stringify({ config, encrypted_token: cipher }))
  // A crash occurred after committing the credential and before committing config.
  writeFileSync(join(home, 'credentials.bin'), cipher)
  await recoverPairing(async () => 'test credential')
  assert.equal(readFileSync(join(home, 'credentials.bin'), 'utf8'), cipher)
  assert.deepEqual(JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')), config)
  assert.equal(existsSync(join(home, 'pairing.pending.json')), false)
  await recoverPairing(async () => 'test credential') // Already committed is idempotent.
})

test('invalid pending ciphertext leaves old identity untouched and stays available for repair', async () => {
  atomicWrite(join(home, 'pairing.pending.json'), JSON.stringify({ config: { server: 'https://new.example', device_id: 'new' }, encrypted_token: 'raw token!' }))
  await assert.rejects(recoverPairing(), /事务格式无效/)
  assert.equal(readFileSync(join(home, 'credentials.bin'), 'utf8'), 'old-encrypted-credential')
  assert.equal(JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')).device_id, 'old-device')
  assert.equal(existsSync(join(home, 'pairing.pending.json')), true)
})

test('pending config cannot introduce plaintext credentials or URL credentials', async () => {
  const pending = join(home, 'pairing.pending.json')
  for (const config of [{ server: 'https://new.example', device_id: 'new', token: 'secret' }, { server: 'https://user:password@example.com', device_id: 'new' }]) {
    atomicWrite(pending, JSON.stringify({ config, encrypted_token: 'Y2lwaGVy' }))
    await assert.rejects(recoverPairing(), /事务格式无效/)
    assert.equal(JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')).device_id, 'old-device')
  }
})

test('corrupt DPAPI payload cannot overwrite the old credential despite syntactically valid base64', async () => {
  atomicWrite(join(home, 'pairing.pending.json'), JSON.stringify({ config: { server: 'https://new.example', device_id: 'new' }, encrypted_token: 'Y2lwaGVy' }))
  await assert.rejects(recoverPairing(async () => { throw new Error('invalid DPAPI payload') }), /凭证无法解密/)
  assert.equal(readFileSync(join(home, 'credentials.bin'), 'utf8'), 'old-encrypted-credential')
  assert.equal(JSON.parse(readFileSync(join(home, 'config.json'), 'utf8')).device_id, 'old-device')
  assert.equal(existsSync(join(home, 'pairing.pending.json')), true)
})

test('interrupted unpair resumes deletion instead of leaving a half-cleared identity', async () => {
  atomicWrite(join(home, 'pairing.pending.json'), JSON.stringify({ action: 'unpair' }))
  rmSync(join(home, 'credentials.bin'))
  await recoverPairing(async () => 'test credential')
  assert.equal(existsSync(join(home, 'credentials.bin')), false)
  assert.equal(existsSync(join(home, 'config.json')), false)
  assert.equal(existsSync(join(home, 'pairing.pending.json')), false)
  await clearPairing()
  assert.equal(existsSync(join(home, 'pairing.pending.json')), false)
})
