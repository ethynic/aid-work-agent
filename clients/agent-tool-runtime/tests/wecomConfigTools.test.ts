/**
 * 代理任务 P3（契约 §3）：wecom_config_doctor / wecom_config_calibrate 纳入 runtime 受信面。
 * 覆盖：真实能力发现（新 CLI / 旧 CLI / schema 变形夹具）→ 能力串与 wecom manifest digest；
 * 受信调用沿原 Device invocation/result 通路；calibrate 归写集合（锁屏前置），doctor 只读。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { ProviderManager } from '../src/providerManager.js'
import { deviceCapabilities, type RuntimeConfig } from '../src/config.js'
import { getProviderManifest, isToolAllowedFor, isWriteToolFor, manifestDigestFor, manifestDigestOf, TRUSTED_MANIFESTS } from '../src/providers.js'
import { fakeProviderEntry, startTestStack } from './helpers/runtimeStack.js'

const UNVERIFIED_ANCHOR_DIGEST = 'b7c278721d218db6c6fd2a9f07a4c543403f4fbb597a0c8d345a8c7f59f35279'

function wecomConfig(): RuntimeConfig {
  return { server: 'https://fixture.invalid', device_id: 'fixture', providers: { wecom: { entry: fakeProviderEntry() } } }
}

function capabilitiesOf(manager: ProviderManager, config: RuntimeConfig = wecomConfig()): string[] {
  return deviceCapabilities(config, manager.wecomBusinessReady, manager.wecomDirectoryReady,
    manager.wecomDirectorySearchReady, manager.wecomConfigDoctorReady, manager.wecomConfigCalibrateReady)
    .capabilities as string[]
}

/** digestOf = 按 deviceCapabilities 的真实过滤规则（含 session_directory/read_sessions/config 工具）计算摘要 */
function digestExcluding(excluded: string[]): string {
  return manifestDigestOf({ ...TRUSTED_MANIFESTS.wecom!, tools: TRUSTED_MANIFESTS.wecom!.tools.filter(name => !excluded.includes(name)) })
}

test('受信 manifest：两配置工具进名单并可放行，calibrate 归写集合、doctor 只读，未受信工具仍拒绝', () => {
  const wecom = getProviderManifest('wecom')!
  assert.ok(isToolAllowedFor(wecom, 'wecom_config_doctor'), 'doctor 进受信名单')
  assert.ok(isToolAllowedFor(wecom, 'wecom_config_calibrate'), 'calibrate 进受信名单')
  assert.equal(isWriteToolFor(wecom, 'wecom_config_calibrate'), true,
    'calibrate 与 CLI OPERATIONS cli.write=true 对齐：锁屏前置/崩溃 unknown 沿既有写语义，不另造门禁')
  assert.equal(isWriteToolFor(wecom, 'wecom_config_doctor'), false, 'doctor 只读诊断不进写集合')
  assert.ok(!isToolAllowedFor(wecom, 'wecom_chat_search'), '未受信工具仍被拒绝')
})

test('新 CLI 夹具：真实 schema 校验通过 → 两能力串上报、manifest 含两者、digest 由未验证锚点相应变化', async t => {
  const manager = new ProviderManager(fakeProviderEntry(), { env: { FAKE_WECOM_CONFIG_TOOLS: '1' } })
  t.after(() => manager.shutdown())
  const config = wecomConfig()
  // 配置 entry ≠ 能力：发现前不上报、digest 为旧 8 工具锚点（与改动前旧插件一致）
  const before = capabilitiesOf(manager)
  assert.ok(!before.includes('wecom_config_doctor'))
  assert.ok(!before.includes('wecom_config_calibrate'))
  assert.equal((deviceCapabilities(config).provider_manifests as Record<string, { manifest_digest: string }>).wecom!.manifest_digest,
    UNVERIFIED_ANCHOR_DIGEST, '未验证时 digest 与改动前 8 工具锚点逐字节一致')
  await manager.inspectWecomBusinessTools()
  assert.equal(manager.wecomConfigDoctorReady, true)
  assert.equal(manager.wecomConfigCalibrateReady, true)
  const capabilities = capabilitiesOf(manager)
  assert.ok(capabilities.includes('wecom_config_doctor'), 'doctor 能力串按真实发现上报')
  assert.ok(capabilities.includes('wecom_config_calibrate'), 'calibrate 能力串按真实发现上报')
  // manifest 工具名单含两者（业务面模型可见性由云端投影，host core 只维护受信面）
  const full = deviceCapabilities(config, manager.wecomBusinessReady, manager.wecomDirectoryReady,
    manager.wecomDirectorySearchReady, manager.wecomConfigDoctorReady, manager.wecomConfigCalibrateReady)
  const manifests = full.provider_manifests as Record<string, { manifest_digest: string }>
  assert.equal(manifests.wecom!.manifest_digest,
    digestExcluding(['wecom_session_directory']),
    'digest 排除未就绪的页面目录、包含两配置工具（目录未随本夹具开启）')
  assert.notEqual(manifests.wecom!.manifest_digest, UNVERIFIED_ANCHOR_DIGEST, '工具集变化 → digest 相应变化')
  // 四项能力全部就绪时 digest = 完整受信 manifest
  assert.equal((deviceCapabilities(config, true, true, false, true, true)
    .provider_manifests as Record<string, { manifest_digest: string }>).wecom!.manifest_digest, manifestDigestFor('wecom'))
})

test('旧 CLI 夹具：无两新工具 → 无新能力串、原 8 业务能力与摘要锚点保持不变', async t => {
  const manager = new ProviderManager(fakeProviderEntry(), { env: { FAKE_WECOM_LEGACY: '1' } })
  t.after(() => manager.shutdown())
  await manager.inspectWecomBusinessTools()
  assert.equal(manager.wecomConfigDoctorReady, false)
  assert.equal(manager.wecomConfigCalibrateReady, false)
  const capabilities = capabilitiesOf(manager)
  assert.ok(!capabilities.includes('wecom_config_doctor'))
  assert.ok(!capabilities.includes('wecom_config_calibrate'))
  // 旧插件原工具能力与改动前一致（businessReady=false：批量读取不上报）
  for (const tool of ['wecom_probe', 'wecom_message_send', 'wecom_send_status', 'wecom_group_poll',
    'wecom_send_image', 'wecom_send_file', 'wecom_read_session', 'wecom_unread_list']) {
    assert.ok(capabilities.includes(tool), `${tool} 原能力保持`)
  }
  assert.ok(!capabilities.includes('wecom_read_sessions'))
  const manifests = deviceCapabilities(wecomConfig(), manager.wecomBusinessReady, manager.wecomDirectoryReady,
    manager.wecomDirectorySearchReady, manager.wecomConfigDoctorReady, manager.wecomConfigCalibrateReady)
    .provider_manifests as Record<string, { manifest_digest: string }>
  assert.equal(manifests.wecom!.manifest_digest, UNVERIFIED_ANCHOR_DIGEST, '旧插件 digest 与改动前逐字节一致')
})

test('schema 变形夹具：doctor readOnlyHint 丢失 → doctor 能力不上报，calibrate 不受牵连', async t => {
  const manager = new ProviderManager(fakeProviderEntry(), { env: {
    FAKE_WECOM_CONFIG_TOOLS: '1', FAKE_WECOM_CONFIG_DOCTOR_MUTATED: '1' } })
  t.after(() => manager.shutdown())
  await manager.inspectWecomBusinessTools()
  assert.equal(manager.wecomConfigDoctorReady, false, 'readOnlyHint=false 不通过真实 schema 检查')
  assert.equal(manager.wecomConfigCalibrateReady, true)
  const capabilities = capabilitiesOf(manager)
  assert.ok(!capabilities.includes('wecom_config_doctor'))
  assert.ok(capabilities.includes('wecom_config_calibrate'))
  const manifests = deviceCapabilities(wecomConfig(), manager.wecomBusinessReady, manager.wecomDirectoryReady,
    manager.wecomDirectorySearchReady, manager.wecomConfigDoctorReady, manager.wecomConfigCalibrateReady)
    .provider_manifests as Record<string, { manifest_digest: string }>
  assert.equal(manifests.wecom!.manifest_digest, digestExcluding(['wecom_config_doctor', 'wecom_session_directory']),
    'digest 排除被裁剪的 doctor、仍包含 calibrate')
})

test('受信调用：wecom_config_doctor 经 Device invocation 执行，契约报告沿原 result 通路返回', async () => {
  process.env.FAKE_WECOM_CONFIG_TOOLS = '1'
  const stack = await startTestStack({ providerEntries: { wecom: fakeProviderEntry() } })
  try {
    const id = stack.cloud.enqueueInvocation('wecom_config_doctor', {}, { provider: 'wecom' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'doctor succeeded')
    const result = stack.cloud.callsFor(id).find(call => call.type === 'result')!
    assert.equal(result.payload.success, true)
    assert.equal(result.payload.effect, 'none', 'doctor 只读 → effect=none')
    const data = result.payload.data as Record<string, unknown>
    assert.equal(data.schema_version, 1)
    assert.equal(data.self_user_id, '1688fixture')
    assert.equal((data.localdb as Record<string, unknown>).localdb_ready, true, '契约 §2.1 报告经原通路完整回传')
    assert.equal(stack.cloud.callsFor(id).filter(call => call.type === 'started').length, 1)
  } finally { await stack.stop(); delete process.env.FAKE_WECOM_CONFIG_TOOLS }
})

test('受信调用：wecom_config_calibrate 成功结果沿原通路返回；锁屏时走写工具 DESKTOP_NOT_INTERACTIVE 前置', async () => {
  process.env.FAKE_WECOM_CONFIG_TOOLS = '1'
  const stack = await startTestStack({ providerEntries: { wecom: fakeProviderEntry() } })
  try {
    const id = stack.cloud.enqueueInvocation('wecom_config_calibrate', {}, { provider: 'wecom' })
    await stack.cloud.waitFor(() => stack.cloud.getInvocation(id)?.state === 'succeeded', 10_000, 'calibrate succeeded')
    const result = stack.cloud.callsFor(id).find(call => call.type === 'result')!
    assert.equal(result.payload.success, true)
    assert.equal(result.payload.effect, 'applied', 'calibrate 为写动作')
    const data = result.payload.data as Record<string, unknown>
    assert.deepEqual(data.title_point, { x: 1310, y: 218 })
    assert.equal(data.self_user_id, '1688fixture')
  } finally { await stack.stop() }

  const locked = await startTestStack({ providerEntries: { wecom: fakeProviderEntry() }, desktopCheck: async () => false })
  const callLog = join(locked.dataDir, 'calls.jsonl')
  try {
    const doctorId = locked.cloud.enqueueInvocation('wecom_config_doctor', {}, { provider: 'wecom' })
    await locked.cloud.waitFor(() => locked.cloud.getInvocation(doctorId)?.state === 'succeeded', 10_000,
      'doctor 只读不受锁屏前置限制')
    const calibrateId = locked.cloud.enqueueInvocation('wecom_config_calibrate', { call_log_file: callLog }, { provider: 'wecom' })
    await locked.cloud.waitFor(() => locked.cloud.callsFor(calibrateId).some(call => call.type === 'result'), 10_000,
      'calibrate rejected by lock screen')
    const rejected = locked.cloud.callsFor(calibrateId).find(call => call.type === 'result')!
    assert.equal(rejected.payload.code, 'DESKTOP_NOT_INTERACTIVE', '写集合语义生效：锁屏前置拦截')
    assert.equal(rejected.payload.effect, 'none')
    assert.equal(rejected.payload.retryable, true)
    assert.ok(!existsSync(callLog), '锁屏前置在 Provider 调用前拦截，未产生真实执行')
  } finally { await locked.stop(); delete process.env.FAKE_WECOM_CONFIG_TOOLS }
})
