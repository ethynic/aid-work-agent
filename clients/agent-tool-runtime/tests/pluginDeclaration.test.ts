/**
 * 契约 2.20 Runtime 端到端：未预知插件（ai.aidwork.test-tool）按商店调度声明派生的
 * manifest 接入执行，心跳上报 parallel_tools 与 tool_declarations_v1。
 * TRUSTED_MANIFESTS 无该插件条目——Runtime 源码零改动接入的唯一通路是启动装配
 * （assembleStoreManifests）注入 runnerDeps.manifests 与能力上报。
 */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
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

test('商店微信声明优先于存量 v2Send，配对和心跳同源，为好友视觉转交现有配对身份', {
  skip: process.platform !== 'win32', timeout: 30_000,
}, async (t) => {
  const home = mkdtempSync(join(tmpdir(), 'aid-weixin-store-'))
  const cloud = new FakeCloud()
  await cloud.start()
  const adapter = new LegacyExecution(process.execPath, home, undefined, true)
  const tools = [
    { name: 'weixin_probe', annotations: { readOnlyHint: true } },
    { name: 'weixin_message_send', annotations: { readOnlyHint: false } },
    { name: 'weixin_friend_add', annotations: { readOnlyHint: false } },
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
      async verifyEnabledPlugins() {},
      resetExecutionChecks() {},
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
    assert.deepEqual(wxManager.providerEnv, {
      AID_WEIXIN_SERVER_URL: cloud.baseUrl, AIDWORK_RUNTIME_SERVER: cloud.baseUrl, AIDWORK_RUNTIME_DEVICE_TOKEN: token,
    })

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

test('Runtime 在成功心跳后开始后台检查，检查期间继续心跳和接单', {
  skip: process.platform !== 'win32', timeout: 35_000,
}, async t => {
  const home = mkdtempSync(join(tmpdir(), 'aid-wecom-first-use-'))
  const cloud = new FakeCloud()
  await cloud.start()
  const adapter = new LegacyExecution(process.execPath, home, undefined, true)
  const stringValue = { type: 'string' }
  const stringArray = { type: 'array', items: stringValue }
  const integer = { type: 'integer' }
  const tools = [
    ['wecom_read_session', { session: stringValue }],
    ['wecom_read_sessions', { sessions: stringArray, cursor: stringValue, limit: integer }],
    ['wecom_message_send', { session: stringValue, text: stringValue }],
    ['wecom_session_directory', { mode: { ...stringValue, enum: ['search'] }, session: stringValue, sessions: stringArray,
      query: stringValue, type: { ...stringValue, enum: ['contact', 'group', 'any'] }, limit: integer }],
    ['wecom_config_doctor', {}],
  ].map(([name, properties]) => ({ name: String(name), inputSchema: { type: 'object', properties }, annotations: { readOnlyHint: name !== 'wecom_message_send' } }))
  const wecomDeclaration = { version: 1 as const, tools: tools.map(({ name }) => ({ name,
    ...(name === 'wecom_session_directory' ? { parallel: true } : {}) })) }
  const wecom = { ...TEST_TOOL_MANIFEST, provider_id: 'ai.aidwork.wecom', tools, tool_declaration: wecomDeclaration }
  const digest = (hex: string) => `sha256:${hex.repeat(64)}`
  const checks: string[] = []
  const events: string[] = []
  let releaseHeartbeat!: () => void
  const firstResponse = new Promise<void>(resolve => { releaseHeartbeat = resolve })
  const heartbeat = ApiClient.prototype.heartbeat
  let delayFirstResponse = true
  t.mock.method(ApiClient.prototype, 'heartbeat', async function(this: ApiClient, ...args: Parameters<ApiClient['heartbeat']>) {
    const response = await heartbeat.apply(this, args)
    if (delayFirstResponse) { delayFirstResponse = false; await firstResponse }
    return response
  })
  let wecomAvailable = true
  try {
    await adapter.initialize()
    // A missing entry represents late payload damage. Signed metadata alone must still let the device connect.
    Object.assign(adapter, { pluginStore: {
      async verifyEnabledPlugins(signal: AbortSignal) {
        events.push('background-started')
        await new Promise<void>(resolve => signal.addEventListener('abort', () => resolve(), { once: true }))
        events.push('background-stopped')
      },
      resetExecutionChecks() {},
      resolveEntries: () => ({ 'test-tool': fakeProviderEntry(), ...(wecomAvailable ? { wecom: join(home, 'missing.js') } : {}) }),
      declaredPlugins: () => [
        { plugin_id: TEST_TOOL_MANIFEST.provider_id, provider_key: 'test-tool', manifest: TEST_TOOL_MANIFEST, declaration: TEST_TOOL_MANIFEST.tool_declaration,
          envelope: { manifest_digest: digest('b') } },
        ...(wecomAvailable ? [{ plugin_id: wecom.provider_id, provider_key: 'wecom', manifest: wecom, declaration: wecomDeclaration,
          envelope: { manifest_digest: digest('a') } }] : []),
      ],
      async verifyForExecution(provider: string, _entry: string, atLaunch: boolean) {
        checks.push(`${provider}/${atLaunch}`)
        if (provider === 'wecom') { wecomAvailable = false; throw new Error('invalid payload fixture') }
      },
    } })
    cloud.issueCode('WCFIRST')
    await adapter.pair({ server: cloud.baseUrl, device_name: 'fixture', pairing_code: 'WCFIRST' })
    await adapter.executionMarker(randomUUID())
    const begin = () => adapter.start(status => { if (status === 'online') events.push('online') }, () => {})
    await begin()
    const token = cloud.lastPairToken!
    const capabilities = () => cloud.lastHeartbeatBody(token)?.capabilities as Record<string, unknown> | undefined
    await cloud.waitFor(() => Boolean(capabilities()), 6_000, '请求已发，成功响应仍未交给 Host')
    assert.deepEqual(events, [], 'Sending the heartbeat is insufficient to start the background queue')
    const early = cloud.enqueueInvocation('test_tool_status', {}, { provider: 'test-tool' })
    await cloud.waitFor(() => cloud.getInvocation(early)?.state === 'succeeded', 6_000, '已领取的业务沿原路径执行')
    assert.deepEqual(events, [], 'An accepted task does not wait for this execution window to receive its first heartbeat')
    assert.deepEqual(checks, ['test-tool/false', 'test-tool/true'])
    releaseHeartbeat()
    await cloud.waitFor(() => events.filter(event => event === 'online').length >= 2, 11_000, '后台暂停时仍收到两次心跳')
    assert.deepEqual(events, ['online', 'background-started', 'online'])
    const pool = (adapter as unknown as { providers: ProviderSet }).providers
    assert.deepEqual(checks, ['test-tool/false', 'test-tool/true'], 'The background queue must not start a business Provider')
    assert.equal(pool.get('wecom').isRunning, false)
    const advertised = capabilities()!.capabilities as string[]
    for (const name of ['wecom_read_sessions', 'wecom_session_directory', 'wecom_session_directory_search', 'wecom_config_doctor']) {
      assert.ok(advertised.includes(name), `Signed tools advertise ${name} without an MCP process`)
    }
    assert.deepEqual(capabilities()!.plugin_manifest_digests, { wecom: digest('a'), 'test-tool': digest('b') })
    assert.deepEqual((capabilities()!.parallel_tools as Record<string, string[]>).wecom, ['wecom_session_directory'])

    const a = cloud.enqueueInvocation('wecom_session_directory', {}, { provider: 'wecom', parallel: true })
    await cloud.waitFor(() => cloud.getInvocation(a)?.state === 'failed', 7_000, '损坏插件确定未执行')
    const aResult = cloud.callsFor(a).find(call => call.type === 'result')!
    assert.equal(aResult.payload.code, 'PROVIDER_NOT_AVAILABLE')
    assert.equal(aResult.payload.effect, 'none')
    assert.equal(pool.get('wecom').isRunning, false)
    assert.deepEqual(checks.slice(2), ['wecom/false'])

    const b = cloud.enqueueInvocation('test_tool_status', {}, { provider: 'test-tool' })
    await cloud.waitFor(() => cloud.getInvocation(b)?.state === 'succeeded', 7_000, '未受影响的插件完成')
    assert.deepEqual(checks.slice(2), ['wecom/false', 'test-tool/false'])
    await cloud.waitFor(() => !(capabilities()?.providers as string[] | undefined)?.includes('wecom'), 11_000, '心跳撤回')
    assert.deepEqual(capabilities()!.plugin_manifest_digests, { 'test-tool': digest('b') })
    assert.deepEqual(capabilities()!.declared_tools, { 'test-tool': ['test_tool_status', 'test_tool_poll'] })
    assert.deepEqual(capabilities()!.parallel_tools, { 'test-tool': ['test_tool_poll'] })
    await adapter.stop()
    assert.equal(events.at(-1), 'background-stopped')
    await begin()
    await cloud.waitFor(() => events.filter(event => event === 'background-started').length === 2, 6_000, '重新开始只启动本轮后台检查')
    assert.deepEqual(events.slice(-3), ['background-stopped', 'online', 'background-started'])
  } finally {
    releaseHeartbeat()
    await adapter.stop()
    adapter.onDisposed()
    await cloud.stop()
    rmSync(home, { recursive: true, force: true })
  }
})
