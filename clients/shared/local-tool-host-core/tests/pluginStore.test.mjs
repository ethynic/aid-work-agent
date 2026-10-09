import assert from 'node:assert/strict'
import { generateKeyPairSync, sign } from 'node:crypto'
import { mkdtempSync, readFileSync, rmSync, writeFileSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { test } from 'node:test'
import yazl from 'yazl'
import { PluginStore } from '../dist/plugins/store.js'
import { canonicalJson, sha256 } from '../dist/plugins/package.js'

const keys = generateKeyPairSync('ed25519')
const platform = { platform: 'win32', arch: 'x64', node_version: '22.23.3', node_abi: '127', runtime_version: '0.2.14' }
const trust = [{ key_id: 'isolated-store-test', public_key: keys.publicKey.export({ type: 'spki', format: 'pem' }), providers: ['ai.aidwork.weixin', 'ai.aidwork.wecom'] }]
const zip = entries => new Promise((resolve, reject) => {
  const file = new yazl.ZipFile(); const chunks = []
  file.outputStream.on('data', value => chunks.push(value)); file.outputStream.on('end', () => resolve(Buffer.concat(chunks))); file.outputStream.on('error', reject)
  for (const [name, bytes] of entries) file.addBuffer(Buffer.from(bytes), name)
  file.end()
})
async function pack({ version = '1.0.0', release = 'release-1', provider = 'ai.aidwork.weixin', content = 'export {}' } = {}) {
  const entry = Buffer.from(content)
  const manifest = { manifest_version: 1, provider_id: provider, provider_version: version, display_name: '测试微信', description: 'isolated', execution_target: 'local_required', entrypoint: ['dist/cli.js', 'mcp', '--stdio'], tools: [{ name: 'weixin_status', inputSchema: { type: 'object' } }], schema_digest: 'existing',
    distribution: { platform: 'win32', arch: 'x64', node: { version: '22.23.3', modules_abi: '127' }, required_files: [{ path: 'dist/cli.js', size: entry.length, sha256: sha256(entry) }] } }
  const payload = await zip([['runtime-manifest.json', canonicalJson(manifest)], ['dist/cli.js', entry]])
  const unsigned = { envelope_version: 1, provider_release_id: release, provider_id: provider, provider_version: version, platform: 'win32', arch: 'x64', package_format: 'zip', package_size: payload.length,
    package_digest: `sha256:${sha256(payload)}`, manifest_digest: `sha256:${sha256(canonicalJson(manifest))}`, min_runtime_version: '0.2.14', max_runtime_version: null, publisher_key_id: 'isolated-store-test', signature_algorithm: 'Ed25519', created_at: '2026-10-09T00:00:00Z' }
  const envelope = { ...unsigned, signature: 'base64:' + sign(null, Buffer.from(canonicalJson(unsigned)), keys.privateKey).toString('base64') }
  return zip([['envelope.json', JSON.stringify(envelope)], ['payload.zip', payload]])
}
function fixture(t, probe = async () => ({ ready: true })) {
  const home = mkdtempSync(join(tmpdir(), 'aid-store-test-'))
  t.after(() => { assert.equal(dirname(resolve(home)), resolve(tmpdir())); assert.ok(home.includes('aid-store-test-')); rmSync(home, { recursive: true, force: true }) })
  const options = { home, platform, trust, probe }
  return { home, options, store: new PluginStore(options) }
}

test('new install is enabled but app readiness is independent and can recover on refresh', async t => {
  let ready = false; let changed = 0
  const { store, options } = fixture(t, async () => ready ? { ready: true } : { ready: false, reason: '请启动微信' })
  options.onChanged = () => changed++
  await store.initialize(); await store.import(await pack())
  assert.equal(store.list()[0].enabled, true); assert.equal(store.list()[0].ready, false)
  assert.deepEqual(store.resolveEntries({ weixin: 'legacy' }), {})
  ready = true; await store.refresh()
  assert.equal(store.list()[0].ready, true); assert.ok(store.resolveEntries({}).weixin.endsWith('dist\\cli.js') || store.resolveEntries({}).weixin.endsWith('dist/cli.js'))
  assert.equal(changed, 2, 'Installation and readiness each publish one synchronized fact change')
})

test('refresh checks large releases serially and publishes one complete readiness snapshot', async t => {
  let active = 0, peak = 0, refreshing = false, changes = 0
  const { store, options } = fixture(t, async () => {
    active++; peak = Math.max(peak, active)
    if (refreshing) assert.ok(store.list().every(plugin => plugin.ready), 'Readers must retain the old complete snapshot during checks')
    await new Promise(resolve => setTimeout(resolve, 5))
    active--; return { ready: !refreshing }
  })
  options.onChanged = () => changes++
  await store.initialize(); await store.import(await pack())
  await store.import(await pack({ provider: 'ai.aidwork.wecom' }))
  refreshing = true; await store.refresh()
  assert.equal(peak, 1, 'Large signed OCR archives must not be inflated concurrently')
  assert.ok(store.list().every(plugin => !plugin.ready))
  assert.equal(changes, 3, 'Both refreshed facts publish through one revision notification')
})
test('disabled state survives higher-version upgrade and repeated imports never re-enable it', async t => {
  const { store } = fixture(t); await store.initialize(); const first = await pack(); await store.import(first)
  const id = store.list()[0].installation_id; await store.disable(id); await store.import(first)
  assert.equal(store.list()[0].enabled, false)
  await store.import(await pack({ version: '1.1.0', release: 'release-2' }))
  assert.equal(store.list()[0].installation_id, id); assert.equal(store.list()[0].enabled, false); assert.equal(store.list()[0].version, '1.1.0')
})
test('downgrade, same-version replacement and altered immutable release retain the active release', async t => {
  const { store } = fixture(t); await store.initialize(); await store.import(await pack({ version: '2.0.0' }))
  for (const options of [{ version: '1.9.0', release: 'lower' }, { version: '2.0.0', release: 'same-version-other' }, { version: '2.0.0', content: 'different signed bytes' }]) {
    await assert.rejects(store.import(await pack(options)), error => error.code === 8)
    assert.equal(store.list()[0].release_id, 'release-1'); assert.equal(store.list()[0].ready, true)
  }
})
test('fatal version/dependency probe failure rolls back before inventory commit', async t => {
  let fail = false; const { store, home } = fixture(t, async () => { if (fail) throw new Error('fatal runtime dependency'); return { ready: true } })
  await store.initialize(); await store.import(await pack()); const before = readFileSync(join(home, 'plugins/inventory.json'), 'utf8')
  fail = true; await assert.rejects(store.import(await pack({ version: '1.1.0', release: 'bad-probe' })))
  assert.equal(readFileSync(join(home, 'plugins/inventory.json'), 'utf8'), before); assert.equal(store.list()[0].ready, true)
})
test('uninstall tombstone survives restart and prevents a configured legacy entry from reappearing', async t => {
  const { store, options } = fixture(t); await store.initialize(); await store.import(await pack()); await store.uninstall(store.list()[0].installation_id)
  const reopened = new PluginStore(options); await reopened.initialize()
  assert.deepEqual(reopened.list(), []); assert.deepEqual(reopened.resolveEntries({ weixin: 'old-path', 'boss-recruiting': 'boss-path' }), { 'boss-recruiting': 'boss-path' })
})
test('corruption affects only its own plugin and no unsigned local metadata can make it ready', async t => {
  const { store, options, home } = fixture(t); await store.initialize(); await store.import(await pack()); await store.import(await pack({ provider: 'ai.aidwork.wecom', release: 'wecom-1' }))
  const inventory = JSON.parse(readFileSync(join(home, 'plugins/inventory.json'), 'utf8'))
  const weixin = inventory.records.find(record => record.plugin_id === 'ai.aidwork.weixin')
  writeFileSync(join(home, 'plugins/releases', weixin.directory, 'payload/dist/cli.js'), 'tampered')
  const reopened = new PluginStore(options); await reopened.initialize()
  assert.equal(reopened.list().find(plugin => plugin.plugin_id === 'ai.aidwork.weixin').ready, false)
  assert.equal(reopened.list().find(plugin => plugin.plugin_id === 'ai.aidwork.wecom').ready, true)
  assert.equal(reopened.resolveEntries({ weixin: 'legacy' }).weixin, undefined)
})
test('unfinished transaction or unreadable inventory fails loudly without replaying its probe', async t => {
  let probes = 0; const { store, options, home } = fixture(t, async () => { probes++; return { ready: true } })
  await store.initialize(); writeFileSync(join(home, 'plugins/pending.json'), '{}')
  await assert.rejects(new PluginStore(options).initialize(), error => error.code === 11)
  assert.equal(probes, 0)
  rmSync(join(home, 'plugins/pending.json')); writeFileSync(join(home, 'plugins/inventory.json'), 'broken')
  await assert.rejects(new PluginStore(options).initialize(), error => error.code === 11)
  assert.equal(probes, 0)
})
