import assert from 'node:assert/strict'
import { generateKeyPairSync, randomUUID, sign } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, symlinkSync, utimesSync, writeFileSync } from 'node:fs'
import filePromises from 'node:fs/promises'
import { syncBuiltinESMExports } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { test } from 'node:test'
import yazl from 'yazl'
import { PluginStore } from '../dist/plugins/store.js'
import { canonicalJson, sha256 } from '../dist/plugins/package.js'
import { deviceCapabilities } from '../dist/legacy/config.js'
import { deriveProviderManifest, isToolAllowedFor, isWriteToolFor } from '../dist/legacy/providers.js'

const keys = generateKeyPairSync('ed25519')
const platform = { platform: 'win32', arch: 'x64', node_version: '22.23.3', node_abi: '127', runtime_version: '0.2.14' }
const trust = [{ key_id: 'isolated-store-test', public_key: keys.publicKey.export({ type: 'spki', format: 'pem' }), providers: ['ai.aidwork.weixin', 'ai.aidwork.wecom', 'ai.aidwork.boss-recruiting'] }]
const zip = entries => new Promise((resolve, reject) => {
  const file = new yazl.ZipFile(); const chunks = []
  file.outputStream.on('data', value => chunks.push(value)); file.outputStream.on('end', () => resolve(Buffer.concat(chunks))); file.outputStream.on('error', reject)
  for (const [name, bytes] of entries) file.addBuffer(Buffer.from(bytes), name)
  file.end()
})
// declared：默认带有效调度声明（新 CLI 形态）；false = 旧 CLI 无声明；对象 = 自定义（损坏声明用例）
async function pack({ version = '1.0.0', release = 'release-1', provider = 'ai.aidwork.weixin', content = 'export {}',
  tools = [{ name: 'weixin_status', inputSchema: { type: 'object' } }], declared = true } = {}) {
  const entry = Buffer.from(content)
  const manifest = { manifest_version: 1, provider_id: provider, provider_version: version, display_name: '测试微信', description: 'isolated', execution_target: 'local_required', entrypoint: ['dist/cli.js', 'mcp', '--stdio'], tools, schema_digest: 'existing',
    ...(declared === false ? {} : { tool_declaration: declared === true ? { version: 1, tools: tools.map(tool => ({ name: tool.name })) } : declared }),
    distribution: { platform: 'win32', arch: 'x64', node: { version: '22.23.3', modules_abi: '127' }, required_files: [{ path: 'dist/cli.js', size: entry.length, sha256: sha256(entry) }] } }
  const payload = await zip([['runtime-manifest.json', canonicalJson(manifest)], ['dist/cli.js', entry]])
  const unsigned = { envelope_version: 1, provider_release_id: release, provider_id: provider, provider_version: version, platform: 'win32', arch: 'x64', package_format: 'zip', package_size: payload.length,
    package_digest: `sha256:${sha256(payload)}`, manifest_digest: `sha256:${sha256(canonicalJson(manifest))}`, min_runtime_version: '0.2.14', max_runtime_version: null, publisher_key_id: 'isolated-store-test', signature_algorithm: 'Ed25519', created_at: '2026-10-09T00:00:00Z' }
  const envelope = { ...unsigned, signature: 'base64:' + sign(null, Buffer.from(canonicalJson(unsigned)), keys.privateKey).toString('base64') }
  return zip([['envelope.json', JSON.stringify(envelope)], ['payload.zip', payload]])
}
function fixture(t, probe = async () => ({ ready: true }), extraProviders = null) {
  const home = mkdtempSync(join(tmpdir(), 'aid-store-test-'))
  t.after(() => { assert.equal(dirname(resolve(home)), resolve(tmpdir())); assert.ok(home.includes('aid-store-test-')); rmSync(home, { recursive: true, force: true }) })
  const trustList = extraProviders ? [{ ...trust[0], providers: [...trust[0].providers, ...extraProviders] }] : trust
  const options = { home, platform, trust: trustList, ...(extraProviders ? { providers: [...trust[0].providers, ...extraProviders] } : {}), probe }
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

test('starting verifies an unchanged archive and current payload without repeating a trusted version probe', async t => {
  const verifiedVersion = [], timing = []
  let ready = true
  const { home, store, options } = fixture(t, async (_path, _manifest, context) => {
    verifiedVersion.push(context?.versionVerified === true)
    return { ready }
  })
  options.onCheckTiming = event => timing.push(event)
  await store.initialize(); await store.import(await pack())
  const restarted = new PluginStore(options); await restarted.initialize()
  ready = false
  await restarted.refresh()
  assert.deepEqual(verifiedVersion, [false, false, true], 'Readiness runs again but only the same verified entry can reuse its version')
  assert.deepEqual(timing.map(({ pass, phase, succeeded }) => [pass, phase, succeeded]), [
    ['initialize', 'archive_read', true], ['initialize', 'archive_verify', true], ['initialize', 'payload_verify', true],
    ['refresh', 'archive_read', true], ['refresh', 'archive_digest', true], ['refresh', 'payload_verify', true],
  ])
  assert.equal(restarted.list()[0].ready, false)
  assert.ok(restarted.resolveEntries({}).weixin)

  const entry = restarted.resolveEntries({}).weixin
  const original = statSync(entry)
  writeFileSync(entry, 'export []')
  utimesSync(entry, original.atime, original.mtime)
  assert.equal(statSync(entry).size, original.size, 'Unchanged metadata must not hide altered executable bytes')
  await restarted.refresh()
  assert.deepEqual(restarted.resolveEntries({ weixin: 'legacy' }), {}, 'Tampering before a delayed start must block execution including legacy fallback')
  assert.deepEqual(restarted.declaredPlugins(), [], 'The updated heartbeat must not advertise an invalid plugin')
  assert.equal(verifiedVersion.length, 3, 'A damaged plugin must not reach even its read-only doctor')

  const { records } = JSON.parse(readFileSync(join(home, 'plugins/inventory.json'), 'utf8'))
  writeFileSync(entry, 'export {}')
  await restarted.refresh()
  assert.equal(verifiedVersion.at(-1), false, 'Recovery must re-establish trust before reuse')
  const archive = join(home, 'plugins/releases', records[0].directory, 'archive.zip')
  const changed = Buffer.from(readFileSync(archive)); changed[0] ^= 1
  writeFileSync(archive, changed)
  await restarted.refresh()
  assert.deepEqual(restarted.resolveEntries({}), {}, 'Even an unchanged installed payload requires an intact archived package')
  assert.equal(timing.at(-1).phase, 'archive_digest')
  assert.equal(timing.at(-1).succeeded, false)
})

test('the current release owns the proof and withdrawing its publisher trust removes its runnable entry', async t => {
  const probes = []
  const { options, store } = fixture(t, async (_root, manifest, context) => {
    probes.push([manifest.provider_version, context?.versionVerified === true]); return { ready: true }
  })
  await store.initialize(); await store.import(await pack())
  await store.refresh()
  await store.import(await pack({ version: '2.0.0', release: 'replacement' }))
  await store.refresh()
  assert.deepEqual(probes, [['1.0.0', false], ['1.0.0', true], ['2.0.0', false], ['2.0.0', true]], 'A replacement must establish its own proof before any reuse')
  options.trust = []
  await store.refresh()
  assert.deepEqual(store.declaredPlugins(), [], 'An unchanged archive cannot reuse a proof from different publisher inputs')
  assert.deepEqual(store.resolveEntries({ weixin: 'legacy' }), {})
  assert.equal(probes.length, 4, 'Rechecking publisher trust happens before attempting to run the plugin')
})

test('a slow background file read shares admission with the same plugin and leaves another plugin available', { timeout: 5000 }, async t => {
  const timing = [], probes = []
  const { store, options } = fixture(t)
  await store.initialize()
  await store.import(await pack())
  await store.import(await pack({ provider: 'ai.aidwork.wecom' }))
  const reopened = new PluginStore({ ...options, deferStartupChecks: true, onCheckTiming: value => timing.push(value),
    probe: async (_path, manifest, context) => {
      probes.push([manifest.provider_id, context.versionVerified])
      return { ready: manifest.provider_id === 'ai.aidwork.wecom', reason: 'fixture readiness' }
    } })
  await reopened.initialize()
  assert.equal(reopened.declaredPlugins().length, 2)
  assert.deepEqual(timing.map(({ pass, phase }) => [pass, phase]), [
    ['metadata', 'archive_envelope'], ['metadata', 'manifest'], ['metadata', 'archive_envelope'], ['metadata', 'manifest'],
  ])
  assert.deepEqual(probes, [], 'The signed catalog can be advertised without running any plugin code')
  assert.ok(reopened.list().every(item => !item.ready && item.reason === '等待检查插件'))
  const entries = reopened.resolveEntries({})
  let releaseWeixin, enterWeixin
  const waitForWeixin = new Promise(resolve => { releaseWeixin = resolve })
  const weixinReachedRead = new Promise(resolve => { enterWeixin = resolve })
  const read = filePromises.readFile
  const slowRead = t.mock.method(filePromises, 'readFile', async (path, ...args) => {
    if (path === entries.weixin) { enterWeixin(); await waitForWeixin }
    return read(path, ...args)
  })
  syncBuiltinESMExports()
  const background = reopened.verifyEnabledPlugins(new AbortController().signal)
  let firstUse
  let timeout
  try {
    await Promise.race([weixinReachedRead, new Promise((_, reject) => {
      timeout = setTimeout(() => reject(new Error('first-use verification must read payload files asynchronously')), 2500)
    })])
    assert.deepEqual(probes, [], 'The background queue must check only one plugin at a time')
    firstUse = reopened.verifyForExecution('weixin', entries.weixin)
    await reopened.verifyForExecution('wecom', entries.wecom)
    assert.equal(reopened.list().find(item => item.plugin_id === 'ai.aidwork.wecom').ready, true)
    assert.deepEqual(probes, [['ai.aidwork.wecom', false]], 'A slow payload read on one plugin cannot block another plugin')
  } finally {
    clearTimeout(timeout)
    releaseWeixin()
    try { await Promise.all([background, firstUse]) } finally { slowRead.mock.restore(); syncBuiltinESMExports() }
  }
  await reopened.verifyForExecution('weixin', entries.weixin, true)
  assert.deepEqual(probes, [['ai.aidwork.wecom', false], ['ai.aidwork.weixin', false]], 'Parallel and subsequent callers share the same full proof')
  assert.equal(reopened.declaredPlugins().length, 2, 'Authentic but not logged in remains visible for diagnostics')
  assert.deepEqual(timing.filter(item => item.phase === 'payload_verify').map(item => [item.pluginId, item.pass]), [
    ['ai.aidwork.wecom', 'first_use'], ['ai.aidwork.weixin', 'background'],
  ], 'Only one full file check runs per plugin, whether the waiting caller is a task or the background queue')
  const beforeStop = timing.length
  reopened.resetExecutionChecks()
  assert.equal(timing.length, beforeStop, 'Stopping execution must not scan or run plugin files')
  assert.ok(reopened.list().every(item => !item.ready && item.reason === '等待检查插件'))
  writeFileSync(entries.weixin, 'export []')
  await assert.rejects(reopened.verifyForExecution('weixin', entries.weixin), /完整检查/, 'A new execution window cannot reuse a proof that preceded the stopped interval')
  assert.equal(probes.length, 2, 'The changed file must be rejected before executing plugin diagnostics')
})

test('stopping the background leaves no false failure or queued check in the next execution window', { timeout: 5000 }, async t => {
  const { store, options } = fixture(t)
  await store.initialize()
  await store.import(await pack())
  await store.import(await pack({ provider: 'ai.aidwork.wecom' }))
  const started = Promise.withResolvers(), finish = Promise.withResolvers()
  const probes = [], timing = []
  const reopened = new PluginStore({ ...options, deferStartupChecks: true, onCheckTiming: event => timing.push(event),
    async probe(_path, manifest, context) {
      const provider = manifest.provider_id
      await context.measure('version', async () => {
        probes.push([provider, 'version'])
        if (probes.length === 1) { started.resolve(); await finish.promise }
      })
      await context.measure('doctor', async () => { probes.push([provider, 'doctor']) })
      return { ready: true }
    },
  })
  await reopened.initialize()
  const window = new AbortController()
  const background = reopened.verifyEnabledPlugins(window.signal)
  await started.promise
  window.abort(); finish.resolve()
  await background
  assert.deepEqual(probes, [['ai.aidwork.weixin', 'version']], 'Stopping does not start the next phase or the next plugin')
  assert.equal(reopened.declaredPlugins().length, 2, 'Cancellation does not withdraw trusted dispatch metadata')
  assert.ok(reopened.list().every(item => item.reason === '等待检查插件'))
  assert.ok(timing.every(item => item.succeeded), 'Cancellation is not logged as integrity failure')

  reopened.resetExecutionChecks()
  await reopened.verifyEnabledPlugins(new AbortController().signal)
  assert.ok(reopened.list().every(item => item.ready), 'The next window establishes its own complete proofs')
  assert.deepEqual(probes.slice(1), [
    ['ai.aidwork.weixin', 'version'], ['ai.aidwork.weixin', 'doctor'],
    ['ai.aidwork.wecom', 'version'], ['ai.aidwork.wecom', 'doctor'],
  ])
})

test('already accepted work retries if it joins a background check while cancellation is settling', { timeout: 5000 }, async t => {
  const { store, options } = fixture(t)
  await store.initialize(); await store.import(await pack())
  const window = new AbortController()
  let probes = 0, invocation
  const reopened = new PluginStore({ ...options, deferStartupChecks: true, async probe(_path, _manifest, context) {
    if (++probes === 1) {
      window.abort()
      try { await context.measure('doctor', async () => assert.fail('cancelled work must not start doctor')) }
      catch (error) {
        invocation = reopened.verifyForExecution('weixin', reopened.resolveEntries({}).weixin)
        throw error
      }
    }
    return { ready: true }
  } })
  await reopened.initialize()
  await reopened.verifyEnabledPlugins(window.signal)
  assert.ok(invocation, 'The already accepted task encounters the cancelling background check')
  await invocation
  assert.equal(probes, 2)
  assert.equal(reopened.list()[0].ready, true)
})

test('a background check withdraws a damaged plugin and verifies the remaining eligible plugins', async t => {
  const { store, home, options } = fixture(t)
  await store.initialize()
  const signed = await pack()
  await store.import(signed)
  await store.import(await pack({ provider: 'ai.aidwork.wecom' }))
  await store.import(await pack({ provider: 'ai.aidwork.boss-recruiting' }))
  const entries = store.resolveEntries({})
  writeFileSync(entries.weixin, 'export []')
  const fileMeta = JSON.parse(readFileSync(join(home, 'plugins/inventory.json'), 'utf8')).records.find(item => item.plugin_id === 'ai.aidwork.wecom')
  const wecomManifest = join(home, 'plugins/releases', fileMeta.directory, 'payload/runtime-manifest.json')
  const manifest = readFileSync(wecomManifest, 'utf8')
  writeFileSync(wecomManifest, manifest.replace('weixin_status', 'wecom_tamper'))
  const probes = []
  const reopened = new PluginStore({ ...options, deferStartupChecks: true, probe: async (_root, manifest) => { probes.push(manifest.provider_id); return { ready: true } } })
  await reopened.initialize()
  assert.deepEqual(reopened.declaredPlugins().map(item => item.provider_key), ['weixin', 'boss-recruiting'], 'The altered manifest never enters the signed catalog')
  await reopened.verifyEnabledPlugins(new AbortController().signal)
  await assert.rejects(reopened.verifyForExecution('weixin', entries.weixin), /完整检查/)
  assert.deepEqual(reopened.declaredPlugins().map(item => item.provider_key), ['boss-recruiting'], 'The failed payload is withdrawn from every future dispatch snapshot')
  assert.deepEqual(reopened.resolveEntries({ weixin: 'legacy', wecom: 'legacy' }), { 'boss-recruiting': entries['boss-recruiting'] })
  assert.deepEqual(probes, ['ai.aidwork.boss-recruiting'], 'A damaged file or manifest cannot even execute version or doctor')
  await reopened.import(signed)
  const repaired = reopened.resolveEntries({}).weixin
  assert.notEqual(repaired, entries.weixin, 'A repaired release gets a new independent proof')
  await assert.rejects(reopened.verifyForExecution('weixin', entries.weixin), /完整检查/)
  await reopened.verifyForExecution('weixin', repaired)
  assert.deepEqual(probes, ['ai.aidwork.boss-recruiting', 'ai.aidwork.weixin'])
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
test('old CLI without a scheduling declaration shows 需要升级插件 and is excluded from execution resolution', async t => {
  const { store } = fixture(t)
  await store.initialize()
  await store.import(await pack({ declared: false }))
  const summary = store.list()[0]
  assert.equal(summary.enabled, true)
  assert.equal(summary.ready, false)
  assert.match(summary.reason, /需要升级插件（缺少调度声明）/)
  assert.deepEqual(store.resolveEntries({ weixin: 'legacy' }), {}, '无声明插件不进入执行解析，其他插件照常（legacy 直配也不复活）')
  assert.deepEqual(store.declaredPlugins(), [], '无声明插件不参与派生装配与并行上报')
  // 声明版本损坏的更高版本包照常验签安装，仍按无声明门禁显示
  await store.import(await pack({ version: '1.1.0', release: 'release-broken', declared: { version: 2, tools: [{ name: 'weixin_status' }] } }))
  assert.match(store.list()[0].reason, /需要升级插件（缺少调度声明）/)
  await store.import(await pack({ version: '1.2.0', release: 'release-2' }))
  assert.equal(store.list()[0].ready, true, '升级到带声明版本即恢复接入')
  assert.equal(store.list()[0].reason, undefined)
  assert.ok(store.resolveEntries({}).weixin)
  assert.deepEqual(store.declaredPlugins().map(plugin => plugin.plugin_id), ['ai.aidwork.weixin'])
})
test('damaged declaration is treated as no declaration, not a rejection', async t => {
  const { store } = fixture(t)
  await store.initialize()
  // 声明引用未知工具名：验签不拒包（宽松解析），但按无声明处理
  await store.import(await pack({ declared: { version: 1, tools: [{ name: 'weixin_missing' }] } }))
  assert.match(store.list()[0].reason, /需要升级插件（缺少调度声明）/)
  assert.deepEqual(store.resolveEntries({}), {})
})
test('runtime-unknown plugin joins execution from its own declaration without runtime source knowledge', async t => {
  const { store } = fixture(t, async () => ({ ready: true }), ['ai.aidwork.test-tool'])
  await store.initialize()
  await store.import(await pack({ provider: 'ai.aidwork.test-tool', tools: [
    { name: 'test_tool_status', inputSchema: { type: 'object' }, annotations: { readOnlyHint: true } },
  ] }))
  assert.equal(store.list()[0].plugin_id, 'ai.aidwork.test-tool')
  assert.equal(store.list()[0].ready, true)
  const entry = store.resolveEntries({})['test-tool']
  assert.ok(entry, '未知插件按前缀派生 provider key 并进入执行解析')
  const declared = store.declaredPlugins()
  assert.deepEqual(declared.map(plugin => plugin.provider_key), ['test-tool'])
  const derived = deriveProviderManifest(declared[0].manifest)
  assert.equal(derived.provider_id, 'ai.aidwork.test-tool')
  assert.deepEqual([...derived.tools], ['test_tool_status'])
  assert.equal(isToolAllowedFor(derived, 'test_tool_status'), true, '派生 manifest 可路由调用')
  assert.equal(isWriteToolFor(derived, 'test_tool_status'), false, '注解只读派生为读工具')
  assert.ok(entry, 'entry 指向签名包 payload 入口')
})

test('安装白名单：默认构造拒绝第 4 方身份；信任配置含第 4 方身份（providers 同源派生）即安装成功', async t => {
  const fourth = 'ai.aidwork.resume-tool'
  const { store, options } = fixture(t, async () => ({ ready: true }), [fourth])
  await store.initialize()
  // 默认名单（FIRST_PARTY_PROVIDERS 三方）不放行第 4 方身份（镜像 runtimeHost 无信任配置回退）
  const defaultStore = new PluginStore({ ...options, providers: undefined })
  await defaultStore.initialize()
  await assert.rejects(async () => defaultStore.import(await pack({ provider: fourth, declared: false })), error => error.code === 3)
  // 信任配置含第 4 方身份（trust root providers + 安装白名单同源派生，镜像 runtimeHost 装配）：安装成功
  await store.import(await pack({ provider: fourth, tools: [
    { name: 'resume_parse', inputSchema: { type: 'object' }, annotations: { readOnlyHint: true } },
  ] }))
  assert.equal(store.list()[0].plugin_id, fourth)
  assert.equal(store.list()[0].ready, true, '带声明的新身份插件安装即就绪')
})
