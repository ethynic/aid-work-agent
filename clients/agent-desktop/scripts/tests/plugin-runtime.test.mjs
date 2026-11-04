import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { createOfflinePackage, sha256, zipBytes } from '../../../runtime-plugin-packaging/package.mjs'
import { verifyPluginRuntime, WECOM_BUSINESS_TOOLS } from '../verify-plugin-runtime.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../../..')

test('installation preflight uses the target public roots and Host tools, not the package MCP count', async () => {
  const home = mkdtempSync(join(tmpdir(), 'aid-plugin-runtime-'))
  try {
    const core = join(home, 'host/node_modules/@aid/local-tool-host-core')
    mkdirSync(join(core, 'dist/plugins'), { recursive: true }); mkdirSync(join(core, 'dist/legacy'))
    mkdirSync(join(home, 'node/win-x64'), { recursive: true })
    copyFileSync(process.execPath, join(home, 'node/win-x64/node.exe'))
    writeFileSync(join(core, 'package.json'), JSON.stringify({ type: 'module', version: '0.2.10' }))
    writeFileSync(join(home, 'host/package.json'), JSON.stringify({ version: '0.2.25' }))
    const verifier = pathToFileURL(join(root, 'clients/shared/local-tool-host-core/dist/plugins/package.js')).href
    writeFileSync(join(core, 'dist/plugins/package.js'), `export { verifyOfflinePackage } from ${JSON.stringify(verifier)}`)
    const config = pathToFileURL(join(root, 'clients/shared/local-tool-host-core/dist/legacy/config.js')).href
    writeFileSync(join(core, 'dist/legacy/config.js'), `export { RUNTIME_VERSION } from ${JSON.stringify(config)}`)
    const allowlist = tools => writeFileSync(join(core, 'dist/legacy/providers.js'), `export const getProviderManifest=()=>({tools:${JSON.stringify(tools)}})`)
    allowlist(WECOM_BUSINESS_TOOLS)
    const pair = generateKeyPairSync('ed25519')
    const trust = { key_id: 'anonymous-fixture', public_key: pair.publicKey.export({ format: 'pem', type: 'spki' }), providers: ['ai.aidwork.wecom'] }
    const cli = Buffer.from('// no execution')
    const manifest = { manifest_version: 1, provider_id: 'ai.aidwork.wecom', provider_version: '1.2.3', display_name: 'Fixture', description: '', schema_digest: 'fixture', tools: WECOM_BUSINESS_TOOLS.map(name => ({ name })), execution_target: 'local_required', entrypoint: ['index.js', 'mcp', '--stdio'], distribution: { platform: process.platform, arch: process.arch, node: { version: process.versions.node, modules_abi: process.versions.modules }, required_files: [{ path: 'index.js', size: cli.length, sha256: sha256(cli) }] } }
    const payload = await zipBytes([['runtime-manifest.json', Buffer.from(JSON.stringify(manifest))], ['index.js', cli]])
    const built = await createOfflinePackage(payload, manifest, { release_id: 'fixture-1', key_id: trust.key_id, created_at: '2026-11-02T00:00:00Z' }, pair.privateKey.export({ format: 'pem', type: 'pkcs8' }))
    const pkg = join(home, 'wecom.aidplugin.zip'); writeFileSync(pkg, built.archive)
    const roots = join(home, 'trust-roots.json')
    writeFileSync(roots, JSON.stringify({ roots: [] }))
    await assert.rejects(verifyPluginRuntime(home, pkg), 'An independently valid plugin cannot install on a target that does not trust its publisher')
    writeFileSync(roots, JSON.stringify({ roots: [trust] }))
    const before = readFileSync(pkg)
    const report = await verifyPluginRuntime(home, pkg)
    assert.equal(report.result, 'WECOM_TARGET_RUNTIME_PASS')
    const { RUNTIME_VERSION } = await import(config)
    assert.equal(report.runtimeVersion, RUNTIME_VERSION, 'Import compatibility follows the actual RuntimeHost-consumed export, rather than guessing from Host/Core package metadata')
    assert.equal(report.hostVersion, '0.2.25')
    assert.equal(report.coreVersion, '0.2.10')
    assert.deepEqual(readFileSync(pkg), before, 'Preflight must not extract/import or rewrite signed bytes')
    // A separate resource tree models an old Host whose allowlist lacks the new directory tool.
    const old = join(home, 'old'); mkdirSync(old)
    const { cpSync } = await import('node:fs'); cpSync(join(home, 'host'), join(old, 'host'), { recursive: true })
    cpSync(join(home, 'node'), join(old, 'node'), { recursive: true }); cpSync(roots, join(old, 'trust-roots.json'))
    writeFileSync(join(old, 'host/node_modules/@aid/local-tool-host-core/dist/legacy/providers.js'), `export const getProviderManifest=()=>({tools:['wecom_read_session','wecom_read_sessions','wecom_message_send']})`)
    await assert.rejects(verifyPluginRuntime(old, pkg), /wecom_session_directory/)
  } finally { rmSync(home, { recursive: true, force: true }) }
})
