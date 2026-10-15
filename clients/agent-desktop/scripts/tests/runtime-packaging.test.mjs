import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { createRequire } from 'node:module'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { normalizeTrust, filesUnder, nodeSpecification, assertNoSensitiveAssets, validateReleaseInputs, digest, runtimeNodeSignExclusion, authenticodeStatus,
  acceptanceTrustRegistryPath, runtimeR15TrustSnapshotPath, loadAcceptanceTrustRegistry, assertHistoricalTrustCoverage, assertRegisteredPublisherTrust, assertUnusedAcceptanceTestKeyId, main as prepareRuntime,
} from '../prepare-runtime.mjs'
import { mergePublisherTrust, registerPublisherTrust } from '../../../runtime-plugin-packaging/register-publisher-trust.mjs'
const require = createRequire(import.meta.url)
require('app-builder-lib') // Initialize the library's supported entry before its packager classes.
const { WinPackager } = require('app-builder-lib/out/winPackager.js')

const key = generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' })
const roots = [{ key_id: 'name-does-not-define-trust', public_key: key, providers: ['ai.aidwork.weixin'], test_only: true }]
test('打包仅原样消费最终wrapped信任输入', () => {
  const trust = { schemaVersion: 1, profile: 'acceptance', roots }
  assert.equal(normalizeTrust(trust, 'acceptance'), trust)
  assert.throws(() => normalizeTrust({ test_only: true, key_id: 'old', public_key: key, provider_ids: ['ai.aidwork.weixin'] }, 'acceptance'))
  assert.throws(() => normalizeTrust({ ...trust, trust_roots_version: 1 }, 'acceptance'))
  assert.throws(() => normalizeTrust({ ...trust, roots: [{ key_id: 'old', public_key: key, providers: ['ai.aidwork.weixin'] }] }, 'acceptance'))
})
test('production拒显式测试根及profile混入，不根据名字猜测', () => {
  assert.throws(() => normalizeTrust({ schemaVersion: 1, profile: 'production', roots }, 'production'))
  const production = { schemaVersion: 1, profile: 'production', roots: [{ ...roots[0], key_id: 'contains-test-name', test_only: false }] }
  assert.equal(normalizeTrust(production, 'production'), production)
  assert.throws(() => normalizeTrust(production, 'acceptance'))
})
test('重复发布者key必须失败，不能依靠数组顺序选择信任', () => {
  assert.throws(() => normalizeTrust({ schemaVersion: 1, profile: 'acceptance', roots: [...roots, ...roots] }, 'acceptance'))
})
test('资源清单包含每个嵌套运行文件的实际size及sha', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-resource-test-'))
  try {
    mkdirSync(path.join(root, 'sessionTasks'))
    writeFileSync(path.join(root, 'sessionTasks/engine.js'), 'abc')
    assert.deepEqual(filesUnder(root), [{ path: 'sessionTasks/engine.js', size: 3, sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' }])
  } finally { rmSync(root, { recursive: true, force: true }) }
})
test('固定Node来源精确到版本/架构/官方zip摘要，不能采用latest地址', () => {
  assert.equal(nodeSpecification.version, '22.23.3')
  assert.equal(nodeSpecification.arch, 'x64')
  assert.equal(nodeSpecification.modulesAbi, '127')
  assert.equal(nodeSpecification.url, 'https://nodejs.org/dist/v22.23.3/node-v22.23.3-win-x64.zip')
  assert.equal(nodeSpecification.sha256, '2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71')
})
test('临时测试签名私钥和设备凭证不可进入受控Host资源', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-resource-test-'))
  try {
    writeFileSync(path.join(root, 'credentials.bin'), 'not-a-real-credential')
    assert.throws(() => assertNoSensitiveAssets(root), /credential/)
    rmSync(path.join(root, 'credentials.bin'))
    writeFileSync(path.join(root, 'signer.pem'), generateKeyPairSync('ed25519').privateKey.export({ type: 'pkcs8', format: 'pem' }))
    assert.throws(() => assertNoSensitiveAssets(root), /private key/)
    writeFileSync(path.join(root, 'signer.pem'), key)
    assert.doesNotThrow(() => assertNoSensitiveAssets(root))
  } finally { rmSync(root, { recursive: true, force: true }) }
})
test('publisher public_key拒PKCS8私钥，完整资源扫描覆盖JSON中的转义私钥', () => {
  const privateKey = generateKeyPairSync('ed25519').privateKey.export({ type: 'pkcs8', format: 'pem' })
  const trust = { schemaVersion: 1, profile: 'acceptance', roots: [{ ...roots[0], public_key: privateKey }] }
  assert.throws(() => normalizeTrust(trust, 'acceptance'), /publisher trust/)
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-resource-test-'))
  try {
    mkdirSync(path.join(root, 'runtime'))
    writeFileSync(path.join(root, 'runtime/trust-roots.json'), JSON.stringify(trust))
    assert.throws(() => assertNoSensitiveAssets(root), /private key/)
    rmSync(path.join(root, 'runtime/trust-roots.json'))
    writeFileSync(path.join(root, 'validator.js'), 'const marker = "-----BEGIN PRIVATE KEY-----"; const regex = /-----BEGIN PRIVATE KEY-----\\r?\\n/;')
    assert.doesNotThrow(() => assertNoSensitiveAssets(root))
  } finally { rmSync(root, { recursive: true, force: true }) }
})
test('发行输入必须复算digest，拒重复路径/越界路径和未绑定的包内输入', () => {
  const inputs = [{ path: 'dist/electron/main.js', sha256: 'a'.repeat(64) }, { path: 'dist/electron/releasePolicy.js', sha256: 'b'.repeat(64) }, { path: 'resources/runtime/resource-manifest.json', sha256: 'c'.repeat(64) }]
  const value = entries => ({ inputs: entries, packagedInputs: [entries[0], entries[2]], buildInputSha256: digest(Buffer.from(JSON.stringify(entries))) })
  assert.equal(validateReleaseInputs(value(inputs)).size, 2)
  assert.throws(() => validateReleaseInputs({ ...value(inputs), buildInputSha256: 'a'.repeat(64) }), /fingerprint/)
  assert.throws(() => validateReleaseInputs(value([...inputs, inputs[0]])), /duplicate/)
  const escaped = [{ ...inputs[0], path: 'dist/../../escape' }, ...inputs.slice(1)]
  assert.throws(() => validateReleaseInputs(value(escaped)), /invalid/)
  assert.throws(() => validateReleaseInputs({ ...value(inputs), packagedInputs: [{ ...inputs[0], sha256: 'b'.repeat(64) }, inputs[2]] }), /invalid/)
  assert.throws(() => validateReleaseInputs({ ...value(inputs), packagedInputs: [inputs[0]] }), /omitted/)
})
test('实际builder签名选择排除固定Node资源，仍签主程序/NSIS/其他exe', () => {
  const packager = { platformSpecificBuildOptions: { signExts: [runtimeNodeSignExclusion] } }
  const decide = file => WinPackager.prototype.shouldSignFile.call(packager, file, true)
  assert.equal(decide('C:\\build\\runtime-resources\\runtime\\node\\win-x64\\node.exe'), false)
  assert.equal(decide('C:\\release\\win-unpacked\\resources\\runtime\\node\\win-x64\\node.exe'), false)
  assert.equal(decide('C:\\release\\win-unpacked\\AID Work Runtime.exe'), true)
  assert.equal(decide('C:\\release\\AID-Work-Runtime-installer.exe'), true)
  assert.equal(decide('C:\\release\\resources\\other-tool.exe'), true)
  // A different node.exe is not exempted merely because of its file name.
  assert.equal(decide('C:\\release\\node.exe'), true)
  packager.shouldSignFile = file => WinPackager.prototype.shouldSignFile.call(packager, file)
  const transformer = WinPackager.prototype.createTransformerForExtraFiles.call(packager, { appOutDir: 'C:\\release\\win-unpacked' })
  assert.equal(transformer('C:\\build\\runtime-resources\\runtime\\node\\win-x64\\node.exe'), null)
  assert.notEqual(transformer('C:\\build\\runtime-resources\\other-tool.exe'), null)
})
test('Windows5.1验签忽略继承的PowerShell7模块路径并加载系统Security模块', () => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-authenticode-test-'))
  try {
    const unsigned = path.join(root, "unsigned'fixture.ps1")
    writeFileSync(unsigned, '# isolated unsigned signature fixture\n')
    const baseline = authenticodeStatus(unsigned)
    assert.equal(baseline, 'NotSigned')
    const polluted = { ...process.env, PSModulePath: 'C:\\Program Files\\PowerShell\\7\\Modules', psMODULEpath: 'C:\\invalid\\modules' }
    assert.equal(authenticodeStatus(unsigned, polluted), baseline)
  } finally { rmSync(root, { recursive: true, force: true }) }
})

const wrapped = roots => ({ schemaVersion: 1, profile: 'acceptance', roots })
const additionalRoot = () => ({ ...roots[0], key_id: 'aid-isolated-registration-fixture', public_key: generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' }) })

test('验收构建根必须与登记完全一致：漏根、换钥、改scope/test_only和未登记新根均拒绝', () => {
  const registry = loadAcceptanceTrustRegistry()
  const changed = change => {
    const trust = structuredClone(registry)
    change(trust)
    return trust
  }
  for (const invalid of [
    changed(trust => trust.roots.shift()),
    changed(trust => { trust.roots[0].public_key = key }),
    changed(trust => { trust.roots[0].providers.push('ai.aidwork.unregistered') }),
    changed(trust => { trust.roots[0].test_only = !trust.roots[0].test_only }),
    changed(trust => { trust.roots.push(additionalRoot()) }),
  ]) assert.throws(() => assertRegisteredPublisherTrust(invalid, 'acceptance'), /historical|unregistered/)
  assert.equal(assertRegisteredPublisherTrust(registry, 'acceptance'), registry)
})

test('根顺序、provider顺序、JSON格式及规范PEM换行变化不改变已有信任', () => {
  const registry = loadAcceptanceTrustRegistry()
  const reordered = wrapped(registry.roots.toReversed().map(root => ({
    public_key: root.public_key.replaceAll('\n', '\r\n'), test_only: root.test_only,
    providers: root.providers.toReversed(), key_id: root.key_id,
  })))
  assert.doesNotThrow(() => assertRegisteredPublisherTrust(JSON.parse(JSON.stringify(reordered)), 'acceptance'))
})

test('production保持显式生产信任门，不消费验收registry', () => {
  const production = { schemaVersion: 1, profile: 'production', roots: [{ ...roots[0], test_only: false }] }
  // Even a deliberately invalid acceptance registry must not affect production.
  assert.equal(assertRegisteredPublisherTrust(production, 'production', { roots: [] }), production)
  assert.throws(() => assertRegisteredPublisherTrust({ ...production, roots }, 'production'), /test publisher/)
})

test('登记表不可丢r15任一历史根；历史快照本身缺根也不能重新定义基线', () => {
  const registry = loadAcceptanceTrustRegistry()
  const historical = JSON.parse(readFileSync(runtimeR15TrustSnapshotPath, 'utf8'))
  assert.equal(historical.roots.length, 4)
  for (const root of historical.roots) {
    assert.throws(() => assertHistoricalTrustCoverage(wrapped(registry.roots.filter(item => item.key_id !== root.key_id)), historical), /historical/)
  }
  const directory = mkdtempSync(path.join(os.tmpdir(), 'aid-trust-history-'))
  try {
    const registryPath = path.join(directory, 'registry.json'), snapshotPath = path.join(directory, 'snapshot.json')
    writeFileSync(registryPath, JSON.stringify(wrapped(registry.roots.slice(1))))
    assert.throws(() => loadAcceptanceTrustRegistry(registryPath), /historical/)
    writeFileSync(registryPath, JSON.stringify(registry))
    writeFileSync(snapshotPath, JSON.stringify(wrapped(historical.roots.slice(1))))
    assert.throws(() => loadAcceptanceTrustRegistry(registryPath, snapshotPath), /snapshot SHA-256/)
  } finally { rmSync(directory, { recursive: true, force: true }) }
})

test('r15之后已发布的第五根也必须保留，任一发布快照损坏均拒绝', () => {
  const registry = loadAcceptanceTrustRegistry()
  const original = JSON.parse(readFileSync(runtimeR15TrustSnapshotPath, 'utf8'))
  const published = JSON.parse(readFileSync(path.join(path.dirname(runtimeR15TrustSnapshotPath), 'runtime-0.0.12.roots.json'), 'utf8'))
  const originalIds = new Set(original.roots.map(root => root.key_id))
  const newer = published.roots.find(root => !originalIds.has(root.key_id))
  assert.ok(newer, 'Published 0.0.12 snapshot must include the signer added after r15')
  const directory = mkdtempSync(path.join(os.tmpdir(), 'aid-trust-published-'))
  try {
    const registryPath = path.join(directory, 'registry.json'), history = path.join(directory, 'history')
    mkdirSync(history)
    const releasePath = path.join(history, 'runtime-0.0.12.roots.json')
    writeFileSync(releasePath, JSON.stringify(published))
    writeFileSync(registryPath, JSON.stringify(registry))
    assert.doesNotThrow(() => loadAcceptanceTrustRegistry(registryPath, runtimeR15TrustSnapshotPath, history))
    const missingNewer = wrapped(registry.roots.filter(root => root.key_id !== newer.key_id))
    assert.doesNotThrow(() => assertHistoricalTrustCoverage(missingNewer, original))
    writeFileSync(registryPath, JSON.stringify(missingNewer))
    assert.throws(() => loadAcceptanceTrustRegistry(registryPath, runtimeR15TrustSnapshotPath, history), /historical/)
    writeFileSync(registryPath, JSON.stringify(registry))
    writeFileSync(path.join(history, 'next-release.roots.json'), 'corrupt-snapshot-body-fixture')
    assert.throws(() => loadAcceptanceTrustRegistry(registryPath, runtimeR15TrustSnapshotPath, history), /Invalid historical publisher trust snapshot JSON/)
    writeFileSync(path.join(history, 'next-release.roots.json'), JSON.stringify({ ...published, roots: [] }))
    assert.throws(() => loadAcceptanceTrustRegistry(registryPath, runtimeR15TrustSnapshotPath, history), /publisher trust/)
  } finally { rmSync(directory, { recursive: true, force: true }) }
})

test('纯追加登记保留全部旧根，同id同key/scope幂等，同id替key/scope或私钥拒绝', () => {
  const registry = loadAcceptanceTrustRegistry(), added = additionalRoot()
  const merged = mergePublisherTrust(registry, wrapped([added]))
  assert.equal(merged.roots.length, registry.roots.length + 1)
  assert.deepEqual(merged.roots.slice(0, registry.roots.length), registry.roots)
  assert.deepEqual(mergePublisherTrust(merged, wrapped([added])), merged)
  const first = registry.roots[0]
  for (const conflict of [
    { ...first, public_key: key },
    { ...first, providers: ['ai.aidwork.different'] },
    { ...first, test_only: !first.test_only },
    { ...added, public_key: generateKeyPairSync('ed25519').privateKey.export({ type: 'pkcs8', format: 'pem' }) },
  ]) assert.throws(() => mergePublisherTrust(registry, wrapped([conflict])), /historical|publisher trust/)
})

test('追加登记原子持久化后能供新构建使用，失败与重复登记不改变旧文件', () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'aid-trust-register-'))
  try {
    const registryPath = path.join(directory, 'registry.json')
    const before = readFileSync(acceptanceTrustRegistryPath)
    writeFileSync(registryPath, before)
    const added = additionalRoot()
    const merged = registerPublisherTrust(wrapped([added]), registryPath)
    assert.deepEqual(loadAcceptanceTrustRegistry(registryPath), merged)
    assert.doesNotThrow(() => assertRegisteredPublisherTrust(merged, 'acceptance', loadAcceptanceTrustRegistry(registryPath)))
    const after = readFileSync(registryPath)
    registerPublisherTrust(wrapped([added]), registryPath)
    assert.deepEqual(readFileSync(registryPath), after)
    assert.throws(() => registerPublisherTrust(wrapped([{ ...added, public_key: key }]), registryPath), /historical/)
    assert.deepEqual(readFileSync(registryPath), after)
    assert.deepEqual(readdirSync(directory), ['registry.json'])
    writeFileSync(registryPath + '.lock', '')
    assert.throws(() => registerPublisherTrust(wrapped([additionalRoot()]), registryPath), /already locked/)
    assert.equal(existsSync(registryPath + '.lock'), true)
    assert.deepEqual(readFileSync(registryPath), after)
  } finally { rmSync(directory, { recursive: true, force: true }) }
})

test('随机测试密钥生成前拒绝复用任一已登记id，避免同名新钥导致旧包不可升级', () => {
  const registry = loadAcceptanceTrustRegistry()
  for (const root of registry.roots) assert.throws(() => assertUnusedAcceptanceTestKeyId(root.key_id), /unique --test-key-id/)
  assert.equal(assertUnusedAcceptanceTestKeyId('aid-isolated-unused-fixture'), 'aid-isolated-unused-fixture')
})

test('prepare在下载或解包Node前拒绝丢历史根输入', async () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'aid-trust-early-gate-'))
  try {
    const hostPath = path.join(directory, 'host.tgz'), trustPath = path.join(directory, 'trust.json')
    const host = Buffer.from('not-extracted-in-this-test')
    writeFileSync(hostPath, host)
    const registry = loadAcceptanceTrustRegistry()
    writeFileSync(trustPath, JSON.stringify(wrapped(registry.roots.slice(1))))
    await assert.rejects(prepareRuntime([
      '--profile', 'acceptance', '--host-package', hostPath, '--host-sha256', digest(host),
      '--trust-roots', trustPath, '--node-archive', path.join(directory, 'must-not-read.zip'),
    ]), /historical/)
    assert.deepEqual(readdirSync(directory).sort(), ['host.tgz', 'trust.json'])
  } finally { rmSync(directory, { recursive: true, force: true }) }
})

test('登记CLI拒绝私钥和损坏JSON且错误输出不包含输入正文，固定registry字节不变', () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'aid-trust-cli-'))
  try {
    const inputPath = path.join(directory, 'input.json')
    const before = readFileSync(acceptanceTrustRegistryPath)
    const privateKey = generateKeyPairSync('ed25519').privateKey.export({ type: 'pkcs8', format: 'pem' })
    const script = fileURLToPath(new URL('../../../runtime-plugin-packaging/register-publisher-trust.mjs', import.meta.url))
    for (const input of [
      JSON.stringify(wrapped([{ ...additionalRoot(), public_key: privateKey }])),
      JSON.stringify(wrapped([{ ...additionalRoot(), key_id: privateKey }])),
      JSON.stringify(wrapped([{ ...additionalRoot(), providers: [privateKey] }])),
      'not-json-sensitive-body-fixture',
    ]) {
      writeFileSync(inputPath, input)
      const result = spawnSync(process.execPath, [script, '--input', inputPath], { encoding: 'utf8', windowsHide: true })
      assert.equal(result.status, 1)
      assert.equal(result.stdout, '')
      assert.match(result.stderr, /Publisher trust registration failed/)
      assert.doesNotMatch(result.stderr, /BEGIN|sensitive-body-fixture/)
      assert.deepEqual(readFileSync(acceptanceTrustRegistryPath), before)
    }
  } finally { rmSync(directory, { recursive: true, force: true }) }
})
