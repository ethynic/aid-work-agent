/**
 * Provider manifest 注册表：boss 18 工具逐项一致、digest 兼容锚点、weixin 白名单与写集合。
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { TRUSTED_MANIFEST, isToolAllowed, isWriteTool, manifestDigest } from '../src/manifestVerifier.js'
import { getProviderManifest, isToolAllowedFor, isWriteToolFor, manifestDigestFor, TRUSTED_MANIFESTS } from '../src/providers.js'

/** 多 Provider 改造前实测的 boss digest（云端核对锚点，逐字节不得漂移） */
const BOSS_DIGEST_BASELINE = '322acbcb1a74724c54917c0ff88fe1fb106253077b176d234bc600d8bd71fe7e'

const BOSS_TOOLS_EXPECTED = [
  'boss_filter',
  'boss_clear_filter',
  'boss_filter_options',
  'boss_goto',
  'boss_greet',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_list_jobs',
  'boss_select_job',
  'boss_resume_detail',
  'boss_resume_batch',
  'boss_read_chat',
  'boss_open_chat',
  'boss_overlay_inspect',
  'boss_overlay_dismiss',
]

const BOSS_WRITE_TOOLS_EXPECTED = new Set([
  'boss_filter',
  'boss_clear_filter',
  'boss_greet',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_select_job',
  'boss_resume_detail',
  'boss_resume_batch',
])

test('boss manifest：18 个工具与改造前逐项一致（顺序与集合）', () => {
  const boss = getProviderManifest('boss-recruiting')!
  assert.deepEqual([...boss.tools], BOSS_TOOLS_EXPECTED)
  assert.equal(boss.provider_id, 'ai.aidwork.boss-recruiting')
  assert.equal(boss.execution_target, 'local_required')
  // 兼容导出与注册表同一份内容
  assert.deepEqual([...TRUSTED_MANIFEST.tools], BOSS_TOOLS_EXPECTED)
  assert.equal(TRUSTED_MANIFEST.provider_id, boss.provider_id)
})

test('boss 写集合：11 个写工具逐项一致，只读工具不在写集合', () => {
  const boss = getProviderManifest('boss-recruiting')!
  for (const tool of BOSS_TOOLS_EXPECTED) {
    const expected = BOSS_WRITE_TOOLS_EXPECTED.has(tool)
    assert.equal(isWriteToolFor(boss, tool), expected, `${tool} 写属性应=${expected}`)
    assert.equal(isWriteTool(tool), expected, `兼容 isWriteTool(${tool}) 应=${expected}`)
  }
})

test('boss digest：与改造前基线逐字节一致（manifestVerifier 与注册表同源）', () => {
  assert.equal(manifestDigest(), BOSS_DIGEST_BASELINE)
  assert.equal(manifestDigestFor('boss-recruiting'), BOSS_DIGEST_BASELINE)
})

test('weixin manifest：5 个工具、provider_id、仅 weixin_message_send 为写、v2 能力未开', () => {
  const weixin = getProviderManifest('weixin')!
  assert.deepEqual([...weixin.tools], [
    'weixin_probe',
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
  assert.ok(!isToolAllowedFor(weixin, 'weixin_message_send_v2'), 'v2 工具不得在 v1 白名单')
  assert.match(manifestDigestFor('weixin'), /^[0-9a-f]{64}$/)
  assert.notEqual(manifestDigestFor('weixin'), manifestDigestFor('boss-recruiting'), '各 Provider digest 应独立')
})

test('白名单：跨 Provider 工具不串道，未知 provider 无 manifest', () => {
  const boss = getProviderManifest('boss-recruiting')!
  const weixin = getProviderManifest('weixin')!
  assert.ok(!isToolAllowedFor(weixin, 'boss_greet'), 'weixin manifest 不得放行 boss 工具')
  assert.ok(!isToolAllowedFor(boss, 'weixin_message_send'), 'boss manifest 不得放行 weixin 工具')
  assert.ok(!isToolAllowed('weixin_message_send'), '兼容 isToolAllowed 仍按 boss 白名单判断')
  assert.equal(getProviderManifest('nope-unknown'), undefined)
  assert.equal(Object.keys(TRUSTED_MANIFESTS).length, 2)
})
