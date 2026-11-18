import { createHash, randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { BrowserFailure, chromeExecutable, PROFILE_KEY, XiaohongshuBrowser } from '../browser.js'

const packageRoot = new URL('../../../', import.meta.url)
const { version } = JSON.parse(readFileSync(new URL('package.json', packageRoot), 'utf8'))
const providerId = 'ai.aidwork.xiaohongshu'
const tools = [
  { name: 'xiaohongshu_login', description: '在专用本机 Chrome 中显示小红书官方登录二维码' },
  { name: 'xiaohongshu_open', description: '使用专用本机 Chrome 会话打开小红书首页' },
].map(tool => ({
  ...tool,
  inputSchema: {
    type: 'object' as const, required: ['profile_key'], additionalProperties: false,
    properties: { profile_key: { type: 'string', pattern: PROFILE_KEY.source } },
  },
  annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: true },
}))

function manifest() {
  return {
    provider_id: providerId, provider_version: version, protocol: 'mcp', transport: 'stdio',
    platforms: ['win32-x64'], execution_target: 'local_required', min_mcp_protocol_version: '2024-11-05',
    entrypoint: ['dist/src/cli/index.js', 'mcp', '--stdio'], tools,
    tool_declaration: { version: 1, tools: tools.map(tool => ({ name: tool.name })) },
    schema_digest: 'sha256:' + createHash('sha256').update(JSON.stringify(tools)).digest('hex'),
  }
}

async function serve() {
  const browser = new XiaohongshuBrowser()
  const server = new Server({ name: providerId, version }, {
    capabilities: { tools: {} },
    instructions: '小红书本机连接器。调用会打开所选 Windows 设备上的专用 Chrome。用户在官方网页扫码，Cookie 留在本机。',
  })
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }))
  server.setRequestHandler(CallToolRequestSchema, async ({ params }, extra) => {
    const common = { effect: 'none' as const, run_id: randomUUID() }
    let result
    try {
      const args = params.arguments
      if (!args || Object.keys(args).length !== 1 || typeof args.profile_key !== 'string' || !PROFILE_KEY.test(args.profile_key)) {
        throw new BrowserFailure('XHS_INVALID_INPUT', '需要有效的连接资料')
      }
      if (!tools.some(tool => tool.name === params.name)) {
        throw new BrowserFailure('XHS_INVALID_INPUT', '未知的小红书操作')
      }
      const action = params.name === 'xiaohongshu_login' ? 'login' : 'open'
      const status = await browser.run(action, args.profile_key, extra.signal)
      result = { ...common, success: true, code: 'OK', retryable: false,
        message: status === 'qr_ready' ? '请在 Chrome 窗口扫码登录' : '已用登录会话打开小红书',
        data: { schema_version: 1, status } }
    } catch (error) {
      const failure = error instanceof BrowserFailure
        ? error : new BrowserFailure('XHS_BROWSER_UNAVAILABLE', '小红书连接器暂时无法执行')
      result = { ...common, success: false, code: failure.code, message: failure.message,
        retryable: failure.code !== 'XHS_INVALID_INPUT' }
    }
    return { content: [{ type: 'text', text: JSON.stringify(result) }], structuredContent: result }
  })
  const shutdown = async () => { await browser.close(); process.exit() }
  process.stdin.once('end', shutdown)
  process.once('SIGTERM', shutdown)
  await server.connect(new StdioServerTransport())
}

const args = process.argv.slice(2).join(' ')
if (args === 'version --json') {
  console.log(JSON.stringify(manifest()))
} else if (args === 'doctor' || args === 'doctor --json') {
  const ready = Boolean(chromeExecutable())
  console.log(JSON.stringify({ success: ready, runtime_readiness: { ready,
    ...(ready ? {} : { reason: '请在所选 Windows 设备安装 Google Chrome' }) } }))
  process.exitCode = ready ? 0 : 1
} else if (args === 'mcp --stdio') {
  serve().catch(() => { console.error('小红书连接器无法启动'); process.exitCode = 1 })
} else {
  console.error('用法：version --json | doctor [--json] | mcp --stdio')
  process.exitCode = 2
}
