/**
 * 插件调度声明（契约 2.20 修订：声明即调度授权）：清单 tool_declaration 宽松解析、
 * 通用 ProviderManifest 派生（受信面 = 声明 tools 全集，无历史名单交集锚定）、
 * 已交付三插件派生结果与 TRUSTED_MANIFESTS 逐字段一致。
 *
 * 三插件 fixture 工具名单与 readOnlyHint 镜像三个 CLI 的 buildManifest 实况
 * （boss 25 工具/14 写、wecom 17 工具/4 写、weixin 8 工具/1 写+v2 写），
 * 与各 CLI tests/manifest.test.ts 的「注解派生写集合 == 原写集合清单」断言互为锁。
 * 声明子集镜像各 CLI buildToolDeclaration 白名单（wecom 12、weixin 6 个 v1 工具）。
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
// 与 wecom CLI TOOL_DEFS 一致（17 项）；写 = WECOM_WRITE_TOOLS（4）
const WECOM_TOOLS = [
  'wecom_probe', 'wecom_chat_search', 'wecom_chat_select', 'wecom_message_send', 'wecom_send_status',
  'wecom_send_image', 'wecom_send_file', 'wecom_unread_list', 'wecom_read_session',
  'wecom_session_directory', 'wecom_read_sessions', 'wecom_watch_poll', 'wecom_group_list',
  'wecom_group_read', 'wecom_group_poll', 'wecom_config_doctor', 'wecom_config_calibrate',
]
const WECOM_WRITE = ['wecom_message_send', 'wecom_send_image', 'wecom_send_file', 'wecom_config_calibrate']
const WECOM_PARALLEL = ['wecom_read_session', 'wecom_read_sessions']
// wecom 声明白名单（CLI buildToolDeclaration）：12 个受信工具；5 个内部命令不声明
const WECOM_DECLARED = [
  'wecom_probe', 'wecom_message_send', 'wecom_send_status', 'wecom_send_image', 'wecom_send_file',
  'wecom_unread_list', 'wecom_read_session', 'wecom_session_directory', 'wecom_read_sessions',
  'wecom_group_poll', 'wecom_config_doctor', 'wecom_config_calibrate',
]
// 与 weixin CLI TOOL_DEFS 一致（8 项，含 v2 协商专用 2 项）；写 = message_send + v2 发送
const WEIXIN_TOOLS = [
  'weixin_probe', 'weixin_chat_search', 'weixin_message_send', 'weixin_history_read',
  'weixin_session_observe', 'weixin_unread_list', 'weixin_name_resolve', 'weixin_message_send_v2',
]
const WEIXIN_WRITE = ['weixin_message_send', 'weixin_message_send_v2']
// weixin 声明白名单（CLI buildToolDeclaration）：6 个 v1 工具；session_observe/v2 发送不声明
const WEIXIN_DECLARED = [
  'weixin_probe', 'weixin_chat_search', 'weixin_message_send', 'weixin_history_read',
  'weixin_unread_list', 'weixin_name_resolve',
]

const tools = (names, write) => names.map(name => ({ name, annotations: { readOnlyHint: !write.includes(name) } }))
const declaration = (names, parallel = []) => ({
  version: 1, tools: names.map(name => (parallel.includes(name) ? { name, parallel: true } : { name })),
})
const pluginManifest = (pluginId, names, write, parallel = [], declared = names, extra = {}) => ({
  manifest_version: 1, provider_id: pluginId, provider_version: '1.0.0',
  tools: tools(names, write), tool_declaration: declaration(declared, parallel), ...extra,
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

test('已交付三插件派生结果与 TRUSTED_MANIFESTS 逐字段一致（声明已收敛到同一集合，12/6/25）', () => {
  const cases = [
    ['ai.aidwork.boss-recruiting', BOSS_TOOLS, BOSS_WRITE, [], BOSS_TOOLS, 'boss-recruiting'],
    ['ai.aidwork.wecom', WECOM_TOOLS, WECOM_WRITE, WECOM_PARALLEL, WECOM_DECLARED, 'wecom'],
    ['ai.aidwork.weixin', WEIXIN_TOOLS, WEIXIN_WRITE, [], WEIXIN_DECLARED, 'weixin'],
  ]
  for (const [pluginId, names, write, parallel, declared, key] of cases) {
    const derived = deriveProviderManifest(pluginManifest(pluginId, names, write, parallel, declared))
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

test('未预知插件与已交付插件同一逻辑：受信面 = 声明 tools 全集', () => {
  // 未知插件：无历史名单可比对，工具名单与写集合完全来自声明
  const unknown = deriveProviderManifest(pluginManifest('ai.aidwork.test-tool', ['test_tool_status', 'test_tool_send'], ['test_tool_send']))
  assert.equal(unknown.provider_key, 'test-tool')
  assert.deepEqual([...unknown.tools].sort(), ['test_tool_send', 'test_tool_status'])
  assert.deepEqual([...unknown.write_tools], ['test_tool_send'])
  assert.equal(unknown.protocol_version, 1)
  assert.equal(unknown.shared_lock_capable, false)
  assert.equal(unknown.execution_target, 'local_required')
})

test('清单未声明的内部工具不进受信面（wecom 5 个内部命令、weixin v2 协商专用 2 项）', () => {
  // wecom 清单 17 项 vs 声明 12 项：内部命令不经云端派发，不进声明也不进受信面
  const wecom = deriveProviderManifest(pluginManifest('ai.aidwork.wecom', WECOM_TOOLS, WECOM_WRITE, WECOM_PARALLEL, WECOM_DECLARED))
  assert.equal(wecom.tools.length, 12, 'wecom 受信面 = 声明 12 工具')
  for (const tool of ['wecom_chat_search', 'wecom_chat_select', 'wecom_watch_poll', 'wecom_group_list', 'wecom_group_read']) {
    assert.ok(!wecom.tools.includes(tool), `${tool} 未声明，不得进入受信面`)
    assert.ok(!wecom.write_tools.has(tool), `${tool} 不得派生为写`)
  }
  // 写分类仍按清单 annotations：声明内 4 个写工具照常派生
  assert.deepEqual([...wecom.write_tools].sort(), [...WECOM_WRITE].sort())
  assert.ok(wecom.tools.includes('wecom_config_calibrate'), '声明内的配置工具照常受信')

  // weixin 清单 8 项 vs 声明 6 项：v2 协商专用 2 项不声明（v2 override 机制不受声明驱动）
  const weixin = deriveProviderManifest(pluginManifest('ai.aidwork.weixin', WEIXIN_TOOLS, WEIXIN_WRITE, [], WEIXIN_DECLARED))
  assert.equal(weixin.tools.length, 6, 'weixin 受信面 = 声明 6 个 v1 工具')
  for (const tool of ['weixin_session_observe', 'weixin_message_send_v2']) {
    assert.ok(!weixin.tools.includes(tool), `${tool} 未声明，不得进入受信面`)
  }
  assert.deepEqual([...weixin.write_tools].sort(), ['weixin_message_send'], '写集合仅 v1 发送（与 TRUSTED v1 受信面一致）')
})

test('assembleStoreManifests：派生 manifest、并行工具与声明全集同源上报（legacy 直配不参与）', () => {
  const declared = [
    { plugin_id: 'ai.aidwork.wecom', manifest: pluginManifest('ai.aidwork.wecom', WECOM_TOOLS, WECOM_WRITE, WECOM_PARALLEL, WECOM_DECLARED), declaration: declaration(WECOM_DECLARED, WECOM_PARALLEL) },
    { plugin_id: 'ai.aidwork.test-tool', manifest: pluginManifest('ai.aidwork.test-tool', ['test_tool_status'], []), declaration: declaration(['test_tool_status']) },
  ]
  const assembled = assembleStoreManifests(declared)
  assert.deepEqual(Object.keys(assembled.manifests).sort(), ['test-tool', 'wecom'])
  assert.deepEqual(assembled.parallelTools, { wecom: ['wecom_read_session', 'wecom_read_sessions'] },
    '全串行插件不出现在 parallel_tools')
  assert.deepEqual(assembled.declaredTools, {
    wecom: WECOM_DECLARED,
    'test-tool': ['test_tool_status'],
  }, 'declared_tools = 声明全集映射，与 parallel_tools 同源')
})
