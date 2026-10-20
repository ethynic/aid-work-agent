/**
 * Provider manifest 注册表：boss 21 工具逐项一致、历史 digest 兼容锚点、weixin/wecom
 * 白名单与写集合（M11b：wecom 接入 6 工具，digest 独立计算）。
 */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { test } from 'node:test'
import { TRUSTED_MANIFEST, isToolAllowed, isWriteTool, manifestDigest } from '../src/manifestVerifier.js'
import { getProviderManifest, isToolAllowedFor, isWriteToolFor, manifestDigestFor, TRUSTED_MANIFESTS } from '../src/providers.js'

/** 多 Provider 改造前实测的 boss digest（云端核对锚点，逐字节不得漂移） */
const BOSS_DIGEST_BASELINE = '322acbcb1a74724c54917c0ff88fe1fb106253077b176d234bc600d8bd71fe7e'
/** 既有续筛注册增加 boss_resume_next 后的当前锚点；传输预算不改变此值。 */
const BOSS_DIGEST_WITH_CONTINUATION = '9bef64b53e3ed2c35a903c96490553c3a0f705e35f4f6b2edf7c849dd958f216'
const BOSS_DIGEST_WITH_SELECTION = 'f1b9dd6d72cc7afa1d462ea56cefb7b269d96a594f4b4f064465febcc9ed522f'
const BOSS_DIGEST_WITH_OPEN_DETAIL = '93eccd9f4b9168343b5283cc650d643f0413c6b8cbb9fbedcdf4b15aa3be140c'

const BOSS_TOOLS_EXPECTED = [
  'boss_filter',
  'boss_clear_filter',
  'boss_filter_options',
  'boss_goto',
  'boss_greet',
  'boss_lookup_candidates',
  'boss_greet_selected',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_list_jobs',
  'boss_select_job',
  'boss_resume_detail',
  'boss_resume_batch',
  'boss_resume_next',
  'boss_read_chat',
  'boss_open_chat',
  'boss_overlay_inspect',
  'boss_overlay_dismiss',
  'boss_open_detail',
  'boss_greet_detail',
  'boss_close_detail',
]

const BOSS_WRITE_TOOLS_EXPECTED = new Set([
  'boss_filter',
  'boss_clear_filter',
  'boss_greet',
  'boss_greet_selected',
  'boss_greet_detail',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_select_job',
  'boss_resume_detail',
  'boss_resume_batch',
  'boss_resume_next',
])

test('boss manifest：24 个工具包含已实现的详情操作（顺序与集合）', () => {
  const boss = getProviderManifest('boss-recruiting')!
  assert.deepEqual([...boss.tools], BOSS_TOOLS_EXPECTED)
  assert.equal(boss.provider_id, 'ai.aidwork.boss-recruiting')
  assert.equal(boss.execution_target, 'local_required')
  // 兼容导出与注册表同一份内容
  assert.deepEqual([...TRUSTED_MANIFEST.tools], BOSS_TOOLS_EXPECTED)
  assert.equal(TRUSTED_MANIFEST.provider_id, boss.provider_id)
})

test('boss 写集合：详情打招呼保持外部写属性，关闭详情无外发副作用', () => {
  const boss = getProviderManifest('boss-recruiting')!
  for (const tool of BOSS_TOOLS_EXPECTED) {
    const expected = BOSS_WRITE_TOOLS_EXPECTED.has(tool)
    assert.equal(isWriteToolFor(boss, tool), expected, `${tool} 写属性应=${expected}`)
    assert.equal(isWriteTool(tool), expected, `兼容 isWriteTool(${tool}) 应=${expected}`)
  }
})

test('boss digest：新增精确选择工具锚点固定，移除新增工具仍匹配历史基线', () => {
  assert.equal(manifestDigest(), manifestDigestFor('boss-recruiting'))
  const prior = JSON.stringify({ provider_id: TRUSTED_MANIFEST.provider_id,
    tools: [...TRUSTED_MANIFEST.tools].filter(tool => !['boss_open_detail', 'boss_greet_detail', 'boss_close_detail'].includes(tool)).sort(), execution_target: 'local_required' })
  assert.equal(createHash('sha256').update(prior).digest('hex'), BOSS_DIGEST_WITH_SELECTION)
  assert.notEqual(manifestDigestFor('boss-recruiting'), BOSS_DIGEST_WITH_SELECTION)
  const boss = getProviderManifest('boss-recruiting')!
  const beforeDetailActions = JSON.stringify({ provider_id: boss.provider_id,
    tools: boss.tools.filter(tool => !['boss_greet_detail', 'boss_close_detail'].includes(tool)).sort(), execution_target: boss.execution_target })
  assert.equal(createHash('sha256').update(beforeDetailActions).digest('hex'), BOSS_DIGEST_WITH_OPEN_DETAIL)
  const preSelectionTools = boss.tools.filter((tool) => !['boss_open_detail', 'boss_greet_detail', 'boss_close_detail', 'boss_lookup_candidates', 'boss_greet_selected'].includes(tool))
  const preSelection = JSON.stringify({ provider_id: boss.provider_id, tools: [...preSelectionTools].sort(), execution_target: boss.execution_target })
  assert.equal(createHash('sha256').update(preSelection).digest('hex'), BOSS_DIGEST_WITH_CONTINUATION)
  const legacy = JSON.stringify({ provider_id: boss.provider_id,
    tools: preSelectionTools.filter((tool) => tool !== 'boss_resume_next').sort(), execution_target: boss.execution_target })
  assert.equal(createHash('sha256').update(legacy).digest('hex'), BOSS_DIGEST_BASELINE)
})

test('weixin manifest：名称定位只读，默认仅 message_send 为写，v2需显式启用', () => {
  const weixin = getProviderManifest('weixin')!
  assert.deepEqual([...weixin.tools], [
    'weixin_probe',
    'weixin_name_resolve',
    'weixin_chat_search',
    'weixin_message_send',
    'weixin_history_read',
    'weixin_unread_list',
  ])
  assert.equal(weixin.provider_id, 'ai.aidwork.weixin')
  assert.equal(weixin.execution_target, 'local_required')
  assert.equal(weixin.protocol_version, 1)
  assert.equal(weixin.shared_lock_capable, false)
  for (const tool of weixin.tools) {
    assert.equal(isWriteToolFor(weixin, tool), tool === 'weixin_message_send', `${tool} 写属性不符合现 CLI 写集合`)
  }
  assert.ok(isToolAllowedFor(weixin, 'weixin_message_send'))
  assert.ok(!isToolAllowedFor(weixin, 'weixin_message_send_v2'), 'v2 工具不得在未协商的 v1 白名单')
  assert.match(manifestDigestFor('weixin'), /^[0-9a-f]{64}$/)
  assert.notEqual(manifestDigestFor('weixin'), manifestDigestFor('boss-recruiting'), '各 Provider digest 应独立')
})

test('weixin v2 会话变体：仅显式协商后生效（setManifestOverride），运行期判断同源（#6）', async () => {
  const { setManifestOverride, weixinV2Manifest } = await import('../src/providers.js')
  try {
    setManifestOverride('weixin', weixinV2Manifest())
    const v2 = getProviderManifest('weixin')!
    assert.equal(v2.protocol_version, 2)
    assert.equal(v2.shared_lock_capable, true)
    assert.ok(isToolAllowedFor(v2, 'weixin_session_observe'), '观察工具在 v2 变体')
    assert.ok(isToolAllowedFor(v2, 'weixin_message_send_v2'), 'v2 发送在 v2 变体')
    assert.ok(isWriteToolFor(v2, 'weixin_message_send_v2'), 'v2 发送为写')
    assert.ok(!isWriteToolFor(v2, 'weixin_session_observe'), '观察只读')
  } finally {
    setManifestOverride('weixin', null)
  }
  const reverted = getProviderManifest('weixin')!
  assert.equal(reverted.protocol_version, 1, '撤销协商后回归真实 v1')
  assert.ok(!isToolAllowedFor(reverted, 'weixin_message_send_v2'))
})

test('wecom manifest：M11b 接入 6 工具清单一致，仅三个发送工具为写，v1 独占桌面锁', () => {
  const wecom = getProviderManifest('wecom')!
  assert.deepEqual([...wecom.tools], [
    'wecom_probe',
    'wecom_message_send',
    'wecom_send_image',
    'wecom_send_file',
    'wecom_read_session',
    'wecom_unread_list',
  ])
  assert.equal(wecom.provider_id, 'ai.aidwork.wecom')
  assert.equal(wecom.execution_target, 'local_required')
  assert.equal(wecom.protocol_version, 1)
  assert.equal(wecom.shared_lock_capable, false)
  for (const tool of wecom.tools) {
    const expected = tool === 'wecom_message_send' || tool === 'wecom_send_image' || tool === 'wecom_send_file'
    assert.equal(isWriteToolFor(wecom, tool), expected, `${tool} 写属性不符（读类 probe/unread/read_session 不进写集合）`)
  }
  // M11b 决策：search/select 不接入（send 内含 search 定位）；watch_poll/add_customer 不接入
  assert.ok(!isToolAllowedFor(wecom, 'wecom_chat_search'))
  assert.ok(!isToolAllowedFor(wecom, 'wecom_chat_select'))
  assert.ok(!isToolAllowedFor(wecom, 'wecom_watch_poll'))
  assert.ok(!isToolAllowedFor(wecom, 'wecom_add_customer'))
  // digest 独立计算（与 boss/weixin 各不相同，boss 基线不受影响——见上）
  assert.match(manifestDigestFor('wecom'), /^[0-9a-f]{64}$/)
  assert.notEqual(manifestDigestFor('wecom'), manifestDigestFor('boss-recruiting'), '各 Provider digest 应独立')
  assert.notEqual(manifestDigestFor('wecom'), manifestDigestFor('weixin'), '各 Provider digest 应独立')
})

test('白名单：跨 Provider 工具不串道，未知 provider 无 manifest', () => {
  const boss = getProviderManifest('boss-recruiting')!
  const weixin = getProviderManifest('weixin')!
  const wecom = getProviderManifest('wecom')!
  assert.ok(!isToolAllowedFor(weixin, 'boss_greet'), 'weixin manifest 不得放行 boss 工具')
  assert.ok(!isToolAllowedFor(boss, 'weixin_message_send'), 'boss manifest 不得放行 weixin 工具')
  assert.ok(!isToolAllowedFor(wecom, 'boss_greet'), 'wecom manifest 不得放行 boss 工具')
  assert.ok(!isToolAllowedFor(wecom, 'weixin_message_send'), 'wecom manifest 不得放行 weixin 工具')
  assert.ok(!isToolAllowedFor(boss, 'wecom_message_send'), 'boss manifest 不得放行 wecom 工具')
  assert.ok(!isToolAllowedFor(weixin, 'wecom_message_send'), 'weixin manifest 不得放行 wecom 工具')
  assert.ok(!isToolAllowed('weixin_message_send'), '兼容 isToolAllowed 仍按 boss 白名单判断')
  assert.equal(getProviderManifest('nope-unknown'), undefined)
  assert.equal(Object.keys(TRUSTED_MANIFESTS).length, 4)
})
