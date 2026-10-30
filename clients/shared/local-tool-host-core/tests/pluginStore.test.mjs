import assert from 'node:assert/strict'
import { generateKeyPairSync, randomUUID, sign } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { test } from 'node:test'
import yazl from 'yazl'
import { PluginStore } from '../dist/plugins/store.js'
import { canonicalJson, sha256 } from '../dist/plugins/package.js'
import { deviceCapabilities } from '../dist/legacy/config.js'

const keys = generateKeyPairSync('ed25519')
const platform = { platform: 'win32', arch: 'x64', node_version: '22.23.3', node_abi: '127', runtime_version: '0.2.14' }
const trust = [{ key_id: 'isolated-store-test', public_key: keys.publicKey.export({ type: 'spki', format: 'pem' }), providers: ['ai.aidwork.weixin', 'ai.aidwork.wecom', 'ai.aidwork.boss-recruiting'] }]
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
  assert.ok(store.resolveEntries({ weixin: 'legacy' }).weixin !== 'legacy', 'An installed app remains discoverable before login so the agent can diagnose it')
  ready = true; await store.refresh()
  assert.equal(store.list()[0].ready, true); assert.ok(store.resolveEntries({}).weixin.endsWith('dist\\cli.js') || store.resolveEntries({}).weixin.endsWith('dist/cli.js'))
  assert.equal(changed, 2, 'Installation and readiness each publish one synchronized fact change')
})

test('verified BOSS remains available without a provider-specific bootstrap exception', async t => {
  const { store, options } = fixture(t, async () => ({ ready: false, reason: '调试 Chrome 尚未启动' }))
  await store.initialize(); await store.import(await pack({ provider: 'ai.aidwork.boss-recruiting' }))
  assert.equal(store.list()[0].ready, false)
  assert.ok(store.resolveEntries({})['boss-recruiting'])
  const entries = store.resolveEntries({}, ['boss-recruiting'])
  assert.ok(entries['boss-recruiting'])
  assert.deepEqual(deviceCapabilities({ providers: { 'boss-recruiting': { entry: entries['boss-recruiting'] } } }).providers, ['boss-recruiting'])
  const reopened = new PluginStore(options); await reopened.initialize()
  assert.equal(reopened.resolveEntries({}, ['boss-recruiting'])['boss-recruiting'], entries['boss-recruiting'])
  await reopened.disable(reopened.list()[0].installation_id)
  assert.deepEqual(reopened.resolveEntries({ 'boss-recruiting': 'legacy' }, ['boss-recruiting']), {})
  await reopened.enable(reopened.list()[0].installation_id)
  await reopened.uninstall(reopened.list()[0].installation_id)
  assert.deepEqual(reopened.resolveEntries({ 'boss-recruiting': 'legacy' }, ['boss-recruiting']), {})
})

test('all verified unready providers remain discoverable, while tampered or invalid releases cannot execute', async t => {
  const { store, home, options } = fixture(t, async () => ({ ready: false }))
  await store.initialize()
  await store.import(await pack({ provider: 'ai.aidwork.boss-recruiting' }))
  await store.import(await pack({ provider: 'ai.aidwork.weixin' }))
  await store.import(await pack({ provider: 'ai.aidwork.wecom' }))
  assert.deepEqual(Object.keys(store.resolveEntries({}, ['boss-recruiting'])), ['boss-recruiting', 'weixin', 'wecom'])
  const entry = store.resolveEntries({}, ['boss-recruiting'])['boss-recruiting']
  writeFileSync(entry, 'tampered')
  await store.refresh()
  const remaining = store.resolveEntries({ 'boss-recruiting': 'legacy' }, ['boss-recruiting'])
  assert.equal(remaining['boss-recruiting'], undefined)
  assert.deepEqual(Object.keys(remaining), ['weixin', 'wecom'], 'A broken BOSS package cannot hide unrelated installed tools')
  const { store: failedStore } = fixture(t, async () => { throw new Error('version mismatch') })
  await failedStore.initialize()
  await assert.rejects(failedStore.import(await pack({ provider: 'ai.aidwork.boss-recruiting' })))
  assert.deepEqual(failedStore.resolveEntries({}, ['boss-recruiting']), {})
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
test('explicit signed same-version replacement and rollback work without uninstall, but immutable release bytes cannot change', async t => {
  const { store } = fixture(t); await store.initialize(); await store.import(await pack({ version: '2.0.0' }))
  const id = store.list()[0].installation_id
  await assert.rejects(store.import(await pack({ version: '2.0.0', content: 'different signed bytes' })), error => error.code === 8)
  assert.equal(store.list()[0].release_id, 'release-1')
  await store.import(await pack({ version: '2.0.0', release: 'same-version-other' }))
  assert.equal(store.list()[0].release_id, 'same-version-other')
  await store.import(await pack({ version: '1.9.0', release: 'lower' }))
  assert.equal(store.list()[0].version, '1.9.0'); assert.equal(store.list()[0].installation_id, id)
  await store.uninstall(id)
  await store.import(await pack({ version: '1.0.0', release: 'older-after-uninstall' }))
  assert.equal(store.list()[0].version, '1.0.0'); assert.equal(store.list()[0].installation_id, id)
})
test('same-package reinstall repairs damaged files atomically and preserves a disabled installation', async t => {
  const { store, options, home } = fixture(t); await store.initialize()
  const bytes = await pack(); await store.import(bytes)
  const id = store.list()[0].installation_id
  const originalEntry = store.resolveEntries({}).weixin
  const originalInventory = JSON.parse(readFileSync(join(home, 'plugins/inventory.json'), 'utf8'))
  await store.import(bytes)
  assert.equal(store.resolveEntries({}).weixin, originalEntry, 'A healthy identical release is an idempotent no-op')
  writeFileSync(originalEntry, 'damaged')
  await store.disable(id); await store.import(bytes)
  assert.equal(store.list()[0].installation_id, id); assert.equal(store.list()[0].enabled, false)
  const repairedInventory = JSON.parse(readFileSync(join(home, 'plugins/inventory.json'), 'utf8'))
  assert.notEqual(repairedInventory.records[0].directory, originalInventory.records[0].directory)
  const reopened = new PluginStore(options); await reopened.initialize(); await reopened.enable(id)
  assert.equal(readFileSync(reopened.resolveEntries({}).weixin, 'utf8'), 'export {}', 'Trusted same-package repair must restore the actual payload, not only readiness metadata')
})
test('rollback keeps retained signed release identities immutable after reopening without a separate history ledger', async t => {
  const { store, options, home } = fixture(t); await store.initialize()
  const releaseA = await pack({ version: '1.0.0', release: 'release-A' })
  await store.import(releaseA)
  const original = JSON.parse(readFileSync(join(home, 'plugins/inventory.json'), 'utf8')).records[0]
  await store.import(await pack({ version: '1.1.0', release: 'release-B' }))
  const reopened = new PluginStore(options); await reopened.initialize()
  await assert.rejects(reopened.import(await pack({ version: '1.0.0', release: 'release-A', content: 'signed but changed A' })), error => error.code === 8)
  assert.equal(reopened.list()[0].release_id, 'release-B', 'A signature does not authorize changing the identity of an already retained release')
  const retainedArchive = join(home, 'plugins/releases', original.directory, 'archive.zip')
  // Retained identity reads must not inflate an old OCR payload. Its signed
  // envelope remains sufficient even when the unused payload data is damaged.
  const archive = readFileSync(retainedArchive)
  const payloadHeader = archive.indexOf(Buffer.from('payload.zip'))
  assert.ok(payloadHeader > 0)
  archive[payloadHeader + 'payload.zip'.length] ^= 0xff
  writeFileSync(retainedArchive, archive)
  await assert.rejects(reopened.import(await pack({ version: '1.0.0', release: 'release-A', content: 'changed again' })), error => error.code === 8)
  await reopened.import(releaseA)
  assert.equal(reopened.list()[0].release_id, 'release-A', 'The original signed release remains a valid explicit rollback')
})
test('fatal version/dependency probe failure rolls back before inventory commit', async t => {
  let fail = false; const { store, home } = fixture(t, async () => { if (fail) throw new Error('fatal runtime dependency'); return { ready: true } })
  await store.initialize(); await store.import(await pack()); const before = readFileSync(join(home, 'plugins/inventory.json'), 'utf8')
  fail = true; await assert.rejects(store.import(await pack({ version: '1.1.0', release: 'bad-probe' })))
  assert.equal(readFileSync(join(home, 'plugins/inventory.json'), 'utf8'), before); assert.equal(store.list()[0].ready, true)
})
test('uninstall tombstone survives restart and prevents a configured legacy entry from reappearing', async t => {
  const { store, options, home } = fixture(t); await store.initialize(); await store.import(await pack())
  await store.import(await pack({ version: '1.1.0', release: 'weixin-2' }))
  await store.import(await pack({ provider: 'ai.aidwork.wecom', release: 'wecom-1' }))
  mkdirSync(join(home, 'journal')); writeFileSync(join(home, 'journal/old.jsonl'), 'original unknown send')
  await store.uninstall(store.list().find(plugin => plugin.plugin_id === 'ai.aidwork.weixin').installation_id)
  assert.equal(readdirSync(join(home, 'plugins/releases')).length, 1, 'Uninstall reclaims both large versions of its own provider, retaining the other installed provider')
  assert.equal(readFileSync(join(home, 'journal/old.jsonl'), 'utf8'), 'original unknown send')
  const reopened = new PluginStore(options); await reopened.initialize()
  assert.equal(reopened.list().length, 1)
  const entries = reopened.resolveEntries({ weixin: 'old-path', 'boss-recruiting': 'boss-path' })
  assert.equal(entries.weixin, undefined); assert.equal(entries['boss-recruiting'], 'boss-path')
  assert.ok(entries.wecom)
  await reopened.import(await pack())
  assert.ok(reopened.resolveEntries({}).weixin, 'The signed offline package can reinstall after its old payload was collected')
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
test('successful upgrade bounds retained packages without deleting another provider or unknown business evidence', async t => {
  const { store, home } = fixture(t); await store.initialize()
  const releases = join(home, 'plugins/releases')
  const directory = provider => JSON.parse(readFileSync(join(home, 'plugins/inventory.json'), 'utf8')).records.find(record => record.plugin_id === provider).directory
  await store.import(await pack())
  const first = directory('ai.aidwork.weixin')
  await store.import(await pack({ release: 'wecom-1', provider: 'ai.aidwork.wecom' }))
  const other = directory('ai.aidwork.wecom')
  await store.import(await pack({ version: '1.1.0', release: 'weixin-2' }))
  const previous = directory('ai.aidwork.weixin')
  const orphan = join(releases, randomUUID()); mkdirSync(orphan); writeFileSync(join(orphan, 'archive.zip'), 'incomplete staging')
  const outside = mkdtempSync(join(tmpdir(), 'plugin-cleanup-outside-')); t.after(() => rmSync(outside, { recursive: true, force: true }))
  writeFileSync(join(outside, 'keep.txt'), 'not owned by the plugin store')
  symlinkSync(outside, join(releases, randomUUID()), process.platform === 'win32' ? 'junction' : 'dir')
  mkdirSync(join(home, 'journal')); writeFileSync(join(home, 'journal/old.jsonl'), 'unknown send receipt')
  await store.import(await pack({ version: '1.2.0', release: 'weixin-3' }))
  assert.equal(existsSync(join(releases, first)), false, 'The third installation must not retain every historical OCR payload')
  assert.ok(existsSync(join(releases, previous)), 'Keep one immediately previous version for an explicit rollback')
  assert.ok(existsSync(join(releases, other)), 'Maintenance of one provider does not collect another installed provider')
  assert.equal(existsSync(orphan), false)
  assert.equal(readFileSync(join(outside, 'keep.txt'), 'utf8'), 'not owned by the plugin store')
  assert.equal(readFileSync(join(home, 'journal/old.jsonl'), 'utf8'), 'unknown send receipt')
  assert.equal(readdirSync(releases, { withFileTypes: true }).filter(entry => !entry.isSymbolicLink()).length, 3)
})

test('pending installation intent does not block an empty host or replay an uncommitted package', async t => {
  let probes = 0; const { store, options, home } = fixture(t, async () => { probes++; return { ready: true } })
  await store.initialize(); writeFileSync(join(home, 'plugins/pending.json'), '{}')
  const reopened = new PluginStore(options); await reopened.initialize()
  assert.deepEqual(reopened.list(), []); assert.equal(existsSync(join(home, 'plugins/pending.json')), false)
  assert.equal(probes, 0)
  writeFileSync(join(home, 'plugins/inventory.json'), 'broken')
  await assert.rejects(new PluginStore(options).initialize(), error => error.code === 11)
  assert.equal(probes, 0)
})
test('interrupted install uses the atomic inventory both before and after a release switch', async t => {
  const releasesProbed = []
  const { store, options, home } = fixture(t, async root => {
    releasesProbed.push(root); return { ready: true }
  })
  await store.initialize(); await store.import(await pack())
  const originalEntry = store.resolveEntries({}).weixin
  writeFileSync(join(home, 'plugins/pending.json'), JSON.stringify({ version: 1, action: 'import', installation_id: store.list()[0].installation_id }))
  const beforeCommit = new PluginStore(options); await beforeCommit.initialize()
  assert.equal(beforeCommit.resolveEntries({}).weixin, originalEntry, 'An intent before commit must leave the old active release usable')
  await beforeCommit.import(await pack({ version: '1.1.0', release: 'new-committed' }))
  const committedEntry = beforeCommit.resolveEntries({}).weixin
  writeFileSync(join(home, 'plugins/pending.json'), JSON.stringify({ version: 1, action: 'import', installation_id: beforeCommit.list()[0].installation_id }))
  const afterCommit = new PluginStore(options); await afterCommit.initialize()
  assert.equal(afterCommit.resolveEntries({}).weixin, committedEntry, 'A crash after commit must not roll back the already committed release')
  assert.equal(afterCommit.list()[0].release_id, 'new-committed')
  assert.equal(releasesProbed.length, 4, 'Reopening verifies only committed releases and does not replay installation or an unrelated selected package')
})
test('failed pending cleanup preserves committed tools and retries the next plugin write in the same host', async t => {
  const { store, options, home } = fixture(t); await store.initialize(); await store.import(await pack())
  const entry = store.resolveEntries({}).weixin
  const inventoryFile = join(home, 'plugins/inventory.json')
  const committed = readFileSync(inventoryFile, 'utf8')
  const pending = join(home, 'plugins/pending.json')
  mkdirSync(pending)
  const reopened = new PluginStore(options); await reopened.initialize()
  assert.equal(reopened.resolveEntries({}).weixin, entry, 'A failed unlink of installation intent cannot disable a verified active package')
  assert.equal(reopened.list()[0].ready, true); assert.match(reopened.list()[0].reason, /中断标记无法清理/)
  const upgrade = await pack({ version: '1.1.0', release: 'after-cleanup' })
  await assert.rejects(reopened.import(upgrade), error => error.code === 11 && /目录权限/.test(error.message))
  assert.equal(readFileSync(inventoryFile, 'utf8'), committed, 'A write must not overwrite an intent that could not be cleared')
  rmSync(pending, { recursive: true })
  await reopened.import(upgrade)
  assert.equal(reopened.list()[0].release_id, 'after-cleanup')
  assert.equal(reopened.list()[0].reason, undefined, 'Repairing the local path permits a retry without restarting or resetting the host')
})
test('listing an installed plugin is a cached metadata read without repeating doctor', async t => {
  let probes = 0
  const { store } = fixture(t, async () => { probes++; return { ready: false, reason: '请登录微信' } })
  await store.initialize(); await store.import(await pack())
  for (let i = 0; i < 5; i++) {
    assert.equal(store.list()[0].reason, '请登录微信')
    assert.ok(store.resolveEntries({}).weixin)
  }
  assert.equal(probes, 1, 'Opening the plugin list cannot launch diagnostic processes repeatedly')
})
