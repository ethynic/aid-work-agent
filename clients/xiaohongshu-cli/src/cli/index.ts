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

const profileUrlSchema = {
  type: 'string', maxLength: 2048,
  pattern: '^https://www\\.xiaohongshu\\.com/user/profile/[0-9a-fA-F]{24}(?:\\?[^#]*)?$',
}
const browsingTools = [
  { name: 'xiaohongshu_list_notes', description: '读取指定小红书主页的笔记卡片，返回有界结果与完整性状态', limit: true },
  { name: 'xiaohongshu_list_comments', description: '从来源主页打开指定笔记，读取文本评论和评论者主页', limit: true },
  { name: 'xiaohongshu_open_conversation', description: '打开指定小红书用户的原生私信会话，核对稳定目标 ID，不发送消息', limit: false },
].map(tool => ({
  name: tool.name, description: tool.description,
  inputSchema: {
    type: 'object' as const, additionalProperties: false,
    required: ['profile_key', 'profile_url', ...(tool.name === 'xiaohongshu_list_comments' ? ['note_id'] : [])],
    properties: {
      profile_key: { type: 'string', pattern: PROFILE_KEY.source },
      profile_url: profileUrlSchema,
      ...(tool.limit ? { limit: { type: 'integer', minimum: 1, maximum: 100, default: 20 } } : {}),
      ...(tool.name === 'xiaohongshu_list_comments' ? { note_id: { type: 'string', pattern: '^[0-9a-fA-F]{24}$' } } : {}),
    },
  },
  annotations: { readOnlyHint: tool.name !== 'xiaohongshu_open_conversation',
    destructiveHint: false, openWorldHint: true },
}))
const allTools = [...tools, ...browsingTools]

function manifest() {
  return {
    provider_id: providerId, provider_version: version, protocol: 'mcp', transport: 'stdio',
    platforms: ['win32-x64'], execution_target: 'local_required', min_mcp_protocol_version: '2024-11-05',
    entrypoint: ['dist/src/cli/index.js', 'mcp', '--stdio'], tools: allTools,
    tool_declaration: { version: 1, tools: allTools.map(tool => ({ name: tool.name })) },
    schema_digest: 'sha256:' + createHash('sha256').update(JSON.stringify(allTools)).digest('hex'),
  }
}

async function serve() {
  const browser = new XiaohongshuBrowser()
  const server = new Server({ name: providerId, version }, {
    capabilities: { tools: {} },
    instructions: '小红书本机连接器。调用会打开所选 Windows 设备上的专用 Chrome。用户在官方网页扫码，Cookie 留在本机。',
  })
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: allTools }))
  server.setRequestHandler(CallToolRequestSchema, async ({ params }, extra) => {
    const common = { effect: 'none' as const, run_id: randomUUID() }
    let result
    try {
      const args = params.arguments
      const definition = allTools.find(tool => tool.name === params.name)
      if (!definition || !args || typeof args.profile_key !== 'string' || !PROFILE_KEY.test(args.profile_key)
        || Object.keys(args).some(key => !Object.hasOwn(definition.inputSchema.properties, key))) {
        throw new BrowserFailure('XHS_INVALID_INPUT', '需要有效的连接资料')
      }
      if (tools.some(tool => tool.name === params.name)) {
        const action = params.name === 'xiaohongshu_login' ? 'login' : 'open'
        const status = await browser.run(action, args.profile_key, extra.signal)
        result = { ...common, success: true, code: 'OK', retryable: false,
          message: status === 'qr_ready' ? '请在 Chrome 窗口扫码登录' : '已用登录会话打开小红书',
          data: { schema_version: 1, status } }
      } else {
        if (typeof args.profile_url !== 'string' || args.profile_url.length > 2048
          || (args.limit !== undefined && typeof args.limit !== 'number')) {
          throw new BrowserFailure('XHS_INVALID_INPUT', '需要有效的小红书主页及数量上限')
        }
        let data
        if (params.name === 'xiaohongshu_list_notes') {
          data = await browser.listNotes(args.profile_key, args.profile_url, args.limit as number | undefined, extra.signal)
        } else if (params.name === 'xiaohongshu_list_comments') {
          if (typeof args.note_id !== 'string') throw new BrowserFailure('XHS_INVALID_INPUT', '需要有效的笔记 ID')
          data = await browser.listComments(args.profile_key, args.profile_url, args.note_id, args.limit as number | undefined, extra.signal)
        } else {
          data = await browser.openConversation(args.profile_key, args.profile_url, extra.signal)
        }
        result = { ...common, success: true, code: 'OK', retryable: false,
          message: '已读取小红书页面结果', data }
      }
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
