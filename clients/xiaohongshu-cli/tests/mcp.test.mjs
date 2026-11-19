import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const entry = fileURLToPath(new URL('../dist/src/cli/index.js', import.meta.url))

test('签名清单和真实 MCP 工具一致，拒绝额外导航输入', async () => {
  const version = JSON.parse(execFileSync(process.execPath, [entry, 'version', '--json'], { encoding: 'utf8' }))
  const client = new Client({ name: 'xiaohongshu-contract-test', version: '1.0.0' })
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [entry, 'mcp', '--stdio'] }))
  try {
    assert.deepEqual((await client.listTools()).tools, version.tools)
    assert.deepEqual(version.tools.map(tool => tool.name), [
      'xiaohongshu_login', 'xiaohongshu_open', 'xiaohongshu_list_notes',
      'xiaohongshu_list_comments', 'xiaohongshu_open_conversation', 'xiaohongshu_send_first_message',
    ])
    assert.deepEqual(version.tool_declaration, {
      version: 1, tools: version.tools.map(tool => ({ name: tool.name })),
    })
    const result = await client.callTool({ name: 'xiaohongshu_open', arguments: {
      profile_key: 'a'.repeat(64), url: 'https://example.invalid/',
    } })
    assert.equal(result.structuredContent.success, false)
    assert.equal(result.structuredContent.code, 'XHS_INVALID_INPUT')
    for (const args of [
      { profile_url: 'https://example.com/user/profile/' + 'a'.repeat(24) },
      { profile_url: 'https://www.xiaohongshu.com/user/profile/' + 'a'.repeat(24), limit: 101 },
      { profile_url: 'https://www.xiaohongshu.com/user/profile/' + 'a'.repeat(24), limit: 0 },
      { profile_url: 'https://www.xiaohongshu.com/user/profile/' + 'a'.repeat(24), constructor: 'unexpected' },
    ]) {
      const rejected = await client.callTool({ name: 'xiaohongshu_list_notes',
        arguments: { profile_key: 'a'.repeat(64), ...args } })
      assert.equal(rejected.structuredContent.code, 'XHS_INVALID_INPUT')
    }
    for (const args of [
      { profile_url: 'https://www.xiaohongshu.com/user/profile/' + 'a'.repeat(24) },
      { profile_url: 'https://www.xiaohongshu.com/user/profile/' + 'a'.repeat(24),
        sender_id: 'c'.repeat(24), source_profile_url: 'https://example.invalid/',
        note_id: '1'.repeat(24), comment_id: 'c1', reason: 'fixture', message: 'fixture' },
    ]) {
      const rejected = await client.callTool({ name: 'xiaohongshu_send_first_message',
        arguments: { profile_key: 'a'.repeat(64), ...args } })
      assert.equal(rejected.structuredContent.code, 'XHS_INVALID_INPUT')
      assert.equal(rejected.structuredContent.retryable, false)
      assert.equal(rejected.structuredContent.effect, 'none')
    }
  } finally {
    await client.close()
  }
})
