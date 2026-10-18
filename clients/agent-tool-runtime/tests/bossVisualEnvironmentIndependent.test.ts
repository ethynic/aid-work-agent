import assert from 'node:assert/strict'
import test from 'node:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { randomUUID } from 'node:crypto'
import { LegacyExecution } from '../src/runtimeHost.js'
import { configureRuntimeHome } from '../src/config.js'
import { saveDeviceToken } from '../src/credentials.js'
import type { ProviderSet } from '../src/providerManager.js'
import { FakeCloud } from './helpers/fakeCloud.js'
import { fakeProviderEntry } from './helpers/runtimeStack.js'
import { effectiveManifestFor, isToolAllowedFor, isWriteToolFor } from '../src/providers.js'

test('independent: the effective BOSS catalog admits open_detail as a page-state read operation', () => {
  const manifest = effectiveManifestFor('boss-recruiting')!
  assert.equal(isToolAllowedFor(manifest, 'boss_open_detail'), true)
  assert.equal(isWriteToolFor(manifest, 'boss_open_detail'), false)
  assert.equal(isToolAllowedFor(manifest, 'boss_unknown_detail'), false)
})

test('independent: actual Host launches BOSS with only its backend credentials; other Providers receive none', { skip: process.platform !== 'win32', timeout: 30_000 }, async () => {
  const home = mkdtempSync(join(tmpdir(), 'boss-visual-env-'))
  const cloud = new FakeCloud(); await cloud.start()
  const device = cloud.createDeviceDirectly()
  const entry = join(home, 'provider.mjs')
  const envKeys = ['AID_BOSS_SERVER_URL', 'AID_BOSS_SERVER_TOKEN', 'ZHIPU_API_KEY'] as const
  const before = Object.fromEntries(envKeys.map(key => [key, process.env[key]]))
  let adapter: LegacyExecution | undefined
  try {
    process.env.AID_BOSS_SERVER_URL = 'http://127.0.0.1:7999'
    process.env.AID_BOSS_SERVER_TOKEN = 'fictional-boss-binding'
    process.env.ZHIPU_API_KEY = 'fictional-vendor-must-not-inherit'
    writeFileSync(entry, `import { writeFileSync } from 'node:fs';
writeFileSync(${JSON.stringify(join(home, 'observed.json'))}, JSON.stringify({
  bossUrl: process.env.AID_BOSS_SERVER_URL === 'http://127.0.0.1:7999',
  bossBinding: process.env.AID_BOSS_SERVER_TOKEN === 'fictional-boss-binding',
  vendorPresent: 'ZHIPU_API_KEY' in process.env
}));
const {startFakeProvider}=await import(${JSON.stringify(pathToFileURL(fakeProviderEntry()).href)}); await startFakeProvider();`)
    writeFileSync(join(home, 'config.json'), JSON.stringify({ server: cloud.baseUrl, device_id: device.device_id,
      providers: { 'boss-recruiting': { entry }, wecom: { entry } } }))
    configureRuntimeHome(home); await saveDeviceToken(device.token)
    adapter = new LegacyExecution(process.execPath, home, undefined, true)
    await adapter.initialize(); await adapter.executionMarker(randomUUID()); await adapter.start(() => {}, () => {})
    const providers = (adapter as unknown as { providers: ProviderSet }).providers
    await providers.get('boss-recruiting').ensureStarted()
    assert.deepEqual(JSON.parse(readFileSync(join(home, 'observed.json'), 'utf8')), { bossUrl: true, bossBinding: true, vendorPresent: false })
    await providers.get('wecom').ensureStarted()
    assert.deepEqual(JSON.parse(readFileSync(join(home, 'observed.json'), 'utf8')), { bossUrl: false, bossBinding: false, vendorPresent: false })
  } finally {
    await adapter?.stop(); await cloud.stop(); configureRuntimeHome(undefined)
    for (const key of envKeys) { if (before[key] === undefined) delete process.env[key]; else process.env[key] = before[key] }
    rmSync(home, { recursive: true, force: true })
  }
})
