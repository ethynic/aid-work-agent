import assert from 'node:assert/strict'
import test from 'node:test'
import { getProviderManifest, isToolAllowedFor, isWriteToolFor, manifestDigestOf } from '../dist/legacy/providers.js'

test('Host 新工具白名单与读写集合：旧 manifest 明确拒绝，摘要自然变化', () => {
  const current = getProviderManifest('boss-recruiting')
  for (const name of ['boss_lookup_candidates', 'boss_greet_selected']) assert.equal(isToolAllowedFor(current, name), true)
  assert.equal(isWriteToolFor(current, 'boss_lookup_candidates'), false)
  assert.equal(isWriteToolFor(current, 'boss_greet_selected'), true)
  const old = { ...current, tools: current.tools.filter(name => !['boss_lookup_candidates', 'boss_greet_selected'].includes(name)) }
  for (const name of ['boss_lookup_candidates', 'boss_greet_selected']) assert.equal(isToolAllowedFor(old, name), false)
  assert.notEqual(manifestDigestOf(old), manifestDigestOf(current))
  assert.equal(current.protocol_version, 1)
  assert.equal(current.shared_lock_capable, false)
})
