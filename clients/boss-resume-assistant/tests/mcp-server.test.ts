/**
 * MCP server 端到端（规格 m02 §9）：SDK client 经 stdio 直连 dist 产物。
 *
 * 覆盖：initialize / list_tools（7 个名称 + annotations）/ call_tool 成功与失败 /
 * 写动作 schema 硬上限 / 并发 BUSY / stdout 零污染。
 *
 * 无 Chrome 环境约定：用空闲端口作为 --cdp-port，boss_goto 快速返回 CHROME_UNAVAILABLE；
 * 并发 BUSY 用「悬挂 TCP 端口」（接受连接但永不响应）让第一个 call 卡在 connect 阶段。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import net from 'node:net'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'

const DIST_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const CLI = path.join(DIST_ROOT, 'src', 'cli', 'index.js')

const EXPECTED_TOOLS = [
  'boss_filter',
  'boss_clear_filter',
  'boss_goto',
  'boss_greet',
  'boss_accept_resume',
  'boss_reject_current',
  'boss_interview_demo',
  'boss_send_to',
  'boss_send_current',
  'boss_list_jobs',
  'boss_select_job',
  'boss_filter_options',
  'boss_resume_detail',
  'boss_resume_batch',
]

/** 找一个空闲端口（listen 0 后立即关闭） */
async function freePort(): Promise<number> {
  const srv = net.createServer()
  await new Promise<void>((r) => srv.listen(0, '127.0.0.1', r))
  const port = (srv.address() as net.AddressInfo).port
  await new Promise<void>((r) => srv.close(() => r()))
  return port
}

async function startClient(args: string[]): Promise<Client> {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args,
    stderr: 'pipe',
    // SDK Windows 下默认只继承 12 个白名单 env（防注入），AID_BOSS_AUTO_CHROME 测试开关
    // 不在其中——不显式透传则 MCP 子进程会真拉起 Chrome，破坏测试隔离
    env: Object.fromEntries(Object.entries(process.env).filter((e): e is [string, string] => e[1] !== undefined)),
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

test('initialize：serverInfo + instructions（含关键前提与写动作上限）', async () => {
  const client = await startClient([CLI, 'mcp', '--stdio', '--cdp-port', String(await freePort())])
  try {
    const info = client.getServerVersion()
    assert.equal(info?.name, 'boss-recruiting')
    const instructions = client.getInstructions() ?? ''
    assert.ok(instructions.includes('仅 Windows'))
    assert.ok(instructions.includes('boss_greet'))
    assert.ok(instructions.length <= 512, `instructions 应在前 512 字符内含关键信息（当前 ${instructions.length}）`)
  } finally {
    await client.close()
  }
})

test('list_tools：14 个 tool，名称与 annotations 正确', async () => {
  const client = await startClient([CLI, 'mcp', '--stdio', '--cdp-port', String(await freePort())])
  try {
    const { tools } = await client.listTools()
    assert.deepEqual(tools.map((t) => t.name).sort(), [...EXPECTED_TOOLS].sort())
    const byName = new Map(tools.map((t) => [t.name, t]))
    // annotations 语义抽查（规格 m02 §6）
    assert.equal(byName.get('boss_goto')!.annotations?.readOnlyHint, true)
    assert.equal(byName.get('boss_interview_demo')!.annotations?.readOnlyHint, true)
    assert.equal(byName.get('boss_greet')!.annotations?.readOnlyHint, false)
    assert.equal(byName.get('boss_greet')!.annotations?.openWorldHint, true)
    assert.equal(byName.get('boss_greet')!.annotations?.idempotentHint, false)
    assert.equal(byName.get('boss_greet')!.annotations?.destructiveHint, false)
    assert.equal(byName.get('boss_filter')!.annotations?.idempotentHint, true)
    assert.equal(byName.get('boss_clear_filter')!.annotations?.idempotentHint, true)
    // 标题全部中文
    for (const t of tools) {
      assert.match(t.title ?? '', /[一-龥]/, `${t.name} 缺中文标题`)
    }
    // inputSchema 是 object 且写动作有硬上限
    const greetProps = (byName.get('boss_greet')!.inputSchema as { properties: Record<string, { maximum?: number }> }).properties
    assert.equal(greetProps.limit!.maximum, 3)
  } finally {
    await client.close()
  }
})

test('call_tool：无 Chrome 时快速返回结构化 CHROME_UNAVAILABLE（字段完整）', async () => {
  const client = await startClient([CLI, 'mcp', '--stdio', '--cdp-port', String(await freePort())])
  try {
    const r = await client.callTool({ name: 'boss_goto', arguments: { target: 'chat' } })
    const s = structuredOf(r)
    assert.equal(s.success, false)
    assert.equal(s.code, 'CHROME_UNAVAILABLE')
    assert.equal(s.effect, 'none')
    assert.equal(s.retryable, true)
    assert.equal(typeof s.message, 'string')
    assert.equal(typeof s.run_id, 'string')
    assert.ok((s.run_id as string).length > 0)
    assert.ok(s.data !== null && typeof s.data === 'object')
  } finally {
    await client.close()
  }
})

test('call_tool：参数非法被 schema 拦截（isError），不触达 Chrome', async () => {
  const client = await startClient([CLI, 'mcp', '--stdio', '--cdp-port', String(await freePort())])
  try {
    const r1 = await client.callTool({ name: 'boss_goto', arguments: { target: 'nope' } })
    assert.equal(r1.isError, true)
    // 写动作 schema 硬上限：greet limit=5 超过 maximum 3
    const r2 = await client.callTool({ name: 'boss_greet', arguments: { limit: 5 } })
    assert.equal(r2.isError, true)
  } finally {
    await client.close()
  }
})

test('并发单飞：第一个 call 卡住时第二个立即返回 BUSY 结构化结果', async () => {
  // 悬挂 TCP 服务器：接受连接但永不响应（子进程被杀时连接 reset 属预期，吞掉 socket error）
  const hang = net.createServer((sock) => sock.on('error', () => {}))
  await new Promise<void>((r) => hang.listen(0, '127.0.0.1', r))
  const hangPort = (hang.address() as net.AddressInfo).port
  const client = await startClient([CLI, 'mcp', '--stdio', '--cdp-port', String(hangPort)])
  try {
    const ac = new AbortController()
    const call1 = client.callTool({ name: 'boss_goto', arguments: { target: 'chat' } }, undefined, { signal: ac.signal })
    call1.catch(() => {})
    await new Promise((r) => setTimeout(r, 800))

    const started = Date.now()
    const r2 = await client.callTool({ name: 'boss_goto', arguments: { target: 'chat' } })
    const elapsed = Date.now() - started
    const s2 = structuredOf(r2)
    assert.equal(s2.success, false)
    assert.equal(s2.code, 'BUSY')
    assert.equal(s2.effect, 'none')
    assert.equal(s2.retryable, true)
    assert.ok(elapsed < 10000, `BUSY 应立即返回（耗时 ${elapsed}ms）`)

    // 取消第一个 call 后锁释放：第三个 call 不再 BUSY（会卡住 → 超时判胜）
    ac.abort()
    await new Promise((r) => setTimeout(r, 500))
    const ac3 = new AbortController()
    const call3 = client.callTool({ name: 'boss_goto', arguments: { target: 'chat' } }, undefined, { signal: ac3.signal })
    call3.catch(() => {})
    const outcome = await Promise.race<{ resolved?: unknown; timeout?: boolean }>([
      call3.then((r) => ({ resolved: r as unknown })),
      new Promise<{ timeout: boolean }>((r) => setTimeout(() => r({ timeout: true }), 3000)),
    ])
    ac3.abort()
    assert.ok('timeout' in outcome, `取消后第三个 call 仍快速返回（疑似 BUSY）：${JSON.stringify(outcome).slice(0, 200)}`)
  } finally {
    await client.close().catch(() => {})
    await new Promise<void>((r) => hang.close(() => r()))
  }
})

test('stdout 零污染：原始 spawn 下 stdout 只出现 JSON-RPC 帧，stdin 关闭后进程退出', async () => {
  const port = await freePort()
  const child = spawn(process.execPath, [CLI, 'mcp', '--stdio', '--cdp-port', String(port)], {
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
      JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'boss_goto', arguments: { target: 'chat' } } }) + '\n',
    )
    await waitFor(() => stdoutBuf.includes('"id":2'), 10000)

    const lines = stdoutBuf.split('\n').filter((l) => l.trim())
    for (const line of lines) {
      const msg = JSON.parse(line) as { jsonrpc?: string }
      assert.equal(msg.jsonrpc, '2.0', `stdout 出现非 JSON-RPC 行：${line.slice(0, 120)}`)
    }
    // call_tool 结果也是协议帧内的结构化结果
    const callLine = lines.find((l) => l.includes('"id":2'))!
    const callMsg = JSON.parse(callLine)
    const s = callMsg.result.structuredContent
    assert.equal(s.code, 'CHROME_UNAVAILABLE')
  } finally {
    child.stdin.end()
  }
  await waitFor(() => exited !== null, 10000)
})
