/**
 * 设备能力上报：providers 数组 / protocol_version / provider_manifests / 旧 provider_id 兼容；
 * 配置兼容：旧 config.json 无 providers 字段照常工作；bossCliEntry 高优先级折算。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { deviceCapabilities, resolveProviderEntries, type RuntimeConfig } from '../src/config.js'
import { manifestDigestFor } from '../src/providers.js'

function makeConfig(overrides: Partial<RuntimeConfig> = {}): RuntimeConfig {
  return { server: 'http://127.0.0.1:1', device_id: 'dev-1', ...overrides }
}

test("boss-only（无 providers 配置）：providers=['boss-recruiting']，旧 provider_id 字段保持", () => {
  const caps = deviceCapabilities(null)
  assert.deepEqual(caps['providers'], ['boss-recruiting'])
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting')
  assert.equal(caps['protocol_version'], 2)
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.deepEqual(Object.keys(manifests), ['boss-recruiting'])
  assert.equal(manifests['boss-recruiting']!['provider_id'], 'ai.aidwork.boss-recruiting')
  assert.equal(manifests['boss-recruiting']!['manifest_digest'], manifestDigestFor('boss-recruiting'))
  assert.equal(manifests['boss-recruiting']!['protocol_version'], 1)
})

test('配置 weixin entry：providers 数组含 weixin，manifests 双份，旧 provider_id 仍为第一个可用 provider', () => {
  const caps = deviceCapabilities(makeConfig({ providers: { weixin: { entry: 'C:/fake/weixin.js' } } }))
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'weixin'])
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting', 'boss 恒为第一个可用 provider（云端兼容）')
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.equal(manifests['weixin']!['provider_id'], 'ai.aidwork.weixin')
  assert.equal(manifests['weixin']!['manifest_digest'], manifestDigestFor('weixin'))
  assert.notEqual(manifests['weixin']!['manifest_digest'], manifests['boss-recruiting']!['manifest_digest'])
})

test('配置兼容：旧 config.json 只有 bossCliEntry 时 resolveProviderEntries 正常折算', () => {
  const entries = resolveProviderEntries(makeConfig({ bossCliEntry: 'C:/boss/entry.js' }))
  assert.deepEqual(entries, { 'boss-recruiting': 'C:/boss/entry.js' })

  // bossCliEntry 优先级高于 providers['boss-recruiting'].entry
  const folded = resolveProviderEntries(makeConfig({
    bossCliEntry: 'C:/boss/high.js',
    providers: { 'boss-recruiting': { entry: 'C:/boss/low.js' }, weixin: { entry: 'C:/wx/entry.js' } },
  }))
  assert.equal(folded['boss-recruiting'], 'C:/boss/high.js')
  assert.equal(folded['weixin'], 'C:/wx/entry.js')

  // 仅 providers 配置 boss entry（无 bossCliEntry）
  const fromProviders = resolveProviderEntries(makeConfig({ providers: { 'boss-recruiting': { entry: 'C:/boss/from-providers.js' } } }))
  assert.equal(fromProviders['boss-recruiting'], 'C:/boss/from-providers.js')

  // 无任何配置 → boss 默认入口兜底
  assert.ok(resolveProviderEntries(null)['boss-recruiting'])
})

test('entry 解析：providers 中非法条目（entry 非字符串/空）被忽略，不影响 boss', () => {
  const bogus = makeConfig({ providers: Object.assign(Object.create(null), { weixin: { entry: 42 } }) })
  const entries = resolveProviderEntries(bogus as unknown as RuntimeConfig)
  assert.equal(entries['weixin'], undefined)
  assert.ok(entries['boss-recruiting'])
})
