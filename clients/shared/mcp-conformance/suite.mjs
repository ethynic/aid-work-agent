/**
 * 第一方 CLI / MCP Provider 统一契约测试套件（标准 docs/system/first-party-cli-mcp-provider-standard.md §11）。
 *
 * 纯 ESM、零框架：用官方 @modelcontextprotocol/sdk client 对目标 Provider 进程跑契约检查，
 * 任何第一方 CLI 都可以参数化复用（见 README.md）。
 *
 * 覆盖：
 *  1. initialize / list_tools / call_tool（成功与失败）
 *  2. 结构化结果字段契约（success/code/message/effect/data/retryable/run_id）
 *  3. stdout 零污染（原始 spawn 逐行断言只有 JSON-RPC 帧）
 *  4. 并发 BUSY（单飞锁：第二个并发 call 立即返回 BUSY 结构化结果）
 *  5. cancel（取消后单飞锁释放，后续调用不再 BUSY）
 *  6. 关闭后无孤儿进程（stdin 关闭 → Provider 进程自行退出）
 *
 * SDK 解析：套件自身不依赖所在位置的 node_modules，调用方通过 requireBase
 * （通常是被测 CLI 包内的任一文件 URL）提供模块解析基点。
 */
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import net from 'node:net'

const EFFECT_VALUES = ['none', 'applied', 'partial', 'unknown']

/**
 * @param {object} options
 * @param {{command: string, args: string[], cwd?: string, env?: Record<string,string>}} options.spawn 被测 Provider 启动命令
 * @param {string} [options.requireBase] SDK 模块解析基点（文件 URL 或路径），缺省用套件自身位置
 * @param {string[]} [options.expectTools] 期望的 tool 名集合（精确匹配，顺序无关）
 * @param {{name: string, arguments: Record<string, unknown>}} options.callProbe 一次快速返回的调用（无 Chrome 时应返回结构化失败，不得挂起）
 * @param {{name: string, arguments: Record<string, unknown>}} options.invalidProbe 参数非法的调用（应 isError 或结构化 INVALID_ARGUMENT）
 * @param {{makeSpawn: (port: number) => {command: string, args: string[], cwd?: string}, tool: {name: string, arguments: Record<string, unknown>}, busyCode?: string}} [options.busyProbe]
 *   并发/取消检查：makeSpawn 返回把 CDP 端口指向悬挂端口的启动命令；缺省跳过并发/取消检查
 * @param {number} [options.timeoutMs] 单次检查超时（默认 15000）
 * @returns {Promise<{passed: number, failed: number, results: Array<{name: string, ok: boolean, detail: string}>}>}
 */
export async function runConformance(options) {
  const timeoutMs = options.timeoutMs ?? 15000
  const results = []
  const record = (name, ok, detail = '') => {
    results.push({ name, ok, detail })
    console.log(`${ok ? '✅' : '❌'} ${name}${detail ? ` — ${detail}` : ''}`)
  }
  /** 单项检查：内部 throw 记为失败而不是中断整个套件 */
  const runCheck = async (name, fn) => {
    try {
      const detail = await fn()
      record(name, true, detail ?? '')
    } catch (err) {
      record(name, false, err instanceof Error ? err.message : String(err))
    }
  }
  const assert = (cond, message) => {
    if (!cond) throw new Error(message)
  }

  const requireBase = options.requireBase ?? import.meta.url
  const req = createRequire(requireBase)
  const { Client } = await import(pathToFileURL(req.resolve('@modelcontextprotocol/sdk/client/index.js')).href)
  const { StdioClientTransport } = await import(pathToFileURL(req.resolve('@modelcontextprotocol/sdk/client/stdio.js')).href)

  /** 启动一个 SDK client 会话 */
  async function startClient(spawnSpec) {
    const transport = new StdioClientTransport({
      command: spawnSpec.command,
      args: spawnSpec.args,
      cwd: spawnSpec.cwd,
      // 未显式指定时全量继承：SDK Windows 默认只给 12 个白名单 env，测试注入的
      // 开关变量（如 AID_BOSS_AUTO_CHROME）会被丢掉导致测试隔离失效
      env: spawnSpec.env ?? { ...process.env },
      stderr: 'pipe',
    })
    const stderrChunks = []
    transport.stderr?.on('data', (d) => stderrChunks.push(d))
    const client = new Client({ name: 'mcp-conformance-suite', version: '0.1.0' }, { capabilities: {} })
    await client.connect(transport)
    return { client, transport, stderrChunks }
  }

  const extractStructured = (result) => {
    if (result.structuredContent && typeof result.structuredContent === 'object') return result.structuredContent
    const text = result.content?.find?.((c) => c.type === 'text')?.text
    if (typeof text === 'string') {
      try {
        return JSON.parse(text)
      } catch {
        return undefined
      }
    }
    return undefined
  }

  // ---------- 检查 1/2/3/4：单会话 initialize + list_tools + call_tool 成功与失败 ----------
  {
    let session
    await runCheck('initialize', async () => {
      session = await startClient(options.spawn)
      const info = session.client.getServerVersion()
      const instructions = session.client.getInstructions()
      assert(!!info?.name, 'serverInfo.name 缺失')
      assert(typeof instructions === 'string' && instructions.length > 0, 'instructions 缺失')
      return `server=${info.name}@${info.version} instructions=${instructions.length} 字符`
    })
    const client = session?.client
    if (client) {
      try {
        // 2. list_tools
        await runCheck('list_tools', async () => {
          const { tools } = await client.listTools()
          assert(Array.isArray(tools) && tools.length > 0, 'tools 为空')
          assert(tools.every((t) => t.name && t.inputSchema?.type === 'object'), '存在缺 name/inputSchema 的 tool')
          if (options.expectTools) {
            const names = tools.map((t) => t.name).sort()
            const expect = [...options.expectTools].sort()
            assert(
              JSON.stringify(names) === JSON.stringify(expect),
              `名称不匹配：实际 ${names.join(',')} 期望 ${expect.join(',')}`,
            )
            return `名称集合匹配（${names.length} 个）`
          }
          return `${tools.length} 个 tool`
        })

        // 3. call_tool 结构化结果契约（成功或失败都行，字段必须完整）
        await runCheck('call_tool 结构化结果契约', async () => {
          const r = await client.callTool({ name: options.callProbe.name, arguments: options.callProbe.arguments })
          const s = extractStructured(r)
          assert(!!s, `无结构化结果：${JSON.stringify(r).slice(0, 200)}`)
          assert(typeof s.success === 'boolean', 'success 缺失')
          assert(typeof s.code === 'string' && s.code.length > 0, 'code 缺失')
          assert(typeof s.message === 'string', 'message 缺失')
          assert(EFFECT_VALUES.includes(s.effect), `effect 非法：${String(s.effect)}`)
          assert(s.data !== null && typeof s.data === 'object', 'data 缺失')
          assert(typeof s.retryable === 'boolean', 'retryable 缺失')
          assert(typeof s.run_id === 'string' && s.run_id.length > 0, 'run_id 缺失')
          return `code=${s.code} effect=${s.effect}`
        })

        // 4. call_tool 参数非法失败路径（isError 或结构化 INVALID_ARGUMENT，不得 crash/挂起）
        await runCheck('call_tool 参数非法失败', async () => {
          const r = await client.callTool({ name: options.invalidProbe.name, arguments: options.invalidProbe.arguments })
          const s = extractStructured(r)
          if (r.isError === true) return 'isError=true（schema 校验拦截）'
          assert(!!s && s.success === false && typeof s.code === 'string', `非法参数未失败：${JSON.stringify(r).slice(0, 200)}`)
          return `结构化失败 code=${s.code}`
        })
      } finally {
        await client.close().catch(() => {})
      }
    }
  }

  // ---------- 检查 5/6：stdout 零污染 + stdin 关闭后无孤儿进程（原始 spawn，不过 SDK） ----------
  {
    const child = spawn(options.spawn.command, options.spawn.args, {
      cwd: options.spawn.cwd,
      env: options.spawn.env ? { ...process.env, ...options.spawn.env } : process.env,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdoutBuf = ''
    child.stdout.on('data', (d) => (stdoutBuf += d))
    let exited = null
    child.on('exit', (code) => (exited = code ?? 0))
    try {
      await runCheck('stdout 零污染', async () => {
        const send = (msg) => child.stdin.write(JSON.stringify(msg) + '\n')
        send({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'raw-probe', version: '0' } } })
        await waitFor(() => stdoutBuf.includes('"id":1'), timeoutMs, 'initialize 响应超时')
        send({ jsonrpc: '2.0', method: 'notifications/initialized' })
        send({ jsonrpc: '2.0', id: 2, method: 'tools/list' })
        await waitFor(() => stdoutBuf.includes('"id":2'), timeoutMs, 'tools/list 响应超时')

        const lines = stdoutBuf.split('\n').filter((l) => l.trim())
        const badLine = lines.find((l) => {
          try {
            return JSON.parse(l).jsonrpc !== '2.0'
          } catch {
            return true
          }
        })
        assert(badLine === undefined, `发现非协议行：${badLine?.slice(0, 120)}`)
        return `${lines.length} 行全部 JSON-RPC`
      })
    } finally {
      child.stdin.end()
    }
    // stdin 关闭后进程应自行退出（无孤儿）
    await runCheck('关闭后无孤儿进程', async () => {
      await waitFor(() => exited !== null, timeoutMs, 'stdin 关闭后进程未退出')
      return `stdin 关闭后进程退出（code=${exited}）`
    })
    if (exited === null) child.kill('SIGKILL')
  }

  // ---------- 检查 7/8：并发 BUSY + 取消后锁释放（需要 busyProbe） ----------
  if (options.busyProbe) {
    // 悬挂 TCP 服务器：接受连接但永不响应，让第一个 call 卡在 Chrome connect 阶段。
    // 被测进程被杀掉时连接被 reset，socket error 属预期，静默吞掉避免 uncaughtException
    const hangServer = net.createServer((sock) => sock.on('error', () => {}))
    await new Promise((resolve) => hangServer.listen(0, '127.0.0.1', resolve))
    const hangPort = hangServer.address().port
    const busySpawn = options.busyProbe.makeSpawn(hangPort)
    const busyCode = options.busyProbe.busyCode ?? 'BUSY'
    let session
    await runCheck('并发环境启动', async () => {
      session = await startClient(busySpawn)
      return 'ok'
    })
    const client = session?.client
    if (client) {
      try {
        const ac1 = new AbortController()
        const call1 = client.callTool(
          { name: options.busyProbe.tool.name, arguments: options.busyProbe.tool.arguments },
          undefined,
          { signal: ac1.signal },
        )
        call1.catch(() => {}) // 取消导致的 rejection 不处理
        // 等第一个 call 进入执行（卡在 connect）
        await sleep(800)

        // 7. 并发第二个 call：必须快速返回 BUSY 结构化结果（不是协议错误、不是挂起）
        await runCheck('并发单飞 BUSY', async () => {
          const started = Date.now()
          const r2 = await client.callTool({ name: options.busyProbe.tool.name, arguments: options.busyProbe.tool.arguments })
          const s2 = extractStructured(r2)
          const elapsed = Date.now() - started
          assert(!!s2 && s2.success === false && s2.code === busyCode, `未返回 ${busyCode}：${JSON.stringify(r2).slice(0, 200)}`)
          assert(elapsed < timeoutMs, `BUSY 返回耗时 ${elapsed}ms（疑似挂起）`)
          return `第二个并发 call ${elapsed}ms 内返回 ${busyCode}`
        })

        // 8. 取消第一个 call：单飞锁必须释放（第三个 call 不得再 BUSY）
        await runCheck('取消后单飞锁释放', async () => {
          ac1.abort()
          await sleep(500)
          const ac3 = new AbortController()
          const call3 = client.callTool(
            { name: options.busyProbe.tool.name, arguments: options.busyProbe.tool.arguments },
            undefined,
            { signal: ac3.signal },
          )
          // 锁已释放时 call3 会卡在悬挂 connect（不会快速返回）；若快速返回 BUSY 说明锁没释放
          const outcome = await Promise.race([
            call3.then((r) => ({ resolved: r })),
            sleep(3000).then(() => ({ timeout: true })),
          ])
          ac3.abort()
          call3.catch(() => {})
          const busyAgain = outcome.resolved && extractStructured(outcome.resolved)?.code === busyCode
          assert(!busyAgain, '取消后仍返回 BUSY：锁未释放')
          return outcome.timeout ? '取消后新 call 正常进入执行（未 BUSY）' : '取消后新 call 快速返回且非 BUSY'
        })
      } finally {
        await client.close().catch(() => {})
      }
    }
    await new Promise((resolve) => hangServer.close(resolve))
  }

  const passed = results.filter((r) => r.ok).length
  const failed = results.length - passed
  return { passed, failed, results }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

async function waitFor(cond, timeoutMs, timeoutMessage) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    if (cond()) return
    if (Date.now() > deadline) throw new Error(timeoutMessage || 'waitFor 超时')
    await sleep(50)
  }
}

// ---------- CLI 模式 ----------
// node suite.mjs [--require-base /path/to/pkg/package.json] [--expect-tools a,b,c] --call 'name:{"arg":1}' \
//   --invalid 'name:{...}' [--busy-port-flag --cdp-port] -- <command> [args...]
// --require-base：SDK（@modelcontextprotocol/sdk）的模块解析基点，指向装了 SDK 的包内任一文件；
// 缺省用套件自身位置（仅当 SDK 在套件向上的 node_modules 链上时可用）。
if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const argv = process.argv.slice(2)
  const sep = argv.indexOf('--')
  if (sep < 0) {
    console.error('用法: node suite.mjs [--require-base <file>] [--expect-tools a,b,c] --call \'name:{json}\' --invalid \'name:{json}\' [--busy-port-flag --cdp-port] -- <command> [args...]')
    process.exit(2)
  }
  const flags = argv.slice(0, sep)
  const [command, ...args] = argv.slice(sep + 1)
  const flagValue = (key) => {
    const i = flags.indexOf(key)
    return i >= 0 ? flags[i + 1] : undefined
  }
  const parseProbe = (raw) => {
    const i = raw.indexOf(':')
    return { name: raw.slice(0, i), arguments: JSON.parse(raw.slice(i + 1)) }
  }
  const busyPortFlag = flagValue('--busy-port-flag')
  const opts = {
    spawn: { command, args },
    requireBase: flagValue('--require-base'),
    expectTools: flagValue('--expect-tools')?.split(','),
    busyProbe: busyPortFlag
      ? {
          makeSpawn: (port) => ({ command, args: [...args, busyPortFlag, String(port)] }),
          tool: parseProbe(flagValue('--busy-tool') ?? flagValue('--call')),
        }
      : undefined,
  }
  if (flagValue('--call')) opts.callProbe = parseProbe(flagValue('--call'))
  if (flagValue('--invalid')) opts.invalidProbe = parseProbe(flagValue('--invalid'))
  if (!opts.callProbe || !opts.invalidProbe) {
    console.error('必须提供 --call 与 --invalid 探针')
    process.exit(2)
  }
  const { failed } = await runConformance(opts)
  process.exit(failed === 0 ? 0 : 1)
}
