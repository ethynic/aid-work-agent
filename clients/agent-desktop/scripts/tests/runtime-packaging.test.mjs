import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { createRequire } from 'node:module'
import { normalizeTrust, filesUnder, nodeSpecification, assertNoSensitiveAssets, validateReleaseInputs, digest, runtimeNodeSignExclusion, authenticodeStatus } from '../prepare-runtime.mjs'
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
