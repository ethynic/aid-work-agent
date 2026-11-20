/**
 * 契约 2.20 Runtime 端到端：未预知插件（ai.aidwork.test-tool）按商店调度声明派生的
 * manifest 接入执行，心跳上报 parallel_tools 与 tool_declarations_v1。
 * TRUSTED_MANIFESTS 无该插件条目——Runtime 源码零改动接入的唯一通路是启动装配
 * （assembleStoreManifests）注入 runnerDeps.manifests 与能力上报。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import type { RuntimeManifest } from '@aid/local-tool-host-core'
import { ApiClient } from '../src/apiClient.js'
import { deviceCapabilities, saveConfig } from '../src/config.js'
import { deriveProviderManifest } from '../src/providers.js'
import { manifestDigestOf, type ProviderManifest } from '../src/providers.js'
import type { ProviderSet } from '../src/providerManager.js'
import { LegacyExecution } from '../src/runtimeHost.js'
import { FakeCloud } from './helpers/fakeCloud.js'
import { fakeProviderEntry, startTestStack, type TestStack } from './helpers/runtimeStack.js'

// 商店验签清单 fixture（等价 PluginStore 派生输入）：两个工具，一串行一并行（企微本地库读先例的抽象样例）
const TEST_TOOL_MANIFEST = {
  manifest_version: 1, provider_id: 'ai.aidwork.test-tool', provider_version: '1.0.0',
  entrypoint: ['dist/cli.js', 'mcp', '--stdio'], execution_target: 'local_required',
  tools: [
    { name: 'test_tool_status', annotations: { readOnlyHint: true } },
    { name: 'test_tool_poll', annotations: { readOnlyHint: true } },
  ],
  tool_declaration: { version: 1, tools: [{ name: 'test_tool_status' }, { name: 'test_tool_poll', parallel: true }] },
} as unknown as RuntimeManifest

function capabilitiesClosure(): () => Record<string, unknown> {
  const derived = deriveProviderManifest(TEST_TOOL_MANIFEST)
  return () => deviceCapabilities(
    { server: 'http://127.0.0.1:1', device_id: 'dev-e2e', providers: { 'test-tool': { entry: fakeProviderEntry() } } },
    false, false, false, false, false,
    {
      parallelTools: { 'test-tool': ['test_tool_poll'] },
      declaredTools: { 'test-tool': ['test_tool_status', 'test_tool_poll'] },
      hasDeclarations: true, manifests: { 'test-tool': derived },
    })
}

test('未预知插件按派生 manifest 路由执行，串行工具无写门禁直接完成', async () => {
  const derived = deriveProviderManifest(TEST_TOOL_MANIFEST)
  assert.equal(derived.provider_key, 'test-tool')
  assert.deepEqual([...derived.tools].sort(), ['test_tool_poll', 'test_tool_status'])
  assert.deepEqual([...derived.write_tools], [], '两个工具注解只读，派生写集合为空')
  assert.equal(derived.protocol_version, 1)
  assert.equal(derived.shared_lock_capable, false)

  const stack = await startTestStack({
    providerEntries: { 'test-tool': fakeProviderEntry() },
    manifests: { 'test-tool': derived },
    capabilities: capabilitiesClosure(),
  })
  try {
    const id = stack.cloud.enqueueInvocation('test_tool_status', {}, { provider: 'test-tool' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'test_tool_status 终态')
    const result = stack.cloud.callsFor(id).find(call => call.type === 'result')
    assert.ok(result, '结果沿原通路回传')
    assert.equal((result!.payload as { success?: boolean }).success, true)
  } finally {
    await stack.stop()
  }
})

test('心跳上报 parallel_tools、declared_tools 与 tool_declarations_v1（FakeCloud 捕获 capabilities_json）', async () => {
  let stack: TestStack | null = null
  try {
    stack = await startTestStack({
      providerEntries: { 'test-tool': fakeProviderEntry() },
      capabilities: capabilitiesClosure(),
    })
    await stack.cloud.waitFor(() => {
      const body = stack!.cloud.lastHeartbeatBody(stack!.token)
      return Boolean((body?.['capabilities'] as Record<string, unknown> | undefined)?.['parallel_tools'])
    }, 10_000, '心跳携带 parallel_tools')
    const body = stack.cloud.lastHeartbeatBody(stack.token)!
    const capabilities = body['capabilities'] as Record<string, unknown>
    assert.deepEqual(capabilities['parallel_tools'], { 'test-tool': ['test_tool_poll'] },
      '仅并行工具入表；全串行插件不出现键')
    assert.deepEqual(capabilities['declared_tools'], { 'test-tool': ['test_tool_status', 'test_tool_poll'] },
      '声明全集映射随心跳上报（服务端 claim 放行的声明依据）')
    assert.ok((capabilities['capabilities'] as string[]).includes('tool_declarations_v1'))
    const manifests = capabilities['provider_manifests'] as Record<string, Record<string, unknown>>
    assert.equal(manifests['test-tool']!['provider_id'], 'ai.aidwork.test-tool')
    assert.equal(manifests['test-tool']!['protocol_version'], 1)
    assert.equal((capabilities as { provider_id?: string }).provider_id, 'ai.aidwork.test-tool',
      '首个可用 provider 兼容字段取派生 manifest 的 provider_id')
  } finally {
    await stack?.stop()
  }
})

test('无并行工具声明仅上报 tool_declarations_v1 与 declared_tools，不出现 parallel_tools 键', async () => {
  const derived: ProviderManifest = { ...deriveProviderManifest(TEST_TOOL_MANIFEST) }
  const caps = deviceCapabilities(
    { server: 'http://127.0.0.1:1', device_id: 'dev-e2e', providers: { 'test-tool': { entry: fakeProviderEntry() } } },
    false, false, false, false, false,
    { parallelTools: {}, declaredTools: { 'test-tool': ['test_tool_status', 'test_tool_poll'] }, hasDeclarations: true, manifests: { 'test-tool': derived } })
  assert.equal(caps['parallel_tools'], undefined, '无并行工具的 provider 不出现键，全空则字段省略')
  assert.deepEqual(caps['declared_tools'], { 'test-tool': ['test_tool_status', 'test_tool_poll'] })
  assert.ok((caps['capabilities'] as string[]).includes('tool_declarations_v1'))
})

test('商店微信声明优先于存量 v2Send，配对和心跳同源且不向微信传设备凭据', {
  skip: process.platform !== 'win32', timeout: 30_000,
}, async (t) => {
  const home = mkdtempSync(join(tmpdir(), 'aid-weixin-store-'))
  const cloud = new FakeCloud()
  await cloud.start()
  const adapter = new LegacyExecution(process.execPath, home, undefined, true)
  const tools = [
    { name: 'weixin_probe', annotations: { readOnlyHint: true } },
    { name: 'weixin_message_send', annotations: { readOnlyHint: false } },
  ]
  const declaration = { version: 1 as const, tools: tools.map(({ name }) => ({ name })) }
  const manifest = {
    ...TEST_TOOL_MANIFEST, provider_id: 'ai.aidwork.weixin', tools, tool_declaration: declaration,
  }
  const expected = { provider_id: manifest.provider_id, manifest_digest: manifestDigestOf(deriveProviderManifest(manifest)), protocol_version: 1 }
  const entry = fakeProviderEntry()
  const pair = t.mock.method(ApiClient.prototype, 'pair')
  try {
    await adapter.initialize()
    saveConfig({ server: cloud.baseUrl, device_id: 'legacy', providers: { weixin: { entry, v2Send: true } } })
    // Substitute only the already verified store boundary. Pair/start use the real Host and HTTP loopback.
    Object.assign(adapter, { pluginStore: {
      async refresh() {},
      resolveEntries: () => ({ weixin: entry }),
      declaredPlugins: () => [{ plugin_id: manifest.provider_id, provider_key: 'weixin', manifest, declaration,
        envelope: { manifest_digest: `sha256:${'a'.repeat(64)}` } }],
    } })
    cloud.issueCode('SIGNEDWX')
    await adapter.pair({ server: cloud.baseUrl, device_name: 'fixture', pairing_code: 'SIGNEDWX' })
    assert.deepEqual(pair.mock.calls[0]?.arguments[0].capabilities.provider_manifests, { weixin: expected })
    await adapter.start(() => {}, () => {})
    const token = cloud.lastPairToken!
    await cloud.waitFor(() => Boolean(cloud.lastHeartbeatBody(token)), 10_000, '商店微信心跳')
    const caps = cloud.lastHeartbeatBody(token)!.capabilities as Record<string, unknown>
    assert.deepEqual(caps.provider_manifests, { weixin: expected })
    assert.deepEqual(caps.declared_tools, { weixin: tools.map(({ name }) => name) })
    assert.deepEqual(caps.plugin_manifest_digests, { weixin: `sha256:${'a'.repeat(64)}` })
    assert.ok(!(caps.capabilities as string[]).includes('weixin_message_send_v2'))

    const providers = (adapter as unknown as { providers: ProviderSet }).providers
    const wxManager = providers.get('weixin') as unknown as { providerEnv: Record<string, string> }
    assert.deepEqual(wxManager.providerEnv, { AID_WEIXIN_SERVER_URL: cloud.baseUrl })

    const request = cloud.enqueueInvocation('weixin_message_send_v2', {}, { provider: 'weixin' })
    await cloud.waitFor(() => cloud.callsFor(request).some(call =>
      call.type === 'operation_result' || call.type === 'result'), 10_000, '旧 v2 拒绝')
    const calls = cloud.callsFor(request)
    assert.ok(!calls.some(call => call.type === 'started'))
    const result = calls.find(call => call.type === 'operation_result' || call.type === 'result')
    assert.equal(result?.payload.code, 'PROTOCOL_NOT_SUPPORTED')
    assert.equal(providers.get('weixin').isRunning, false)
  } finally {
    await adapter.stop()
    adapter.onDisposed()
    await cloud.stop()
    rmSync(home, { recursive: true, force: true })
  }
})
