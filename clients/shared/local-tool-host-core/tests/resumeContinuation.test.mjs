import assert from 'node:assert/strict'
import test from 'node:test'
import { ProviderManager } from '../dist/legacy/providerManager.js'
import { getProviderManifest } from '../dist/legacy/providers.js'

const schema = { type: 'object', required: ['exclude_names', 'expected_job_name'],
  properties: { exclude_names: { type: 'array' }, expected_job_name: { type: 'string' } } }

function stub(tools) {
  let lists = 0, calls = 0
  return { client: {
    listTools: async () => { lists++; return { tools } },
    callTool: async () => { calls++; return { structuredContent: {
      success: true, code: 'OK', effect: 'none', message: 'done', data: {}, retryable: false, run_id: 'test-run',
    } } },
    close: async () => {},
  }, counts: () => ({ lists, calls }) }
}

test('old/malformed Provider is rejected before any resume call', async () => {
  for (const tools of [[], [{ name: 'boss_resume_next', inputSchema: { type: 'object', properties: {} } }]]) {
    const manager = new ProviderManager('/never-spawn')
    const fake = stub(tools)
    manager.client = fake.client
    const result = await manager.callTool('boss_resume_next', {})
    assert.equal(result.code, 'TOOL_NOT_ALLOWED')
    assert.equal(result.effect, 'none')
    assert.deepEqual(fake.counts(), { lists: 1, calls: 0 })
  }
})

test('supported continuation capability cached per process and reset on respawn', async () => {
  const manager = new ProviderManager('/never-spawn')
  const fake = stub([{ name: 'boss_resume_next', inputSchema: schema }])
  manager.client = fake.client
  await manager.callTool('boss_resume_next', {})
  await manager.callTool('boss_resume_next', {})
  assert.deepEqual(fake.counts(), { lists: 1, calls: 2 })
  await manager.disposeDead()
  const old = stub([])
  manager.client = old.client
  assert.equal((await manager.callTool('boss_resume_next', {})).code, 'TOOL_NOT_ALLOWED')
  assert.deepEqual(old.counts(), { lists: 1, calls: 0 })
})

test('ordinary tools keep existing call path without a capability query', async () => {
  const manager = new ProviderManager('/never-spawn')
  const fake = stub([])
  manager.client = fake.client
  await manager.callTool('boss_resume_batch', {})
  assert.deepEqual(fake.counts(), { lists: 0, calls: 1 })
  assert.ok(getProviderManifest('boss-recruiting').tools.includes('boss_resume_next'))
})
