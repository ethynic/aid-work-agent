import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { resolveProductConfiguration } from '../electron/productConfiguration.js'

const publicKey = generateKeyPairSync('ed25519').publicKey.export({ type: 'spki', format: 'pem' })
function fixture(profile = 'acceptance', kind = 'runtime') {
  const root = mkdtempSync(path.join(os.tmpdir(), 'aid-product-test-'))
  const resourcesPath = path.join(root, 'resources')
  const appDataPath = path.join(root, 'app-data')
  for (const file of ['runtime/node/win-x64/node.exe', 'runtime/host/managed-entry.js']) {
    mkdirSync(path.dirname(path.join(resourcesPath, file)), { recursive: true })
    writeFileSync(path.join(resourcesPath, file), 'fixture')
  }
  mkdirSync(path.join(resourcesPath, 'config'))
  const config = { schemaVersion: 1, productKind: kind, profile }
  const trust = { schemaVersion: 1, profile, roots: [{ key_id: 'test-key', public_key: publicKey, providers: ['ai.aidwork.weixin'], test_only: profile === 'acceptance' }] }
  const write = (configuration: unknown = config, roots: unknown = trust) => {
    writeFileSync(path.join(resourcesPath, 'config/runtime-product.json'), JSON.stringify(configuration))
    writeFileSync(path.join(resourcesPath, 'runtime/trust-roots.json'), JSON.stringify(roots))
  }
  write()
  return { root, resourcesPath, appDataPath, isPackaged: true, config, trust, write }
}
function clean(root: string) { rmSync(root, { recursive: true, force: true }) }

test('普通Desktop没有Runtime配置时保留身份且不启动客户机Host', () => {
  const f = fixture()
  try {
    rmSync(path.join(f.resourcesPath, 'config/runtime-product.json'))
    const result = resolveProductConfiguration(f)
    assert.equal(result.kind, 'desktop')
    assert.equal(result.appId, 'cn.aidingyi.agent.desktop')
    assert.equal(result.runtimeEnabled, false)
    assert.equal(result.runtimeHome, null)
    assert.equal(result.userDataPath, null)
  } finally { clean(f.root) }
})

test('Runtime验收包固定独立身份与数据目录，执行路径只能来自可信resources', () => {
  const f = fixture()
  try {
    const result = resolveProductConfiguration(f)
    assert.equal(result.appId, 'cn.aidingyi.agent.runtime.acceptance')
    assert.equal(result.productName, 'AID Work Runtime 验收版')
    assert.equal(result.userDataPath, path.join(f.appDataPath, 'aidwork-runtime-app-acceptance'))
    assert.equal(result.runtimeHome, path.join(f.appDataPath, 'aidwork-tool-runtime-acceptance'))
    assert.equal(result.nodeExecutable, path.join(f.resourcesPath, 'runtime/node/win-x64/node.exe'))
    assert.equal(result.hostEntry, path.join(f.resourcesPath, 'runtime/host/managed-entry.js'))
  } finally { clean(f.root) }
})

test('Desktop受控生产配置保留账户路径并使用原Runtime home', () => {
  const f = fixture('production', 'desktop')
  try {
    const result = resolveProductConfiguration(f)
    assert.equal(result.kind, 'desktop')
    assert.equal(result.userDataPath, null)
    assert.equal(result.runtimeHome, path.join(f.appDataPath, 'aidwork-tool-runtime'))
    assert.equal(result.runtimeEnabled, true)
    // A key name is not a test/production identity: the explicit boolean governs.
    assert.equal(result.profile, 'production')
  } finally { clean(f.root) }
})

test('Desktop验收配置也隔离数据，不能把测试Host接到生产凭证目录', () => {
  const f = fixture('acceptance', 'desktop')
  try {
    const result = resolveProductConfiguration(f)
    assert.equal(result.appId, 'cn.aidingyi.agent.desktop.acceptance')
    assert.equal(result.userDataPath, path.join(f.appDataPath, 'aidwork-desktop-app-acceptance'))
    assert.equal(result.runtimeHome, path.join(f.appDataPath, 'aidwork-tool-runtime-acceptance'))
  } finally { clean(f.root) }
})

test('生产配置明确拒绝test_only根，不回退验收profile', () => {
  const f = fixture('production')
  try {
    f.trust.roots[0]!.test_only = true
    f.write()
    assert.throws(() => resolveProductConfiguration(f), /test publisher/)
    f.write(f.config, { ...f.trust, profile: 'acceptance' })
    assert.throws(() => resolveProductConfiguration(f), /trust configuration/)
  } finally { clean(f.root) }
})

test('拒绝旧单根、缺test_only、重复key及私钥字段，避免猜测发行身份', () => {
  const f = fixture()
  try {
    const root = f.trust.roots[0]!
    for (const trust of [
      { test_only: true, key_id: root.key_id, public_key: root.public_key, provider_ids: root.providers },
      { ...f.trust, roots: [{ key_id: root.key_id, public_key: root.public_key, providers: root.providers }] },
      { ...f.trust, roots: [root, root] },
      { ...f.trust, roots: [{ ...root, private_key: 'forbidden' }] },
    ]) {
      f.write(f.config, trust)
      assert.throws(() => resolveProductConfiguration(f))
    }
  } finally { clean(f.root) }
})

test('配置不能注入任意home/执行路径，资源缺失必须失败', () => {
  const f = fixture()
  try {
    f.write({ ...f.config, runtimeHome: 'C:/other-device' })
    assert.throws(() => resolveProductConfiguration(f), /product configuration/)
    f.write()
    rmSync(path.join(f.resourcesPath, 'runtime/node/win-x64/node.exe'))
    assert.throws(() => resolveProductConfiguration(f), /asset missing/)
  } finally { clean(f.root) }
})

test('public_key中真实PKCS8私钥必须拒绝，不能借createPublicKey派生后打包', () => {
  const f = fixture()
  try {
    const privateKey = generateKeyPairSync('ed25519').privateKey.export({ type: 'pkcs8', format: 'pem' })
    f.write(f.config, { ...f.trust, roots: [{ ...f.trust.roots[0], public_key: privateKey }] })
    assert.throws(() => resolveProductConfiguration(f), /publisher trust/)
  } finally { clean(f.root) }
})
