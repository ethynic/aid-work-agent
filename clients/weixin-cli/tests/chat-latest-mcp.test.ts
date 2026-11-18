import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { ProgressNotificationSchema } from '@modelcontextprotocol/sdk/types.js'

test('real MCP transport exposes only latest metadata on initialization, update, no change, failure, and progress', async () => {
  const stateDir = await mkdtemp(join(tmpdir(), 'weixin-latest-mcp-'))
  const registry = new URL('../src/operations/registry.js', import.meta.url).href
  const operation = new URL('../src/operations/chatLatest.js', import.meta.url).href
  const server = new URL('../src/mcp/server.js', import.meta.url).href
  const code = `
    import { OPERATIONS } from ${JSON.stringify(registry)};
    import { createWeixinChatLatestOperation } from ${JSON.stringify(operation)};
    import { startMcpServer } from ${JSON.stringify(server)};
    let scans = 0;
    OPERATIONS.weixin_chat_latest.operation = createWeixinChatLatestOperation({
      stateDir: ${JSON.stringify(stateDir)},
      dpapiRunFn: async (command, value) => command.includes('::Unprotect')
        ? Buffer.from(value, 'base64').toString('utf8') : Buffer.from(value).toString('base64'),
      runDriverFn: async (options) => {
        const request = JSON.parse(options.stdin);
        if (request.target_name === 'fail') throw Error('private-body private-name C:/private-path');
        const identity = { title: request.target_name, session_identity: '1:2:3', process_started_at: '123' };
        if (request.mode === 'identity') return identity;
        const row = (id, text) => ({ runtime_id: id, kind: 'text', text, sender: 'peer', clipped: false });
        scans++;
        return { ...identity, pages_scanned: 1, empty_proven: false,
          anchor_end: scans === 1 ? -1 : scans === 2 ? 0 : 1,
          rows: [row('a', 'private-body'), ...(scans > 1 ? [row('b', 'new-private-body')] : [])] };
      }
    });
    await startMcpServer();
  `
  let stderr = ''
  const transport = new StdioClientTransport({
    command: process.execPath, args: ['--input-type=module', '-e', code],
    env: { ...Object.fromEntries(Object.entries(process.env).filter((e): e is [string, string] => e[1] !== undefined)),
      AID_WEIXIN_MUTEX_SCOPE: 'latest-mcp-' + process.pid }, stderr: 'pipe',
  })
  transport.stderr?.on('data', (data) => { stderr += data })
  const client = new Client({ name: 'latest-test', version: '1' })
  const progress: unknown[] = []
  client.setNotificationHandler(ProgressNotificationSchema, async (event) => { progress.push(event) })
  try {
    await client.connect(transport)
    for (const [status, count] of [['initialized', 1], ['updated', 1], ['unchanged', 0]] as const) {
      const result = await client.callTool({
        name: 'weixin_chat_latest', arguments: { target_name: 'private-name', include_messages: true },
        _meta: { progressToken: 'latest' },
      })
      assert.deepEqual((result.structuredContent as Record<string, unknown>).data, { status, pages_scanned: 1, new_count: count })
      assert.doesNotMatch(JSON.stringify(result), /private-body|private-name|private-path|\.dpapi|messages/)
    }
    const failure = await client.callTool({ name: 'weixin_chat_latest', arguments: { target_name: 'fail' } })
    assert.equal((failure.structuredContent as Record<string, unknown>).success, false)
    assert.deepEqual((failure.structuredContent as Record<string, unknown>).data, {})
    assert.doesNotMatch(JSON.stringify(failure), /private-body|private-name|private-path/)
    assert.ok(progress.length > 0)
    assert.doesNotMatch(JSON.stringify(progress) + stderr, /private-body|private-name|private-path|\.dpapi/)
  } finally {
    await client.close()
    await rm(stateDir, { recursive: true, force: true })
  }
})
