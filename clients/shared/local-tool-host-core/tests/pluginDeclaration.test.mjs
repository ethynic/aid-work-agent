/**
 * 插件调度声明（契约 2.20）：清单 tool_declaration 宽松解析、通用 ProviderManifest 派生、
 * 已交付三插件派生结果与 TRUSTED_MANIFESTS 逐字段一致。
 *
 * 三插件 fixture 工具名单与 readOnlyHint 镜像三个 CLI 的 buildManifest 实况
 * （boss 25 工具/14 写、wecom 17 工具/4 写、weixin 8 工具/1 写+v2 写），
 * 与各 CLI tests/manifest.test.ts 的「注解派生写集合 == 原写集合清单」断言互为锁。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseToolDeclaration } from '../dist/plugins/package.js'
import { assembleStoreManifests, deriveProviderManifest, providerKeyOfPlugin, TRUSTED_MANIFESTS } from '../dist/legacy/providers.js'

// 与 boss CLI TOOL_DEFS 一致（含 boss_recovery_probe）；写集合 = BOSS_WRITE_TOOLS（14）
const BOSS_TOOLS = [
  'boss_recovery_probe', 'boss_filter', 'boss_clear_filter', 'boss_goto', 'boss_greet',
  'boss_lookup_candidates', 'boss_greet_selected', 'boss_accept_resume', 'boss_reject_current',
  'boss_interview_demo', 'boss_send_to', 'boss_send_current', 'boss_read_chat', 'boss_open_chat',
  'boss_overlay_inspect', 'boss_overlay_dismiss', 'boss_list_jobs', 'boss_select_job',
  'boss_filter_options', 'boss_resume_detail', 'boss_resume_batch', 'boss_resume_next',
  'boss_open_detail', 'boss_greet_detail', 'boss_close_detail',
]
const BOSS_WRITE = [
  'boss_filter', 'boss_clear_filter', 'boss_greet', 'boss_greet_selected', 'boss_greet_detail',
  'boss_accept_resume', 'boss_reject_current', 'boss_interview_demo', 'boss_send_to',
  'boss_send_current', 'boss_select_job', 'boss_resume_detail', 'boss_resume_batch', 'boss_resume_next',
]
// 与 wecom CLI TOOL_DEFS 一致（17 项，含 runtime 受信面之外的 5 个内部工具）；写 = WECOM_WRITE_TOOLS（4）
const WECOM_TOOLS = [
  'wecom_probe', 'wecom_chat_search', 'wecom_chat_select', 'wecom_message_send', 'wecom_send_status',
  'wecom_send_image', 'wecom_send_file', 'wecom_unread_list', 'wecom_read_session',
  'wecom_session_directory', 'wecom_read_sessions', 'wecom_watch_poll', 'wecom_group_list',
  'wecom_group_read', 'wecom_group_poll', 'wecom_config_doctor', 'wecom_config_calibrate',
]
const WECOM_WRITE = ['wecom_message_send', 'wecom_send_image', 'wecom_send_file', 'wecom_config_calibrate']
const WECOM_PARALLEL = ['wecom_read_session', 'wecom_read_sessions']
// 与 weixin CLI TOOL_DEFS 一致（8 项，含 v2 协商专用 2 项）；写 = message_send + v2 发送
const WEIXIN_TOOLS = [
  'weixin_probe', 'weixin_chat_search', 'weixin_message_send', 'weixin_history_read',
  'weixin_session_observe', 'weixin_unread_list', 'weixin_name_resolve', 'weixin_message_send_v2',
]
const WEIXIN_WRITE = ['weixin_message_send', 'weixin_message_send_v2']

const tools = (names, write) => names.map(name => ({ name, annotations: { readOnlyHint: !write.includes(name) } }))
const declaration = (names, parallel = []) => ({
  version: 1, tools: names.map(name => (parallel.includes(name) ? { name, parallel: true } : { name })),
})
const pluginManifest = (pluginId, names, write, parallel = [], extra = {}) => ({
  manifest_version: 1, provider_id: pluginId, provider_version: '1.0.0',
  tools: tools(names, write), tool_declaration: declaration(names, parallel), ...extra,
})

test('parseToolDeclaration：合法声明原样保留（含 parallel 标记）', () => {
  const parsed = parseToolDeclaration(pluginManifest('ai.aidwork.wecom', ['a', 'b'], [], ['b']))
  assert.deepEqual(parsed, { version: 1, tools: [{ name: 'a' }, { name: 'b', parallel: true }] })
})

test('parseToolDeclaration：缺失、损坏、版本不符、未知或重复名一律按无声明处理', () => {
  const base = { manifest_version: 1, provider_id: 'x', tools: [{ name: 'a' }, { name: 'b' }] }
  assert.equal(parseToolDeclaration(base), undefined, '声明缺失 = 无声明')
  for (const broken of [
    undefined, null, 'declaration', 7, [], {},
    { version: 2, tools: [{ name: 'a' }] }, { version: '1', tools: [{ name: 'a' }] },
    { version: 1 }, { version: 1, tools: [] }, { version: 1, tools: 'a' },
    { version: 1, tools: ['a'] }, { version: 1, tools: [{}] }, { version: 1, tools: [{ name: 3 }] },
    { version: 1, tools: [{ name: 'missing' }] }, { version: 1, tools: [{ name: 'a' }, { name: 'a' }] },
    { version: 1, tools: [{ name: 'a', parallel: 'yes' }] },
  ]) {
    assert.equal(parseToolDeclaration({ ...base, tool_declaration: broken }), undefined, JSON.stringify(broken) ?? String(broken))
  }
  // tools 数组缺失（清单损坏）时声明也按无声明处理
  assert.equal(parseToolDeclaration({ manifest_version: 1, provider_id: 'x', tool_declaration: { version: 1, tools: [{ name: 'a' }] } }), undefined)
})

test('providerKeyOfPlugin：去除 ai.aidwork. 前缀，其余原样', () => {
  assert.equal(providerKeyOfPlugin('ai.aidwork.boss-recruiting'), 'boss-recruiting')
  assert.equal(providerKeyOfPlugin('ai.aidwork.weixin'), 'weixin')
  assert.equal(providerKeyOfPlugin('ai.aidwork.wecom'), 'wecom')
  assert.equal(providerKeyOfPlugin('ai.aidwork.test-tool'), 'test-tool')
  assert.equal(providerKeyOfPlugin('vendor.plugin'), 'vendor.plugin')
})

test('已交付三插件派生结果与 TRUSTED_MANIFESTS 逐字段一致（tools/写集合完全相同）', () => {
  const cases = [
    ['ai.aidwork.boss-recruiting', BOSS_TOOLS, BOSS_WRITE, [], 'boss-recruiting'],
    ['ai.aidwork.wecom', WECOM_TOOLS, WECOM_WRITE, WECOM_PARALLEL, 'wecom'],
    ['ai.aidwork.weixin', WEIXIN_TOOLS, WEIXIN_WRITE, [], 'weixin'],
  ]
  for (const [pluginId, names, write, parallel, key] of cases) {
    const derived = deriveProviderManifest(pluginManifest(pluginId, names, write, parallel))
    const trusted = TRUSTED_MANIFESTS[key]
    assert.ok(trusted, `TRUSTED_MANIFESTS 必须含 ${key}`)
    assert.equal(derived.provider_key, trusted.provider_key)
    assert.equal(derived.provider_id, trusted.provider_id)
    assert.equal(derived.execution_target, trusted.execution_target)
    assert.equal(derived.protocol_version, trusted.protocol_version)
    assert.equal(derived.shared_lock_capable, trusted.shared_lock_capable)
    assert.deepEqual([...derived.tools].sort(), [...trusted.tools].sort(), `${key} tools 逐工具一致`)
    assert.deepEqual([...derived.write_tools].sort(), [...trusted.write_tools].sort(), `${key} 写集合逐工具一致`)
  }
})

test('未预知插件完全由清单派生；清单多列内部工具在受信锚点处收敛不扩大', () => {
  // 未知插件：无锚点，工具名单与写集合完全来自清单
  const unknown = deriveProviderManifest(pluginManifest('ai.aidwork.test-tool', ['test_tool_status', 'test_tool_send'], ['test_tool_send']))
  assert.equal(unknown.provider_key, 'test-tool')
  assert.deepEqual([...unknown.tools].sort(), ['test_tool_send', 'test_tool_status'])
  assert.deepEqual([...unknown.write_tools], ['test_tool_send'])
  assert.equal(unknown.protocol_version, 1)
  assert.equal(unknown.shared_lock_capable, false)
  assert.equal(unknown.execution_target, 'local_required')

  // 已交付插件签名清单多列内部工具（wecom 17 项 vs 受信 12 项）：受信面不扩大
  const wecom = deriveProviderManifest(pluginManifest('ai.aidwork.wecom', WECOM_TOOLS, WECOM_WRITE, WECOM_PARALLEL))
  for (const tool of ['wecom_chat_search', 'wecom_chat_select', 'wecom_watch_poll', 'wecom_group_list', 'wecom_group_read']) {
    assert.ok(!wecom.tools.includes(tool), `${tool} 不得因清单多列而进入受信面`)
  }
  assert.ok(!wecom.write_tools.has('wecom_chat_select'), '清单未标写的内部工具不得派生为写')
})

test('assembleStoreManifests：派生 manifest 与并行工具上报同源（legacy 直配不参与 parallel_tools）', () => {
  const declared = [
    { plugin_id: 'ai.aidwork.wecom', manifest: pluginManifest('ai.aidwork.wecom', WECOM_TOOLS, WECOM_WRITE, WECOM_PARALLEL), declaration: declaration(WECOM_TOOLS, WECOM_PARALLEL) },
    { plugin_id: 'ai.aidwork.test-tool', manifest: pluginManifest('ai.aidwork.test-tool', ['test_tool_status'], []), declaration: declaration(['test_tool_status']) },
  ]
  const assembled = assembleStoreManifests(declared)
  assert.deepEqual(Object.keys(assembled.manifests).sort(), ['test-tool', 'wecom'])
  assert.deepEqual(assembled.parallelTools, { wecom: ['wecom_read_session', 'wecom_read_sessions'] },
    '全串行插件不出现在 parallel_tools')
})
