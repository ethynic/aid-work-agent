import test from 'node:test'
import assert from 'node:assert/strict'
import { generateKeyPairSync, randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { prepareRuntimeInstallation, rotateRuntimeConnection, selectRuntimeConnection } from '../electron/runtimeInstallation.js'

function fixture() {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-installation-'))
  const legacyHome = path.join(root, 'legacy'), userDataPath = path.join(root, 'user'), hostEntry = path.join(root, 'installed/resources/runtime/host/managed-entry.js')
  mkdirSync(legacyHome); mkdirSync(path.dirname(hostEntry), { recursive: true }); writeFileSync(hostEntry, 'trusted fixture')
  let stops = 0
  const protect = async (v: string) => Buffer.from(v).toString('base64')
  const unprotect = async (v: string) => { if (v === 'corrupt') throw new Error('DPAPI invalid'); return Buffer.from(v, 'base64').toString() }
  const config = { server: 'https://enterprise.example/', device_id: randomUUID(), name: 'test' }
  const options = { legacyHome, userDataPath, hostEntry, appId: 'cn.aidingyi.agent.runtime.acceptance', node: process.execPath,
    stopLegacy: async () => { stops++ }, protect, unprotect, verifyIdentity: async () => true, migratePlugins: async () => {} }
  return { root, options, config, protect, get stops() { return stops }, clean() { rmSync(root, { recursive: true, force: true }) } }
}
test('valid credentials migrate once, while ten types of legacy state remain isolated', async () => {
  const f = fixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify({ ...f.config, providers: { unknown: { entry: 'untrusted' } }, sessionTasks: true }))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    for (const name of ['management-secret.bin', 'management-operations.json', 'management-retired-request-keys.bin', 'host-running.json', 'journal', 'outbox', 'session-tasks', 'process-supervision', 'pairing-pending.bin', 'invocation-fences']) writeFileSync(path.join(f.options.legacyHome, name), 'corrupt')
    const installed = await prepareRuntimeInstallation(f.options)
    assert.equal(installed.migrated, true)
    assert.equal(installed.fresh, true)
    assert.deepEqual(readdirSync(installed.home).sort(), ['config.json','credentials.bin'])
    assert.deepEqual(JSON.parse(readFileSync(path.join(installed.home, 'config.json'), 'utf8')), f.config)
    assert.equal(f.stops, 1)
    const reopened = await prepareRuntimeInstallation(f.options)
    assert.equal(reopened.home, installed.home)
    assert.equal(reopened.fresh, false)
    assert.equal(f.stops, 1)
  } finally { f.clean() }
})
test('broken config or DPAPI and authentication rejection produce a clean usable home', async () => {
  for (const fault of ['config','token','decrypted_bad_token','revoked']) {
    const f = fixture()
    try {
      writeFileSync(path.join(f.options.legacyHome, 'config.json'), fault === 'config' ? '{corrupt' : JSON.stringify(f.config))
      writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), fault === 'token' ? 'corrupt' : await f.protect(fault === 'decrypted_bad_token' ? 'bad\nheader' : 'a'.repeat(64)))
      f.options.verifyIdentity = async () => fault !== 'revoked'
      const installed = await prepareRuntimeInstallation(f.options)
      assert.equal(installed.migrated, false)
      assert.deepEqual(readdirSync(installed.home), [])
      assert.equal(existsSync(f.options.legacyHome), false)
      if (fault === 'revoked') assert.equal(existsSync(path.join(installed.metadataHome, 'previous-identity.bin')), true)
    } finally { f.clean() }
  }
})
test('previous paired registration credentials win without activating old resource recovery', async () => {
  const f = fixture()
  try {
    const staging = f.options.legacyHome + '.connection-registration'; mkdirSync(staging)
    writeFileSync(path.join(staging, 'state.bin'), await f.protect(JSON.stringify({ version: 1, home: f.options.legacyHome, phase: 'paired', config: f.config, token: 'b'.repeat(64), replacement_pending: true })))
    const installed = await prepareRuntimeInstallation(f.options)
    assert.equal(installed.migrated, true)
    assert.deepEqual(JSON.parse(readFileSync(path.join(installed.home, 'config.json'), 'utf8')), f.config)
    assert.equal(existsSync(path.join(installed.metadataHome, 'legacy-replacement-pending.json')), true)
  } finally { f.clean() }
})
test('bad staged token falls back to the valid old identity without marking replacement pending', async () => {
  const f = fixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    const staging = f.options.legacyHome + '.connection-registration'; mkdirSync(staging)
    writeFileSync(path.join(staging, 'state.bin'), await f.protect(JSON.stringify({ version: 1, home: f.options.legacyHome, phase: 'paired', config: { ...f.config, device_id: randomUUID() }, token: 'bad-token', replacement_pending: true })))
    const installed = await prepareRuntimeInstallation(f.options)
    assert.equal(installed.migrated, true)
    assert.deepEqual(JSON.parse(readFileSync(path.join(installed.home, 'config.json'), 'utf8')), f.config)
    assert.equal(existsSync(path.join(installed.metadataHome, 'legacy-replacement-pending.json')), false)
  } finally { f.clean() }
})
test('new pair rotates connection, retains stable pending transaction and rejects foreign homes', async () => {
  const f = fixture()
  try {
    const installed = await prepareRuntimeInstallation(f.options)
    writeFileSync(path.join(installed.metadataHome, 'clean-registration.bin'), 'protected unknown request')
    writeFileSync(path.join(installed.home, 'journal'), 'corrupt')
    const home = await rotateRuntimeConnection(f.options, installed.home)
    assert.notEqual(home, installed.home); assert.deepEqual(readdirSync(home), [])
    assert.equal(readFileSync(path.join(installed.metadataHome, 'clean-registration.bin'), 'utf8'), 'protected unknown request')
    assert.equal(await selectRuntimeConnection(f.options, home), home)
    await assert.rejects(selectRuntimeConnection(f.options, f.options.legacyHome), /登记无效/)
    await assert.rejects(rotateRuntimeConnection(f.options, installed.home), /另一个客户端/)
  } finally { f.clean() }
})
test('installer generation changes invalidate previously registered executable tickets', async () => {
  const f = fixture()
  try {
    const first = await prepareRuntimeInstallation(f.options)
    const secondId = randomUUID(), secondHome = path.join(first.metadataHome, 'generations', secondId, 'connections', randomUUID())
    mkdirSync(secondHome, { recursive: true })
    writeFileSync(path.join(first.metadataHome, 'current.json'), JSON.stringify({ version: 2, id: secondId, home: secondHome, priorHome: first.home, initialized: false }))
    await assert.rejects(prepareRuntimeInstallation(f.options), /已被新版替代/)
    writeFileSync(path.resolve(path.dirname(f.options.hostEntry), '../../../runtime-installation.id'), secondId)
    const second = await prepareRuntimeInstallation(f.options)
    assert.equal(second.home, secondHome); assert.equal(f.stops, 2)
    await assert.rejects(selectRuntimeConnection(f.options, path.join(f.root, 'foreign-home')), /登记无效/)
  } finally { f.clean() }
})
test('compatible binary replacement retains the same home, device, plugin state and unacknowledged results', async () => {
  const f = fixture(); let migrations = 0
  f.options.migratePlugins = async () => { migrations++ }
  try {
    const first = await prepareRuntimeInstallation(f.options)
    const facts = new Map([
      ['config.json', JSON.stringify({ ...f.config, providers: { 'boss-recruiting': { entry: 'retained-boss-entry' } }, sessionTasks: true })],
      ['credentials.bin', await f.protect('a'.repeat(64))], ['plugins/inventory.json', '{"version":1,"records":[]}'],
      ['journal/old.jsonl', '{"invocation_id":"old","phase":"may_have_started"}'], ['result-outbox/old.json', '{"invocation_id":"old","effect":"unknown"}'],
    ])
    for (const [name, value] of facts) {
      const file = path.join(first.home, name); mkdirSync(path.dirname(file), { recursive: true }); writeFileSync(file, value)
    }
    writeFileSync(f.options.hostEntry, 'trusted newer binary')
    const upgraded = await prepareRuntimeInstallation(f.options)
    assert.equal(upgraded.home, first.home); assert.equal(upgraded.installationId, first.installationId)
    assert.equal(upgraded.fresh, false); assert.equal(f.stops, 1); assert.equal(migrations, 1)
    for (const [name, value] of facts) assert.equal(readFileSync(path.join(upgraded.home, name), 'utf8'), value)
  } finally { f.clean() }
})
test('another installer account changing the machine-wide stamp does not migrate this user paired home', async () => {
  const f = fixture()
  try {
    const first = await prepareRuntimeInstallation(f.options)
    mkdirSync(path.join(first.home, 'result-outbox'))
    writeFileSync(path.join(first.home, 'result-outbox/old.json'), 'original unacknowledged result')
    writeFileSync(path.resolve(path.dirname(f.options.hostEntry), '../../../runtime-installation.id'), randomUUID())
    const updated = await prepareRuntimeInstallation(f.options)
    assert.equal(updated.home, first.home)
    assert.equal(updated.installationId, first.installationId)
    assert.equal(updated.fresh, false)
    assert.equal(readFileSync(path.join(first.home, 'result-outbox/old.json'), 'utf8'), 'original unacknowledged result')
    assert.equal(f.stops, 1, 'A compatible stamp change does not run legacy migration or replay old tasks')
  } finally { f.clean() }
})

test('default offline identity verification retains locally valid credentials', async t => {
  const f = fixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    t.mock.method(globalThis, 'fetch', async () => { throw new Error('isolated temporary network failure') })
    const installed = await prepareRuntimeInstallation({ ...f.options, verifyIdentity: undefined })
    assert.equal(installed.migrated, true, 'A transport failure does not establish that the device was revoked')
    assert.deepEqual(JSON.parse(readFileSync(path.join(installed.home, 'config.json'), 'utf8')), f.config)
    assert.equal(await f.options.unprotect(readFileSync(path.join(installed.home, 'credentials.bin'), 'utf8')), 'a'.repeat(64))
  } finally { f.clean() }
})
test('real Finalize and Main preparation share upgrade and explicit clean-reinstall semantics without touching live installation', { skip: process.platform !== 'win32' }, async () => {
  const id = randomUUID(), root = path.join(os.tmpdir(), `aid-runtime-install-fixture-${id}`)
  const appData = path.join(root, 'appData'), install = path.join(root, 'install')
  const f = fixture()
  try {
    mkdirSync(appData, { recursive: true }); mkdirSync(install)
    const options = { ...f.options, userDataPath: path.join(appData, 'aidwork-runtime-app-acceptance'), legacyHome: path.join(appData, 'aidwork-tool-runtime-acceptance'), hostEntry: path.join(install, 'resources/runtime/host/managed-entry.js') }
    mkdirSync(options.legacyHome); mkdirSync(path.dirname(options.hostEntry), { recursive: true }); writeFileSync(options.hostEntry, 'trusted fixture')
    writeFileSync(path.join(options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    const script = path.resolve('scripts/runtime-clean-install.ps1')
    const finalize = (clean = false) => {
      const installationId = execFileSync(path.join(process.env.WINDIR ?? 'C:/Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe'),
        ['-NoProfile', '-NonInteractive', '-File', script, '-Mode', 'Finalize', '-AppId', options.appId, '-InstallDirectory', install, '-FixtureRegistryId', id, ...(clean ? ['-Clean'] : [])],
        { encoding: 'utf8', windowsHide: true, timeout: 15_000, env: { ...process.env, APPDATA: appData } }).trim()
      writeFileSync(path.join(install, 'runtime-installation.id'), installationId)
      return installationId
    }
    const firstId = finalize(), first = await prepareRuntimeInstallation(options)
    assert.equal(first.home, path.join(options.userDataPath, 'rt'))
    mkdirSync(path.join(first.home, 'journal')); writeFileSync(path.join(first.home, 'journal/old.jsonl'), 'unknown original send')
    assert.equal(finalize(), firstId)
    const upgraded = await prepareRuntimeInstallation(options)
    assert.equal(upgraded.home, first.home); assert.equal(upgraded.fresh, false)
    assert.notEqual(finalize(true), firstId)
    const clean = await prepareRuntimeInstallation(options)
    assert.notEqual(clean.home, first.home); assert.equal(clean.fresh, true); assert.equal(clean.migrated, true)
    assert.deepEqual(JSON.parse(readFileSync(path.join(clean.home, 'config.json'), 'utf8')), f.config)
    assert.equal(readFileSync(path.join(first.home, 'journal/old.jsonl'), 'utf8'), 'unknown original send')
  } finally { f.clean(); rmSync(root, { recursive: true, force: true }) }
})
test('pending registration cannot select a retired short home after an explicit clean replacement', async () => {
  const f = fixture()
  try {
    const original = await prepareRuntimeInstallation(f.options)
    writeFileSync(path.join(original.home, 'management-secret.bin'), 'corrupt')
    mkdirSync(path.join(original.home, 'journal'))
    writeFileSync(path.join(original.home, 'journal/unknown.jsonl'), 'original unknown send')
    const replacement = await rotateRuntimeConnection(f.options, original.home)
    await assert.rejects(selectRuntimeConnection(f.options, original.home), /旧连接已被替换/,
      'An unfinished registration must not undo Clean by selecting its retired short path')
    assert.equal(await selectRuntimeConnection(f.options, replacement), replacement)
    assert.equal(readFileSync(path.join(original.home, 'management-secret.bin'), 'utf8'), 'corrupt', 'Retiring a path does not erase old evidence')
  } finally { f.clean() }
})

test('corrupt readable pointer is rebuilt using the trusted installation id and protected previous identity', async () => {
  const f = fixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    const first = await prepareRuntimeInstallation(f.options)
    writeFileSync(path.resolve(path.dirname(f.options.hostEntry), '../../../runtime-installation.id'), first.installationId)
    writeFileSync(path.join(first.metadataHome, 'current.json'), 'null')
    const rebuilt = await prepareRuntimeInstallation(f.options)
    assert.notEqual(rebuilt.home, first.home)
    assert.equal(rebuilt.installationId, first.installationId)
    assert.equal(rebuilt.migrated, true)
    assert.deepEqual(JSON.parse(readFileSync(path.join(rebuilt.home, 'config.json'), 'utf8')), f.config)
  } finally { f.clean() }
})

// ----- 契约 2.20 R3 复审修复：连接迁移沿用安装信任配置逐包验签迁移（不限定三家或三条） -----

/** dist/tests → clients 根（桥接真实构建产物用） */
const clientsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..', '..')

/**
 * 迁移 fixture 的仓库内前置（desktop 的 npm install/test 均不准备，干净环境按手册
 * 跑 desktop npm test 必须能走完）：runtime-plugin-packaging 的 node_modules
 * （canonicalize/yazl，构造签名离线包用）、agent-tool-runtime 与 local-tool-host-core
 * 的编译 dist（宿主真实迁移实现动态 import）。前置齐备时仍真实执行，不削弱守护。
 */
const migrationPrerequisites = {
  packagingDeps: ['canonicalize', 'yazl'].every(name =>
    existsSync(path.join(clientsRoot, 'runtime-plugin-packaging', 'node_modules', name, 'package.json'))),
  runtimeDist: existsSync(path.join(clientsRoot, 'agent-tool-runtime', 'dist', 'src', 'productTrust.js')),
  hostCoreDist: ['store.js', 'package.js'].every(name =>
    existsSync(path.join(clientsRoot, 'shared', 'local-tool-host-core', 'dist', 'plugins', name))),
}
const missingPrerequisites = Object.entries(migrationPrerequisites).filter(([, ready]) => !ready).map(([name]) => name)
const winX64Only = process.platform === 'win32' && process.arch === 'x64'
const migrationOnly = {
  skip: !winX64Only ? '迁移 fixture 走宿主真实实现，仅 Windows x64 执行'
    : missingPrerequisites.length > 0
      ? `迁移 fixture 前置缺失（${missingPrerequisites.join('、')}）：desktop npm test 不构建 runtime-plugin-packaging 依赖与 agent-tool-runtime/local-tool-host-core dist，显式跳过；补齐后本用例恢复真实执行`
      : false,
}

interface Packaging {
  createOfflinePackage(payload: Buffer, manifest: unknown, metadata: { release_id: string; key_id: string; created_at: string }, privateKey: string): Promise<{ archive: Buffer }>
  sha256(bytes: Buffer): string
  zipBytes(entries: Array<[string, Buffer]>): Promise<Buffer>
}

/** 离线包构造器复用 runtime-plugin-packaging（非本包 TS 依赖，运行时动态加载） */
const packaging = async (): Promise<Packaging> =>
  await import(new URL('../../../runtime-plugin-packaging/package.mjs', import.meta.url).href) as Packaging

/**
 * 真实迁移 fixture：不打 migratePlugins 注入接缝，改为在宿主目录布置 migratePlugins
 * 运行时动态 import 的同包接缝——store.js/package.js 桥接 host-core 真实 dist，
 * productTrust.js 桥接 agent-tool-runtime 真实构建，trust-roots.json 携带含第 4 方
 * 身份的信任配置。需先构建 host-core 与 agent-tool-runtime。
 */
async function migrationFixture() {
  const f = fixture()
  const options = { ...f.options, migratePlugins: undefined }
  const hostDir = path.dirname(options.hostEntry)
  const corePlugins = path.join(clientsRoot, 'shared', 'local-tool-host-core', 'dist', 'plugins')
  const trustModule = path.join(clientsRoot, 'agent-tool-runtime', 'dist', 'src', 'productTrust.js')
  writeFileSync(path.join(hostDir, 'package.json'), JSON.stringify({ version: '0.2.25' }))
  for (const name of ['store.js', 'package.js']) {
    const target = path.join(hostDir, 'node_modules/@aid/local-tool-host-core/dist/plugins', name)
    mkdirSync(path.dirname(target), { recursive: true })
    writeFileSync(target, `export * from ${JSON.stringify(pathToFileURL(path.join(corePlugins, name)).href)}`)
  }
  writeFileSync(path.join(hostDir, 'productTrust.js'), `export * from ${JSON.stringify(pathToFileURL(trustModule).href)}`)
  const { privateKey, publicKey } = generateKeyPairSync('ed25519')
  const trustRoot = {
    key_id: 'migration-fixture',
    public_key: publicKey.export({ format: 'pem', type: 'spki' }).toString(),
    providers: ['ai.aidwork.boss-recruiting', 'ai.aidwork.weixin', 'ai.aidwork.wecom', 'ai.aidwork.resume-tool'],
    test_only: true,
  }
  writeFileSync(path.resolve(hostDir, '../trust-roots.json'), JSON.stringify({ roots: [trustRoot] }))
  const privateKeyPem = privateKey.export({ format: 'pem', type: 'pkcs8' }).toString()
  const { createOfflinePackage, sha256, zipBytes } = await packaging()
  const buildArchive = async (providerId: string, toolName: string): Promise<Buffer> => {
    const cli = Buffer.from('// fixture plugin, never executed by these tests')
    const manifest = {
      manifest_version: 1, provider_id: providerId, provider_version: '1.0.0',
      display_name: 'Fixture', description: '', schema_digest: 'fixture',
      tools: [{ name: toolName }], execution_target: 'local_required', entrypoint: ['index.js', 'mcp', '--stdio'],
      distribution: { platform: process.platform, arch: process.arch, node: { version: process.versions.node, modules_abi: process.versions.modules }, required_files: [{ path: 'index.js', size: cli.length, sha256: sha256(cli) }] },
    }
    const payload = await zipBytes([['runtime-manifest.json', Buffer.from(JSON.stringify(manifest))], ['index.js', cli]])
    const built = await createOfflinePackage(payload, manifest, { release_id: `migration-${toolName}`, key_id: trustRoot.key_id, created_at: '2026-11-12T00:00:00Z' }, privateKeyPem)
    return built.archive
  }
  let inventory: { version: number; records: Record<string, unknown>[] } | null = null
  const addLegacyPlugin = (pluginId: string, enabled: boolean, archive: Buffer): void => {
    const directory = randomUUID()
    const file = path.join(options.legacyHome, 'plugins/releases', directory, 'archive.zip')
    mkdirSync(path.dirname(file), { recursive: true })
    writeFileSync(file, archive)
    inventory ??= { version: 1, records: [] }
    inventory.records.push({ plugin_id: pluginId, enabled, uninstalled: false, directory })
    writeFileSync(path.join(options.legacyHome, 'plugins/inventory.json'), JSON.stringify(inventory))
  }
  return { ...f, options, buildArchive, addLegacyPlugin }
}

const migratedProviderIds = (home: string): string[] => {
  const value = JSON.parse(readFileSync(path.join(home, 'plugins/inventory.json'), 'utf8')) as { version: number; records: Array<{ plugin_id: string; enabled: boolean }> }
  assert.equal(value.version, 1)
  return value.records.map(record => record.plugin_id).sort()
}

test('connection migration installs every trusted plugin beyond the first-party three, including a disabled one', migrationOnly, async () => {
  const f = await migrationFixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    const ids = ['ai.aidwork.boss-recruiting', 'ai.aidwork.weixin', 'ai.aidwork.wecom', 'ai.aidwork.resume-tool']
    for (const [index, id] of ids.entries()) {
      f.addLegacyPlugin(id, id !== 'ai.aidwork.weixin', await f.buildArchive(id, `fixture_tool_${index}`))
    }
    const installed = await prepareRuntimeInstallation(f.options)
    assert.deepEqual(migratedProviderIds(installed.home), [...ids].sort(), '信任配置覆盖的第 4 方身份随三家一起逐包迁移')
    const inventory = JSON.parse(readFileSync(path.join(installed.home, 'plugins/inventory.json'), 'utf8')) as { records: Array<{ plugin_id: string; enabled: boolean }> }
    assert.equal(inventory.records.find(record => record.plugin_id === 'ai.aidwork.weixin')?.enabled, false, '停用状态随迁移保留')
  } finally { f.clean() }
})

test('unauthorized identity in legacy inventory is skipped per package while trusted plugins still migrate', migrationOnly, async () => {
  const f = await migrationFixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    const trusted = ['ai.aidwork.boss-recruiting', 'ai.aidwork.weixin', 'ai.aidwork.wecom']
    for (const [index, id] of trusted.entries()) {
      f.addLegacyPlugin(id, true, await f.buildArchive(id, `fixture_tool_${index}`))
    }
    f.addLegacyPlugin('ai.aidwork.unauthorized', true, await f.buildArchive('ai.aidwork.unauthorized', 'fixture_tool_evil'))
    const installed = await prepareRuntimeInstallation(f.options)
    assert.deepEqual(migratedProviderIds(installed.home), [...trusted].sort(), '未授权身份该条跳过，其余照常迁移')
  } finally { f.clean() }
})

test('legacy three-plugin inventory keeps migrating unchanged', migrationOnly, async () => {
  const f = await migrationFixture()
  try {
    writeFileSync(path.join(f.options.legacyHome, 'config.json'), JSON.stringify(f.config))
    writeFileSync(path.join(f.options.legacyHome, 'credentials.bin'), await f.protect('a'.repeat(64)))
    const trusted = ['ai.aidwork.boss-recruiting', 'ai.aidwork.weixin', 'ai.aidwork.wecom']
    for (const [index, id] of trusted.entries()) {
      f.addLegacyPlugin(id, true, await f.buildArchive(id, `fixture_tool_${index}`))
    }
    const installed = await prepareRuntimeInstallation(f.options)
    assert.deepEqual(migratedProviderIds(installed.home), [...trusted].sort(), '三家旧行为不变')
  } finally { f.clean() }
})
