import assert from 'node:assert/strict'
import { generateKeyPairSync, sign } from 'node:crypto'
import { test } from 'node:test'
import yazl from 'yazl'
import { verifyOfflinePackage, readSafeZip, sha256, canonicalJson, safePackagePath, PACKAGE_LIMITS } from '../dist/plugins/package.js'

const pair = generateKeyPairSync('ed25519')
const trust = [{ key_id: 'isolated-test', public_key: pair.publicKey.export({ type: 'spki', format: 'pem' }), providers: ['ai.aidwork.weixin'] }]
const platform = { platform: 'win32', arch: 'x64', node_version: '22.23.3', node_abi: '127', runtime_version: '0.2.14' }
const zip = entries => new Promise((resolve, reject) => {
  const file = new yazl.ZipFile(); const chunks = []
  file.outputStream.on('data', chunk => chunks.push(chunk)); file.outputStream.on('end', () => resolve(Buffer.concat(chunks))); file.outputStream.on('error', reject)
  for (const [name, bytes, mode] of entries) file.addBuffer(Buffer.from(bytes), name, { mode: mode ?? 0o100644 })
  file.end()
})
async function archive(options = {}) {
  const entry = Buffer.from('export {}')
  const manifest = { provider_id: 'ai.aidwork.weixin', provider_version: '1.0.0', manifest_version: 1, display_name: '微信', description: 'isolated test',
    protocol: 'mcp', transport: 'stdio', execution_target: 'local_required', entrypoint: ['dist/cli.js', 'mcp', '--stdio'], tools: [{ name: 'weixin_status', inputSchema: { type: 'object' } }], schema_digest: 'sha256:existing-schema',
    distribution: { platform: 'win32', arch: 'x64', node: { version: '22.23.3', modules_abi: '127' }, required_files: [{ path: 'dist/cli.js', size: entry.length, sha256: sha256(entry) }] }, ...options.manifest }
  const payload = options.payload ?? await zip([['runtime-manifest.json', canonicalJson(manifest)], ['dist/cli.js', entry], ...options.extraFiles ?? []])
  const unsigned = { envelope_version: 1, provider_release_id: 'isolated-1', provider_id: manifest.provider_id, provider_version: manifest.provider_version,
    platform: 'win32', arch: 'x64', package_format: 'zip', package_size: payload.length, package_digest: `sha256:${sha256(payload)}`,
    manifest_digest: `sha256:${sha256(canonicalJson(manifest))}`, min_runtime_version: '0.2.14', max_runtime_version: null,
    publisher_key_id: 'isolated-test', signature_algorithm: 'Ed25519', created_at: '2026-10-09T00:00:00Z', ...options.envelope }
  const envelope = { ...unsigned, signature: 'base64:' + sign(null, Buffer.from(canonicalJson(unsigned)), pair.privateKey).toString('base64') }
  return zip([['envelope.json', options.envelopeBytes ?? JSON.stringify(envelope)], ['payload.zip', payload]])
}

test('official signed offline release verifies exact Node/ABI and every runtime file', async () => {
  const result = await verifyOfflinePackage(await archive(), trust, platform)
  assert.equal(result.manifest.provider_id, 'ai.aidwork.weixin'); assert.equal(result.files.size, 2)
})
test('package-provided key cannot authorize a publisher or a different Provider', async () => {
  const bytes = await archive()
  await assert.rejects(verifyOfflinePackage(bytes, [], platform), error => error.code === 8)
  await assert.rejects(verifyOfflinePackage(bytes, [{ ...trust[0], providers: ['ai.aidwork.wecom'] }], platform), error => error.code === 8)
  const other = generateKeyPairSync('ed25519')
  await assert.rejects(verifyOfflinePackage(bytes, [{ ...trust[0], public_key: other.publicKey.export({ type: 'spki', format: 'pem' }) }], platform), error => error.code === 8)
  await assert.rejects(verifyOfflinePackage(bytes, [{ ...trust[0], public_key: pair.privateKey.export({ type: 'pkcs8', format: 'pem' }) }], platform), error => error.code === 8, 'An otherwise valid signer private key must not be accepted as a public trust asset')
})
test('signed release cannot omit dependencies, add undeclared code or use incompatible ABI', async () => {
  await assert.rejects(verifyOfflinePackage(await archive({ extraFiles: [['hidden.js', 'extra executable']] }), trust, platform), error => error.code === 8)
  await assert.rejects(verifyOfflinePackage(await archive(), trust, { ...platform, node_abi: '137' }), error => error.code === 8)
  await assert.rejects(verifyOfflinePackage(await archive({ envelope: { package_digest: 'sha256:' + '0'.repeat(64) } }), trust, platform), error => error.code === 8)
})
test('unsupported third-party identity is rejected before payload parsing or execution', async () => {
  await assert.rejects(verifyOfflinePackage(await archive({ manifest: { provider_id: 'third-party.skill' }, payload: Buffer.from('not even a ZIP') }), trust, platform), error => error.code === 3)
})
test('duplicate JSON names and malformed Unicode cannot define signed input ambiguously', async () => {
  await assert.rejects(verifyOfflinePackage(await archive({ envelopeBytes: '{"envelope_version":1,"envelope_version":1}' }), trust, platform), error => error.code === 8)
  assert.throws(() => canonicalJson({ name: '\ud800' }), error => error.code === 8)
})
test('Windows aliases, traversal, alternate streams, links and case collisions never reach extraction', async () => {
  for (const name of ['../x', 'C:/x', 'dir\\x', 'dir/CON.txt', 'x:stream', 'x.', 'x ', '/x', 'dir//x']) assert.throws(() => safePackagePath(name), error => error.code === 8)
  await assert.rejects(readSafeZip(await zip([['Link', 'target', 0o120777]])), error => error.code === 8)
  await assert.rejects(readSafeZip(await zip([['Dir/File.js', '1'], ['dir/file.js', '2']])), error => error.code === 8)
  await assert.rejects(readSafeZip(await zip([['dir', '1'], ['dir/file.js', '2']])), error => error.code === 8)
})
test('entry, per-file, total-inflated and compression-ratio budgets bound hostile ZIPs', async () => {
  const bytes = await zip([['one', '12345'], ['two', '67890']])
  for (const limit of [{ entries: 1 }, { file: 4 }, { expanded: 9 }]) await assert.rejects(readSafeZip(bytes, { ...PACKAGE_LIMITS, ...limit }), error => error.code === 8)
  await assert.rejects(readSafeZip(await zip([['bomb', Buffer.alloc(2 * 1024 ** 2)]])), error => error.code === 8)
})
