/**
 * 设备能力上报：providers 数组 / protocol_version / provider_manifests / 旧 provider_id 兼容；
 * 配置兼容：旧 config.json 无 providers 字段照常工作；bossCliEntry 高优先级折算。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
const existingEntry = fileURLToPath(new URL('./helpers/fakeProvider.js', import.meta.url))
import { deviceCapabilities, resolveProviderEntries, type RuntimeConfig } from '../src/config.js'
import { deriveProviderManifest, manifestDigestFor, manifestDigestOf, TRUSTED_MANIFESTS, type ProviderManifest } from '../src/providers.js'

function makeConfig(overrides: Partial<RuntimeConfig> = {}): RuntimeConfig {
  return { server: 'http://127.0.0.1:1', device_id: 'dev-1', bossCliEntry: existingEntry, ...overrides }
}

test("boss-only（显式 legacy bossCliEntry）：providers=['boss-recruiting']，旧 provider_id 字段保持", () => {
  const caps = deviceCapabilities(makeConfig())
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
  const caps = deviceCapabilities(makeConfig({ providers: { weixin: { entry: existingEntry } } }))
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'weixin'])
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting', 'boss 恒为第一个可用 provider（云端兼容）')
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.equal(manifests['weixin']!['provider_id'], 'ai.aidwork.weixin')
  assert.equal(manifests['weixin']!['manifest_digest'], manifestDigestFor('weixin'))
  assert.notEqual(manifests['weixin']!['manifest_digest'], manifests['boss-recruiting']!['manifest_digest'])
})

test('配置 wecom entry：providers 数组含 wecom，manifests 含独立摘要，capabilities 上报 8 工具能力', () => {
  const caps = deviceCapabilities(makeConfig({ providers: { wecom: { entry: existingEntry } } }))
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'wecom'])
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting', 'boss 恒为第一个可用 provider（云端兼容）')
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.equal(manifests['wecom']!['provider_id'], 'ai.aidwork.wecom')
  assert.equal(manifests['wecom']!['manifest_digest'], 'b7c278721d218db6c6fd2a9f07a4c543403f4fbb597a0c8d345a8c7f59f35279', 'unverified entry retains the accepted eight-tool manifest')
  assert.notEqual(manifests['wecom']!['manifest_digest'], manifests['boss-recruiting']!['manifest_digest'])
  assert.equal(manifests['wecom']!['protocol_version'], 1)
  // M11b：wecom 工具能力按工具名上报（与 manifest 同源；粒度对齐 weixin_message_send_v2）
  const capabilities = caps['capabilities'] as string[]
  assert.ok(!capabilities.includes('wecom_read_sessions'), 'a configured path does not prove the new plugin capability')
  assert.ok(!capabilities.includes('wecom_session_directory'), 'a configured path does not prove the picker capability')
  assert.ok(!capabilities.includes('wecom_session_directory_search'))
  const verified = deviceCapabilities(makeConfig({ providers: { wecom: { entry: existingEntry } } }), true)
  assert.ok((verified.capabilities as string[]).includes('wecom_read_sessions'))
  assert.ok(!(verified.capabilities as string[]).includes('wecom_session_directory'))
  assert.ok(!(verified.capabilities as string[]).includes('wecom_session_directory_search'))
  // 代理任务 P3：配置工具未验证（缺省 false）→ 与 session_directory 同规则被过滤出摘要
  assert.equal((verified.provider_manifests as Record<string, Record<string, unknown>>).wecom!.manifest_digest,
    manifestDigestOf({ ...TRUSTED_MANIFESTS.wecom!, tools: TRUSTED_MANIFESTS.wecom!.tools.filter(name =>
      name !== 'wecom_session_directory' && name !== 'wecom_config_doctor' && name !== 'wecom_config_calibrate') }))
  const directoryVerified = deviceCapabilities(makeConfig({ providers: { wecom: { entry: existingEntry } } }), true, true)
  assert.equal((directoryVerified.provider_manifests as Record<string, Record<string, unknown>>).wecom!.manifest_digest,
    manifestDigestOf({ ...TRUSTED_MANIFESTS.wecom!, tools: TRUSTED_MANIFESTS.wecom!.tools.filter(name =>
      name !== 'wecom_config_doctor' && name !== 'wecom_config_calibrate') }))
  assert.ok((directoryVerified.capabilities as string[]).includes('wecom_session_directory'))
  assert.ok(!(directoryVerified.capabilities as string[]).includes('wecom_session_directory_search'), 'page-only schema does not prove search')
  const searchVerified = deviceCapabilities(makeConfig({ providers: { wecom: { entry: existingEntry } } }), true, true, true)
  assert.ok((searchVerified.capabilities as string[]).includes('wecom_session_directory_search'))
  assert.equal((searchVerified.provider_manifests as Record<string, Record<string, unknown>>).wecom!.manifest_digest,
    manifestDigestOf({ ...TRUSTED_MANIFESTS.wecom!, tools: TRUSTED_MANIFESTS.wecom!.tools.filter(name =>
      name !== 'wecom_config_doctor' && name !== 'wecom_config_calibrate') }),
    'search is a feature capability, not a new trusted tool')
  for (const tool of ['wecom_probe', 'wecom_message_send', 'wecom_send_status', 'wecom_group_poll', 'wecom_send_image', 'wecom_send_file', 'wecom_read_session', 'wecom_unread_list']) {
    assert.ok(capabilities.includes(tool), `${tool} 应在 capabilities`)
  }
  // 未接入 runtime 受信面的工具不得上报
  for (const tool of ['wecom_chat_search', 'wecom_chat_select', 'wecom_watch_poll']) {
    assert.ok(!capabilities.includes(tool), `${tool} 未接入不得上报`)
  }
})

test('未配置 wecom entry：capabilities 与 provider_manifests 均不含 wecom（未安装不上报）', () => {
  const caps = deviceCapabilities(makeConfig({ providers: { weixin: { entry: existingEntry } } }))
  assert.ok(!(caps['providers'] as string[]).includes('wecom'))
  assert.equal((caps['provider_manifests'] as Record<string, unknown>)['wecom'], undefined)
  const capabilities = caps['capabilities'] as string[]
  assert.ok(!capabilities.some((c) => c.startsWith('wecom_')), 'wecom 未安装不得上报任何工具能力')
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
  const fromProviders = resolveProviderEntries(makeConfig({ bossCliEntry: undefined, providers: { 'boss-recruiting': { entry: 'C:/boss/from-providers.js' } } }))
  assert.equal(fromProviders['boss-recruiting'], 'C:/boss/from-providers.js')

  // 空 Host 没有隐式业务入口。
  assert.deepEqual(resolveProviderEntries(null), {})
})

test('entry 解析：providers 中非法条目（entry 非字符串/空）被忽略，不影响 boss', () => {
  const bogus = makeConfig({ providers: Object.assign(Object.create(null), { weixin: { entry: 42 } }) })
  const entries = resolveProviderEntries(bogus as unknown as RuntimeConfig)
  assert.equal(entries['weixin'], undefined)
  assert.ok(entries['boss-recruiting'])
})


test('D5 能力上报与实际生效 manifest 同源：默认 v1；v2Send 显式协商后协议/工具/摘要一致', async () => {
  const { setManifestOverride, weixinV2Manifest, manifestDigestOf } = await import('../src/providers.js')
  const { deviceCapabilities } = await import('../src/config.js')
  const baseConfig: Record<string, unknown> = {
    server: 'http://x', device_id: 'd',
    providers: { weixin: { entry: existingEntry } },
  }
  // 默认（未协商）：协议 1、工具集合无 v2、能力不含 v2 发送
  const def = deviceCapabilities(baseConfig as never) as { providers: string[]; capabilities: string[]; provider_manifests: Record<string, { protocol_version: number; manifest_digest: string }> }
  assert.equal(def.provider_manifests['weixin']!.protocol_version, 1)
  assert.ok(!def.capabilities.includes('weixin_message_send_v2'))
  // 显式协商 v2Send：manifest 升级 v2 变体（cli 启动注入，此处直接模拟）后，
  // 上报协议 2、摘要 = v2 变体摘要、能力含 v2 发送
  setManifestOverride('weixin', weixinV2Manifest())
  try {
    const v2cfg: Record<string, unknown> = { ...baseConfig, providers: { weixin: { entry: existingEntry, v2Send: true } } }
    const negotiated = deviceCapabilities(v2cfg as never) as typeof def
    const v2 = weixinV2Manifest()
    assert.equal(negotiated.provider_manifests['weixin']!.protocol_version, 2)
    assert.equal(negotiated.provider_manifests['weixin']!.manifest_digest, manifestDigestOf(v2))
    assert.ok(negotiated.capabilities.includes('weixin_message_send_v2'))
  } finally {
    setManifestOverride('weixin', null)
  }
  // 撤销后回归
  const reverted = deviceCapabilities(baseConfig as never) as typeof def
  assert.equal(reverted.provider_manifests['weixin']!.protocol_version, 1)
  assert.ok(!reverted.capabilities.includes('weixin_message_send_v2'))
})

test('空Host与缺失entry不上报业务能力，避免领取无法执行的任务', () => {
  for (const config of [null, {server: 'http://isolated.invalid', device_id: 'empty'}, makeConfig({bossCliEntry: 'C:/missing/boss.js', providers: {weixin: {entry: 'C:/missing/weixin.js'}}})]) {
    const caps = deviceCapabilities(config)
    assert.deepEqual(caps.providers, [])
    assert.deepEqual(caps.provider_manifests, {})
    assert.equal(caps.provider_id, undefined)
    assert.ok(!(caps.capabilities as string[]).includes('session_task_v1'))
  }
})

// ----- 契约 2.20：商店插件派生 manifest 与并行声明上报 -----

/** 商店验签清单 fixture（未预知插件：一串行一并行，注解只读） */
const TEST_TOOL_MANIFEST = {
  manifest_version: 1, provider_id: 'ai.aidwork.test-tool', provider_version: '1.0.0',
  entrypoint: ['dist/cli.js', 'mcp', '--stdio'], execution_target: 'local_required',
  tools: [
    { name: 'test_tool_status', annotations: { readOnlyHint: true } },
    { name: 'test_tool_poll', annotations: { readOnlyHint: true } },
  ],
  tool_declaration: { version: 1, tools: [{ name: 'test_tool_status' }, { name: 'test_tool_poll', parallel: true }] },
} as unknown as Parameters<typeof deriveProviderManifest>[0]

test('商店声明上报：派生 manifest 进 provider_manifests，parallel_tools 仅并行工具，legacy 直配不参与', () => {
  const derived = deriveProviderManifest(TEST_TOOL_MANIFEST)
  const caps = deviceCapabilities(makeConfig({
    providers: { 'test-tool': { entry: existingEntry }, 'boss-recruiting': { entry: existingEntry } },
  }), false, false, false, false, false, {
    parallelTools: { 'test-tool': ['test_tool_poll'] }, hasDeclarations: true, manifests: { 'test-tool': derived },
  })
  assert.deepEqual(caps['providers'], ['boss-recruiting', 'test-tool'], 'boss 恒首位键序契约保持')
  assert.equal(caps['provider_id'], 'ai.aidwork.boss-recruiting')
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.equal(manifests['test-tool']!['provider_id'], 'ai.aidwork.test-tool')
  assert.equal(manifests['test-tool']!['manifest_digest'], manifestDigestOf(derived as ProviderManifest))
  assert.equal(manifests['test-tool']!['protocol_version'], 1)
  assert.deepEqual(caps['parallel_tools'], { 'test-tool': ['test_tool_poll'] })
  assert.ok((caps['capabilities'] as string[]).includes('tool_declarations_v1'))
  // 未预知插件不产生工具名能力串（云端投影按既有能力协商，Phase 2 才消费 parallel_tools）
  assert.ok(!(caps['capabilities'] as string[]).includes('test_tool_status'))
})

test('商店派生 manifest 优先于 TRUSTED 回退（同 key 摘要随派生结果变化）', () => {
  const narrowed: ProviderManifest = { ...TRUSTED_MANIFESTS.wecom!, tools: TRUSTED_MANIFESTS.wecom!.tools.filter(name => name !== 'wecom_unread_list') }
  // 业务就绪布尔全真：wecom 业务条件过滤为恒等，摘要差异完全来自派生 manifest
  const caps = deviceCapabilities(makeConfig({ providers: { wecom: { entry: existingEntry } } }),
    true, true, false, true, true, { hasDeclarations: true, manifests: { wecom: narrowed } })
  const manifests = caps['provider_manifests'] as Record<string, Record<string, unknown>>
  assert.equal(manifests['wecom']!['manifest_digest'], manifestDigestOf(narrowed))
  assert.notEqual(manifests['wecom']!['manifest_digest'], manifestDigestFor('wecom'), '派生结果优先于 TRUSTED 回退')
  assert.ok((caps['capabilities'] as string[]).includes('tool_declarations_v1'))
  assert.equal(caps['parallel_tools'], undefined, '无并行工具时整个字段省略')
})
