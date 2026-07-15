import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { EncryptedCredentialStore, isAllowedCredentialKey, isAllowedCredentialValue } from '../electron/credentials.js'

const encryption = {
  isEncryptionAvailable: () => true,
  encryptString: (value: string) => Buffer.from(`encrypted:${value}`),
  decryptString: (value: Buffer) => value.toString().replace(/^encrypted:/, ''),
}

test('只允许 Agent/tenant credential key，防止 Desktop 获得 Portal 凭证命名空间', () => {
  assert.equal(isAllowedCredentialKey('demo_token'), true)
  assert.equal(isAllowedCredentialKey('saas_token_tenant-a'), true)
  assert.equal(isAllowedCredentialKey('portal_token'), false)
  assert.equal(isAllowedCredentialKey('../token'), false)
  assert.equal(isAllowedCredentialKey(`saas_token_${'a'.repeat(129)}`), false)
  assert.equal(isAllowedCredentialValue('a'.repeat(64 * 1024)), true)
  assert.equal(isAllowedCredentialValue('a'.repeat(64 * 1024 + 1)), false)
})

test('凭证磁盘只保存 safeStorage 密文，hydrate 与 logout 删除保持契约', () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'agent-credential-'))
  const file = path.join(directory, 'credentials.json')
  const store = new EncryptedCredentialStore(file, encryption)
  store.set('demo_token', 'test-secret-marker')
  assert.doesNotMatch(readFileSync(file, 'utf8'), /test-secret-marker/)
  assert.equal(store.load().demo_token, 'test-secret-marker')
  store.delete('demo_token')
  assert.equal(store.load().demo_token, undefined)
})

test('safeStorage 不可用或解密失败时 fail loud，不回退明文', () => {
  const file = path.join(mkdtempSync(path.join(os.tmpdir(), 'agent-credential-')), 'credentials.json')
  const unavailable = new EncryptedCredentialStore(file, { ...encryption, isEncryptionAvailable: () => false })
  assert.throws(() => unavailable.set('demo_token', 'value'), /unavailable/)
})

test('corrupt, tampered, forbidden, and undecryptable files fail without exposing contents', () => {
  const file = path.join(mkdtempSync(path.join(os.tmpdir(), 'agent-credential-')), 'credentials.json')
  const store = new EncryptedCredentialStore(file, encryption)
  for (const content of ['null', '[]', '{', '{"portal_token":"dGVzdA=="}', '{"demo_token":1}', '{"demo_token":"not base64"}']) {
    writeFileSync(file, content)
    assert.throws(() => store.load(), (error: Error) => !error.message.includes(content))
  }
  writeFileSync(file, JSON.stringify({ demo_token: Buffer.from('ciphertext').toString('base64') }))
  const decryptFailure = new EncryptedCredentialStore(file, {
    ...encryption,
    decryptString: () => { throw new Error('secret-marker') },
  })
  assert.throws(
    () => decryptFailure.load(),
    (error: Error) => /cannot be decrypted/.test(error.message) && !error.message.includes('secret-marker'),
  )
})
