import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { loadPackagedTrust } from '../src/productTrust.js'

test('plugin trust comes only from fixed matching product resources; production rejects acceptance keys', t => {
  const resources = mkdtempSync(join(tmpdir(), 'aid-product-trust-')); t.after(() => rmSync(resources, { recursive: true, force: true }))
  const entry = join(resources, 'runtime/host'); mkdirSync(entry, { recursive: true }); mkdirSync(join(resources, 'config'))
  const root = { key_id: 'isolated-dev', public_key: generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' }), providers: ['ai.aidwork.weixin'], test_only: true }
  const product = (profile: string) => writeFileSync(join(resources, 'config/runtime-product.json'), JSON.stringify({ schemaVersion: 1, productKind: 'runtime', profile }))
  const trust = (profile: string, roots: unknown[] = [root]) => writeFileSync(join(resources, 'runtime/trust-roots.json'), JSON.stringify({ schemaVersion: 1, profile, roots }))
  assert.equal(loadPackagedTrust(entry), undefined, 'Unconfigured A1/CLI cannot open plugin installation')
  product('acceptance'); trust('acceptance')
  assert.equal(loadPackagedTrust(entry)?.[0]?.test_only, true)
  product('production')
  assert.throws(() => loadPackagedTrust(entry), error => (error as { code: number }).code === 9)
  trust('production')
  assert.throws(() => loadPackagedTrust(entry), error => (error as { code: number }).code === 9)
  product('acceptance'); trust('acceptance', [{ ...root, test_only: undefined }])
  assert.throws(() => loadPackagedTrust(entry), error => (error as { code: number }).code === 9, 'Missing test marker cannot silently become a production key')
  trust('acceptance', [{ ...root, providers: ['third-party.skill'] }])
  assert.throws(() => loadPackagedTrust(entry), error => (error as { code: number }).code === 9)
  const privatePem = generateKeyPairSync('ed25519').privateKey.export({ type: 'pkcs8', format: 'pem' })
  trust('acceptance', [{ ...root, public_key: privatePem }])
  assert.throws(() => loadPackagedTrust(entry), error => (error as { code: number }).code === 9, 'Public-key assets must reject private PEM even when crypto can derive its public key')
})
