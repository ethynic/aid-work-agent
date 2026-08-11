/**
 * MCP server 端到端：SDK client 经 stdio 直连 dist 产物。
 *
 * 覆盖：initialize（instructions 关键前提）/ list_tools / call_tool 结构化契约 /
 * 非法参数 schema 拦截 / 并发 BUSY / 取消后锁释放 / stdout 零污染 / 关闭无孤儿。
 *
 * 无微信环境约定：SESSIONNAME=Console 注入后 probe 快速返回 OK（微信未运行体现在
 * data.weixin_running，不是失败）；并发/取消用测试 hook AID_WEIXIN_TEST_HANG=1 让
 * probe 悬挂。每个 spawn 用独立 AID_WEIXIN_MUTEX_SCOPE，避免与并行测试文件的
 * 默认 scope（session）互相干扰。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

let scopeCounter = 0
/** 干净 env（去掉 undefined 值）+ 测试注入；互斥 scope 每个 server 唯一 */
function testEnv(extra: Record<string, string> = {}): Record<string, string> {
  const base = Object.fromEntries(Object.entries(process.env).filter((e): e is [string, string] => e[1] !== undefined))
  return { ...base, SESSIONNAME: 'Console', AID_WEIXIN_MUTEX_SCOPE: `test-mcp-${process.pid}-${scopeCounter++}`, ...extra }
}

async function startClient(extraEnv: Record<string, string> = {}): Promise<Client> {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [CLI, 'mcp', '--stdio'],
    env: testEnv(extraEnv),
    stderr: 'pipe',
  })
  transport.stderr?.on('data', () => {}) // 丢弃 server stderr 日志
  const client = new Client({ name: 'mcp-server-test', version: '0.1.0' }, { capabilities: {} })
  await client.connect(transport)
  return client
}

function structuredOf(result: unknown): Record<string, unknown> {
  const sc = (result as { structuredContent?: Record<string, unknown> }).structuredContent
  if (sc) return sc
  const text = (result as { content?: Array<{ type: string; text?: string }> }).content?.find((c) => c.type === 'text')?.text
  assert.ok(text, 'call_tool 结果既没有 structuredContent 也没有 text content')
  return JSON.parse(text!) as Record<string, unknown>
}

function assertResultContract(s: Record<string, unknown>): void {
  assert.equal(typeof s.success, 'boolean')
  assert.equal(typeof s.code, 'string')
  assert.equal(typeof s.message, 'string')
  assert.ok(['none', 'applied', 'partial', 'unknown'].includes(s.effect as string), `effect 非法：${String(s.effect)}`)
  assert.ok(s.data !== null && typeof s.data === 'object')
  assert.equal(typeof s.retryable, 'boolean')
  assert.equal(typeof s.run_id, 'string')
  assert.ok((s.run_id as string).length > 0)
}

test('initialize：serverInfo + instructions（前 512 字符内含仅 Windows/未锁屏/前台剪贴板/写动作上限/勿动鼠标）', async () => {
  const client = await startClient()
  try {
    const info = client.getServerVersion()
    assert.equal(info?.name, 'aid-weixin')
    const instructions = client.getInstructions() ?? ''
    for (const phrase of ['仅 Windows', '未锁屏', '前台', '剪贴板', '硬上限', '移动鼠标']) {
      assert.ok(instructions.includes(phrase), `instructions 缺少「${phrase}」`)
    }
    assert.ok(instructions.length <= 512, `instructions 应在前 512 字符内含关键信息（当前 ${instructions.length}）`)
  } finally {
    await client.close()
  }
})

test('list_tools：weixin_probe，readOnly 注解 + 中文标题 + object inputSchema', async () => {
  const client = await startClient()
  try {
    const { tools } = await client.listTools()
    assert.deepEqual(tools.map((t) => t.name), ['weixin_probe'])
    const probe = tools[0]!
    assert.equal(probe.annotations?.readOnlyHint, true)
    assert.equal(probe.annotations?.destructiveHint, false)
    assert.match(probe.title ?? '', /[一-龥]/)
    assert.equal((probe.inputSchema as { type: string }).type, 'object')
  } finally {
    await client.close()
  }
})

test('call_tool：快速返回结构化结果，字段契约完整', async () => {
  const client = await startClient()
  try {
    const r = await client.callTool({ name: 'weixin_probe', arguments: {} })
    const s = structuredOf(r)
    assertResultContract(s)
    assert.equal(s.effect, 'none')
    if (process.platform === 'win32') {
      // 注入了 SESSIONNAME=Console：应 OK，且微信未运行体现在 data 而非失败
      assert.equal(s.code, 'OK')
      assert.equal(s.success, true)
      assert.equal((s.data as Record<string, unknown>).weixin_running !== undefined, true)
    }
  } finally {
    await client.close()
  }
})

test('call_tool：参数非法被 schema 拦截（isError），不执行业务逻辑', async () => {
  const client = await startClient()
  try {
    const r = await client.callTool({ name: 'weixin_probe', arguments: { verbose: 'yes' } })
    assert.equal(r.isError, true)
  } finally {
    await client.close()
  }
})

test('并发单飞：悬挂中第二个 call 立即 BUSY；取消第一个后锁释放', { timeout: 30000 }, async () => {
  const client = await startClient({ AID_WEIXIN_TEST_HANG: '1' })
  try {
    const ac1 = new AbortController()
    const call1 = client.callTool({ name: 'weixin_probe', arguments: {} }, undefined, { signal: ac1.signal })
    call1.catch(() => {})
    await new Promise((r) => setTimeout(r, 800))

    const started = Date.now()
    const r2 = await client.callTool({ name: 'weixin_probe', arguments: {} })
    const elapsed = Date.now() - started
    const s2 = structuredOf(r2)
    assertResultContract(s2)
    assert.equal(s2.success, false)
    assert.equal(s2.code, 'BUSY')
    assert.equal(s2.effect, 'none')
    assert.ok(elapsed < 10000, `BUSY 应立即返回（耗时 ${elapsed}ms）`)

    // 取消第一个 call 后锁释放：第三个 call 不再 BUSY（会再次悬挂 → 超时判胜）
    ac1.abort()
    await new Promise((r) => setTimeout(r, 500))
    const ac3 = new AbortController()
    const call3 = client.callTool({ name: 'weixin_probe', arguments: {} }, undefined, { signal: ac3.signal })
    call3.catch(() => {})
    const outcome = await Promise.race<{ resolved?: unknown; timeout?: boolean }>([
      call3.then((r) => ({ resolved: r as unknown })),
      new Promise<{ timeout: boolean }>((r) => setTimeout(() => r({ timeout: true }), 3000)),
    ])
    ac3.abort()
    assert.ok('timeout' in outcome, `取消后第三个 call 仍快速返回（疑似 BUSY）：${JSON.stringify(outcome).slice(0, 200)}`)
  } finally {
    await client.close().catch(() => {})
  }
})

test('stdout 零污染：原始 spawn 下 stdout 只出现 JSON-RPC 帧，stdin 关闭后进程退出', { timeout: 30000 }, async () => {
  const child = spawn(process.execPath, [CLI, 'mcp', '--stdio'], {
    env: testEnv(),
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  let stdoutBuf = ''
  child.stdout.on('data', (d) => (stdoutBuf += d))
  let exited: number | null = null
  child.on('exit', (code) => (exited = code ?? 0))
  const waitFor = async (cond: () => boolean, ms: number) => {
    const deadline = Date.now() + ms
    while (!cond()) {
      if (Date.now() > deadline) throw new Error('waitFor 超时')
      await new Promise((r) => setTimeout(r, 50))
    }
  }
  try {
    child.stdin.write(
      JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'initialize',
        params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'raw', version: '0' } },
      }) + '\n',
    )
    await waitFor(() => stdoutBuf.includes('"id":1'), 10000)
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n')
    child.stdin.write(
      JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'weixin_probe', arguments: {} } }) + '\n',
    )
    await waitFor(() => stdoutBuf.includes('"id":2'), 10000)

    const lines = stdoutBuf.split('\n').filter((l) => l.trim())
    for (const line of lines) {
      const msg = JSON.parse(line) as { jsonrpc?: string }
      assert.equal(msg.jsonrpc, '2.0', `stdout 出现非 JSON-RPC 行：${line.slice(0, 120)}`)
    }
    const callMsg = JSON.parse(lines.find((l) => l.includes('"id":2'))!)
    assertResultContract(callMsg.result.structuredContent)
  } finally {
    child.stdin.end()
  }
  await waitFor(() => exited !== null, 10000)
})
