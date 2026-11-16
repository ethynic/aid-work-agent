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
    assert.deepEqual(version.tools.map(tool => tool.name), ['xiaohongshu_login', 'xiaohongshu_open'])
    const result = await client.callTool({ name: 'xiaohongshu_open', arguments: {
      profile_key: 'a'.repeat(64), url: 'https://example.invalid/',
    } })
    assert.equal(result.structuredContent.success, false)
    assert.equal(result.structuredContent.code, 'XHS_INVALID_INPUT')
  } finally {
    await client.close()
  }
})
