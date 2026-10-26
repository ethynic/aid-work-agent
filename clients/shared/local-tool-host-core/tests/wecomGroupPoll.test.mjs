import assert from 'node:assert/strict'
import test from 'node:test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ProviderManager, ProviderSet } from '../dist/legacy/providerManager.js'
import { getProviderManifest, isWriteToolFor } from '../dist/legacy/providers.js'
import { runInvocation } from '../dist/legacy/invocationRunner.js'
import { configureRuntimeHome } from '../dist/legacy/config.js'
import { groupMonitorFixture, changeGroupFixture } from '../../../wecom-cli/dist/tests/helpers/groupMonitorFixture.js'

const poll = { name: 'wecom_group_poll', inputSchema: { type: 'object', required: ['target_name'],
  properties: { target_name: { type: 'string' }, cursor: { type: 'string' } } }, annotations: { readOnlyHint: true } }
const send = { name: 'wecom_message_send', inputSchema: { type: 'object', required: ['text'],
  properties: { target_name: { type: 'string' }, group_cursor: { type: 'string' }, text: { type: 'string' } } }, annotations: { readOnlyHint: false } }

test('new poll and scoped send fail before RPC on an older or incompatible CLI; legacy sends retain their contract', async () => {
  for (const tool of [poll, send]) {
    for (const defect of ['missing', 'extra-required', 'type', 'annotation']) {
      const candidate = structuredClone(tool)
      if (defect === 'extra-required') candidate.inputSchema.required.push('unexpected')
      if (defect === 'type') candidate.inputSchema.properties[tool === poll ? 'cursor' : 'group_cursor'].type = 'number'
      if (defect === 'annotation') candidate.annotations.readOnlyHint = !candidate.annotations.readOnlyHint
      let calls = 0
      const manager = new ProviderManager('/never-spawn')
      manager.client = { listTools: async () => ({ tools: defect === 'missing' ? [] : [candidate] }),
        callTool: async () => { calls++; return { structuredContent: { success: true, effect: 'applied', code: 'OK', message: 'synthetic', run_id: 'fixture', retryable: false, data: {} } } }, close: async () => {} }
      try {
        const result = await manager.callTool(tool.name, { target_name: '合成群甲', text: 'synthetic', ...(tool === send ? { group_cursor: 'opaque' } : {}) })
        assert.equal(result.code, 'TOOL_NOT_ALLOWED', defect)
        assert.equal(result.effect, 'none')
        assert.equal(calls, 0)
        await manager.callTool('wecom_message_send', { text: 'legacy' })
        assert.equal(calls, 1)
      } finally { await manager.shutdown() }
    }
  }
})

test('capability cache expires with Provider generation and the poll stays read-only in the trusted catalog', async () => {
  let lists = 0
  const manager = new ProviderManager('/never-spawn')
  const client = tools => ({ listTools: async () => { lists++; return { tools } },
    callTool: async () => ({ structuredContent: { success: true, effect: 'none', code: 'OK', message: 'synthetic', run_id: 'fixture', retryable: false, data: {} } }), close: async () => {} })
  try {
    manager.client = client([poll, send])
    for (let i = 0; i < 2; i++) await manager.callTool('wecom_group_poll', { target_name: '合成群甲' })
    assert.equal(lists, 1)
    await manager.disposeDead()
    manager.client = client([])
    assert.equal((await manager.callTool('wecom_group_poll', { target_name: '合成群甲' })).code, 'TOOL_NOT_ALLOWED')
    assert.equal(lists, 2)
    const manifest = getProviderManifest('wecom')
    assert.ok(manifest.tools.includes('wecom_group_poll'))
    assert.equal(isWriteToolFor(manifest, 'wecom_group_poll'), false)
    assert.equal(manifest.protocol_version, 1)
  } finally { await manager.shutdown() }
})

test('production Runtime -> real MCP -> native synthetic DB cursors and exact scoped reply survives a fresh process', { skip: process.platform !== 'win32' }, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-runtime-poll-'))
  try {
    await groupMonitorFixture(base)
    const bridge = fileURLToPath(new URL('./fixtures/groupMonitorBridge.mjs', import.meta.url))
    const call = (tool_name, args) => JSON.parse(execFileSync(process.execPath, [bridge], {
      input: JSON.stringify({ tool_name, arguments: args }), encoding: 'utf8', windowsHide: true,
      env: { ...process.env, WECOM_TEST_HOME: base }, stdio: ['pipe', 'pipe', 'pipe'],
    }).trim())
    const first = call('wecom_group_poll', { target_name: '合成群甲' })
    assert.equal(first.success, true, first.message)
    assert.deepEqual(first.data.new_messages, [])
    changeGroupFixture(base, 'append', { text: '新问题 ORCHID', mention_self: true })
    const next = call('wecom_group_poll', { target_name: '合成群甲', cursor: first.data.cursor })
    assert.equal(next.data.new_messages.length, 1)
    const text = '配置模型输出：ORCHID\n下一步已收到😀'
    const sent = call('wecom_message_send', { target_name: '合成群甲', group_cursor: next.data.cursor, text })
    assert.equal(sent.success, true, sent.message)
    assert.equal(sent.effect, 'applied')
    assert.equal(sent.data.verification, 'local-record')
    assert.deepEqual(readFileSync(join(base, 'sent.jsonl'), 'utf8').trim().split('\n').map(JSON.parse), [{ target_name: '合成群甲', text }])
    const fresh = call('wecom_group_poll', { target_name: '合成群甲', cursor: next.data.cursor })
    assert.equal(fresh.data.new_messages.length, 1, 'fresh process sees the confirmed outgoing record without shared watermark state')
    assert.equal(fresh.data.new_messages[0].side, 'self')
  } finally { rmSync(base, { recursive: true, force: true }) }
})
