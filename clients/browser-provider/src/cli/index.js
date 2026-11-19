import { createHash, randomUUID } from 'node:crypto'
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import { CallToolRequestSchema, ErrorCode, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { chromePath, nativePath, openNative } from '../native.js'

const root = fileURLToPath(new URL('../../../', import.meta.url))
const identity = JSON.parse(readFileSync(join(root, 'provider-manifest.json'), 'utf8'))
const { version } = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const tools = JSON.parse(readFileSync(new URL('./native-tools.json', import.meta.url), 'utf8'))
const byName = new Map(tools.map(tool => [tool.name, tool]))
const batchCommands = new Set([
  'open', 'goto', 'navigate', 'read', 'snapshot', 'back', 'forward', 'reload', 'click', 'fill', 'type',
  'press', 'check', 'uncheck', 'select', 'scroll', 'wait', 'get', 'tab', 'eval', 'close', 'upload',
])
const batchReadOnly = new Set(['read', 'snapshot', 'get', 'wait'])
const uploadHint = '请将选定的文件放入本机 %LOCALAPPDATA%\\AID-Work\\browser-uploads 目录后，使用其绝对路径'
const urlHint = '浏览器插件仅接受 HTTP 或 HTTPS 页面地址'
const tabHint = '如需在新标签打开链接，请使用 HTTP 或 HTTPS 地址调用 agent_browser_tab_new'

function sortedKeys(value) {
  if (Array.isArray(value)) return value.map(sortedKeys)
  if (value === null || typeof value !== 'object') return value
  return Object.fromEntries(Object.keys(value).sort().map(key => [key, sortedKeys(value[key])]))
}

function manifest() {
  const digest = createHash('sha256').update(JSON.stringify(sortedKeys(tools))).digest('hex')
  return {
    ...identity, provider_version: version, tools, schema_digest: `sha256:${digest}`,
    tool_declaration: { version: 1, tools: tools.map(({ name }) => ({ name })) },
  }
}

function doctor() {
  const platformReady = process.platform === 'win32' && process.arch === 'x64'
  const checks = [
    [platformReady, '浏览器插件需要 Windows x64'],
    [existsSync(nativePath(root)), '插件内的 agent-browser 执行器缺失，请重新安装插件'],
    [Boolean(chromePath()), '未找到本机 Google Chrome，请先安装 Chrome'],
  ]
  const reason = checks.find(([ready]) => !ready)?.[1]
  return { success: !reason, runtime_readiness: reason ? { ready: false, reason } : { ready: true } }
}

function operationReply(runId, code, message, effect, data = null, attachments = []) {
  const result = { success: code === 'OK', code, message, effect, data, retryable: false, run_id: runId }
  return {
    structuredContent: result,
    content: [{ type: 'text', text: JSON.stringify(result) }, ...attachments],
  }
}

function localFiles(files) {
  if (!process.env.LOCALAPPDATA || !Array.isArray(files) || files.length === 0) return false
  const base = resolve(process.env.LOCALAPPDATA, 'AID-Work', 'browser-uploads')
  try {
    if (realpathSync.native(base).toLowerCase() !== base.toLowerCase()) return false
    return files.every(file => {
      if (typeof file !== 'string' || !isAbsolute(file) || file.split(/[\\/]/).includes('..')) return false
      const path = resolve(file)
      const within = relative(base, path)
      if (!within || isAbsolute(within) || within === '..' || within.startsWith('..' + sep) || within.includes(':')) return false
      return realpathSync.native(path).toLowerCase() === path.toLowerCase() && statSync(path).isFile()
    })
  } catch {
    return false
  }
}

function urlAllowed(value) {
  if (value === undefined || value === 'about:blank') return true
  if (typeof value !== 'string' || !value || value.trim() !== value) return false
  if (/^https?:\/\//i.test(value)) return URL.canParse(value)
  return !/^(?:about|chrome(?:-extension)?|data|file|javascript):/i.test(value) &&
    !value.includes('://') && URL.canParse('https://' + value)
}

function batchItemError(command) {
  if (!Array.isArray(command) || !command.every(word => typeof word === 'string')) return '不属于支持的浏览器操作'
  const [name, argument] = command
  if (name === 'screenshot') return '截图请单独调用 agent_browser_screenshot'
  if (!batchCommands.has(name)) return '不属于支持的浏览器操作'
  if (name === 'upload' && !localFiles(command.slice(2))) return uploadHint
  if (name === 'wait' && command.slice(1).some(word => word === '--download' || word === '-d')) {
    return '批处理不支持等待下载'
  }
  if (name === 'click' && command.includes('--new-tab')) return tabHint
  if (name === 'get' && argument === 'cdp-url') return '不能返回浏览器调试端点'
  if (name === 'open' || name === 'goto' || name === 'navigate') {
    const url = command.slice(1).find(word => !word.startsWith('--'))
    if (!urlAllowed(url)) return urlHint
  }
  if (name === 'tab' && argument === 'new') {
    for (let index = 2; index < command.length; index++) {
      if (command[index] === '--label') index++
      else if (!urlAllowed(command[index])) return urlHint
    }
  }
}

function validate(name, args) {
  if (name === 'agent_browser_upload' && !localFiles(args.files)) return uploadHint
  if (name === 'agent_browser_click' && args.newTab) return tabHint
  if (('url' in args) && !urlAllowed(args.url)) return urlHint
  if (args.extraArgs?.length || args.restore) return '此插件的浏览器配置由 Runtime 管理'
  if (name === 'agent_browser_screenshot') {
    if ('path' in args || 'screenshotDir' in args) return '截图由插件保存在临时目录，请省略 path 和 screenshotDir'
    const { selector, format } = args
    if (format !== undefined && !['png', 'jpg', 'jpeg', 'webp'].includes(format)) return '请选择截图格式'
    // Native CLI distinguishes a single positional selector from an output path.
    if (typeof selector === 'string' && (selector.startsWith('-') || /^\.\.?\//.test(selector) ||
      (!/^[.#@]/.test(selector) && /\/|\.(?:png|jpg|jpeg|webp)$/.test(selector)))) {
      return '请使用页面元素引用或 CSS 选择器'
    }
  }
  if (name !== 'agent_browser_batch') return
  if (!Array.isArray(args.commands) || args.commands.length === 0) return '批处理需要至少一条浏览器操作'
  for (const [index, command] of args.commands.entries()) {
    const reason = batchItemError(command)
    if (reason) return `批处理第 ${index + 1} 项：${reason}，整批未执行`
  }
}

function batchReply(runId, args, reply, results) {
  const attempted = new Set(results.map((item, index) => item.index ?? index))
  const unattempted = args.commands.flatMap((_, index) => attempted.has(index) ? [] : [index])
  const succeeded = results.filter(result => result.success === true).length
  const failed = results.length - succeeded
  const data = { results, unattempted }
  const message = `批处理完成，${succeeded} 项成功，${failed} 项失败，${unattempted.length} 项未执行`
  const changesPage = results.some((result, index) =>
    result.success === true && !batchReadOnly.has(args.commands[result.index ?? index]?.[0]))
  if (!failed && !unattempted.length && !reply.isError) {
    return operationReply(runId, 'OK', message, changesPage ? 'applied' : 'none', data)
  }
  return operationReply(runId, 'BROWSER_BATCH_INCOMPLETE', message, succeeded ? 'partial' : 'unknown', data)
}

function nativeReply(runId, tool, args, reply) {
  const response = reply.structuredContent?.response
  if (tool.name === 'agent_browser_batch' && Array.isArray(response)) return batchReply(runId, args, reply, response)
  if (tool.name === 'agent_browser_tools_profiles' && !reply.isError && reply.structuredContent) {
    return operationReply(runId, 'OK', '已读取原生 MCP 的分组信息', 'none', {
      ...reply.structuredContent, runtimeTools: tools.map(({ name }) => name),
    })
  }
  const readOnly = tool.annotations?.readOnlyHint === true
  const data = response?.data ?? null
  const details = data === null || (typeof data === 'object' && !Array.isArray(data)) ? data : { value: data }
  const diagnostic = response?.warning || response?.code
    ? { ...details, native_warning: response.warning, native_code: response.code }
    : details
  if (reply.isError || response?.success === false) {
    const message = response?.error || reply.structuredContent?.stderr?.trim() || '浏览器执行器未确认操作结果'
    return operationReply(runId, 'BROWSER_OPERATION_FAILED', message, readOnly ? 'none' : 'unknown', diagnostic)
  }
  if (response == null) {
    return operationReply(runId, 'BROWSER_RESULT_UNKNOWN', '浏览器执行器未返回可解析的结果', readOnly ? 'none' : 'unknown')
  }
  const attachments = reply.content.filter(item => item.type !== 'text')
  return operationReply(runId, 'OK', '浏览器操作成功', readOnly ? 'none' : 'applied', diagnostic, attachments)
}

async function serve() {
  const server = new Server({ name: identity.provider_id, version }, { capabilities: { tools: {} } })
  let native
  let closing
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }))
  server.setRequestHandler(CallToolRequestSchema, async ({ params }, { signal }) => {
    const runId = randomUUID()
    const tool = byName.get(params.name)
    if (!tool) return operationReply(runId, 'BROWSER_TOOL_UNAVAILABLE', '此插件未开放该浏览器工具', 'none')
    const args = params.arguments ?? {}
    let submitted = false
    try {
      const invalid = validate(params.name, args)
      if (invalid) return operationReply(runId, 'INVALID_ARGUMENT', invalid, 'none')
      native ??= openNative(root, 'core,debug').then(session => {
        session.client.onclose = () => { native = undefined }
        return session
      }, error => {
        native = undefined
        throw error
      })
      const { client } = await native
      signal.throwIfAborted()
      submitted = true
      const reply = await client.callTool({ name: params.name, arguments: args }, undefined, { signal, timeout: 120000 })
      return nativeReply(runId, tool, args, reply)
    } catch (error) {
      if (error?.code === ErrorCode.InvalidParams) return operationReply(runId, 'INVALID_ARGUMENT', error.message, 'none')
      const effect = submitted && tool.annotations?.readOnlyHint !== true ? 'unknown' : 'none'
      const message = submitted ? '浏览器执行器连接中断或超时，执行结果未确认' : '浏览器执行器暂不可用，操作未执行'
      return operationReply(runId, 'BROWSER_UNAVAILABLE', message, effect)
    }
  })
  const shutdown = () => {
    closing ??= (async () => {
      await server.close()
      const session = await native
      await session?.close({ closeBrowsers: true })
    })().catch(() => { process.exitCode = 1 })
  }
  process.stdin.once('end', shutdown)
  process.once('SIGINT', shutdown)
  process.once('SIGTERM', shutdown)
  await server.connect(new StdioServerTransport())
}

const args = process.argv.slice(2).join(' ')
switch (args) {
  case 'mcp --stdio':
    await serve()
    break
  case 'version --json':
    console.log(JSON.stringify(manifest()))
    break
  case 'doctor --json': {
    const result = doctor()
    console.log(JSON.stringify(result))
    if (!result.success) process.exitCode = 1
    break
  }
  default:
    console.error('usage: version --json | doctor --json | mcp --stdio')
    process.exitCode = 2
}
