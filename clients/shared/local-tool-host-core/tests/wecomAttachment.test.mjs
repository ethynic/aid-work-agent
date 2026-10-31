import assert from 'node:assert/strict'
import test from 'node:test'
import { ProviderManager } from '../dist/legacy/providerManager.js'

const receipt = data => ({ success: true, code: 'OK', effect: 'none', message: 'read only',
  run_id: 'anonymous-attachment', retryable: false, data })
function fixture(attachments = false) {
  const calls = []
  let lists = 0
  const properties = { session: { type: 'string' } }
  if (attachments) properties.attachment = { type: 'object', properties: { message_id: { type: 'integer' } } }
  if (attachments === 'nullable') properties.attachment = { anyOf: [properties.attachment, { type: 'null' }] }
  const manager = new ProviderManager('/never-spawn')
  manager.client = {
    listTools: async () => { lists++; return { tools: [{ name: 'wecom_read_session', inputSchema: { type: 'object', properties } }] } },
    callTool: async request => { calls.push(request); return { structuredContent: receipt({ attachment: request.arguments.attachment }) } },
    close: async () => {},
  }
  return { manager, calls, lists: () => lists }
}

test('old WeCom keeps ordinary reads and BOSS calls; only requested attachment is unavailable', async () => {
  const { manager, calls, lists } = fixture()
  try {
    assert.equal((await manager.callTool('wecom_read_session', { session: 'anonymous' })).success, true)
    const result = await manager.callTool('wecom_read_session', { session: 'anonymous', attachment: { message_id: 7 } })
    assert.equal(result.code, 'TOOL_NOT_SUPPORTED')
    assert.equal(result.effect, 'none')
    assert.equal((await manager.callTool('wecom_read_session', { session: 'anonymous', attachment: null })).success, true)
    assert.equal((await manager.callTool('boss_resume_batch', { limit: 1 })).success, true)
    assert.deepEqual(calls.map(call => call.name), ['wecom_read_session', 'wecom_read_session', 'boss_resume_batch'])
    assert.equal(lists(), 1)
  } finally { await manager.shutdown() }
})

test('new optional attachment request is passed unchanged through original Provider result', async () => {
  for (const schema of [true, 'nullable']) {
    const { manager, calls, lists } = fixture(schema)
    const request = { session: 'anonymous', attachment: { message_id: 7, offset: 4096, max_bytes: 4096 } }
    try {
      const result = await manager.callTool('wecom_read_session', request)
      assert.deepEqual(calls[0].arguments, request)
      assert.deepEqual(result.data.attachment, request.attachment)
      await manager.callTool('wecom_read_session', { session: 'anonymous' })
      assert.equal(lists(), 1)
    } finally { await manager.shutdown() }
  }
})

test('Provider restart discovers the current optional capability without changing ordinary readiness', async () => {
  const f = fixture(true)
  await f.manager.callTool('wecom_read_session', { session: 'anonymous', attachment: { message_id: 7 } })
  await f.manager.disposeDead()
  const old = fixture()
  f.manager.client = old.manager.client
  try {
    assert.equal((await f.manager.callTool('wecom_read_session', { session: 'anonymous', attachment: { message_id: 7 } })).code, 'TOOL_NOT_SUPPORTED')
    assert.equal(old.calls.length, 0)
    assert.equal((await f.manager.callTool('wecom_read_session', { session: 'anonymous' })).success, true)
  } finally { await f.manager.shutdown() }
})
