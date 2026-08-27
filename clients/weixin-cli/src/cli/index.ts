/**
 * aid-weixin CLI 入口（设计 §4.1 命令面）。
 *
 * M1 实现：probe / mcp / doctor / version。
 * M2 实现：search --domain chat、send --domain chat、read --domain chat-history|unread。
 * 动词子命令集合固定为 mcp / doctor / version / probe / search / collect / read /
 * get-url / send / follow / lab；操作对象（搜一搜/文章/聊天/公众号）只能作为
 * --domain 等参数值，永远不会有按对象命名的子命令——`aid-weixin souyisou` 之类
 * 一律落入 default 报「未知子命令」。
 *
 * 用法：
 *   node dist/src/cli/index.js probe [--verbose]
 *   node dist/src/cli/index.js search --domain chat --query <词> [--type friend|group|any] [--limit N]
 *   node dist/src/cli/index.js send --domain chat --target-ref <ref> --text <文本>
 *   node dist/src/cli/index.js read --domain chat-history --target-ref <ref> [--since-days N] [--max-pages N]
 *   node dist/src/cli/index.js read --domain unread [--name <名>]
 *   node dist/src/cli/index.js mcp --stdio
 *   node dist/src/cli/index.js doctor [--json]
 *   node dist/src/cli/index.js version [--json]
 */
import { flagString, parseArgs, parseDomainFlag, hasFlag } from './args.js'

const USAGE = `aid-weixin — 微信操作 CLI / MCP Provider（M2）

操作命令：
  probe [--verbose]         只读环境探测：平台 / 交互会话 / PowerShell / Weixin.exe 进程（不激活窗口、不发送输入、不改剪贴板）
  search --domain chat --query <词> [--type friend|group|any] [--limit N]
                            微信全局搜索好友/群（只读，返回带 target_ref 的结果列表）
  send --domain chat --target-ref <ref> --text <文本>
                            向目标发送 1 条文本消息（写动作，发送后校验，失败不自动重试）
  read --domain chat-history --target-ref <ref> [--since-days N] [--max-pages N]
                            读取与目标的聊天记录（会打开会话、清除未读角标）
  read --domain unread [--name <名>]
                            未读会话列表（只读，不开会话不清未读）

Provider 命令：
  mcp --stdio               启动标准本地 MCP server（stdout 只承载协议，供 Codex/WorkBuddy 等 Host 使用）
  doctor [--json]           只读环境检查（平台 / 交互会话 / PowerShell / artifact 目录），任一失败退出码 1
  version [--json]          版本信息；--json 输出机器可读 manifest（含 schema_digest）

命名约定：所有命令以动词开头；搜一搜/文章/聊天/公众号等操作对象只能作为参数值
（如后续版本的 search --domain souyisou），不存在按对象命名的子命令。
`

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2))
  const command = args.positional[0]

  switch (command) {
    case 'probe': {
      const { probeCommand } = await import('./commands/probe.js')
      return probeCommand({ verbose: hasFlag(args, 'verbose') })
    }
    case 'search': {
      const { searchCommand } = await import('./commands/search.js')
      return searchCommand({
        domain: parseDomainFlag(args),
        query: flagString(args, 'query'),
        type: flagString(args, 'type'),
        limit: flagString(args, 'limit'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'send': {
      const { sendCommand } = await import('./commands/send.js')
      return sendCommand({
        domain: parseDomainFlag(args),
        targetRef: flagString(args, 'target-ref'),
        text: flagString(args, 'text'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'read': {
      const { readCommand } = await import('./commands/read.js')
      return readCommand({
        domain: parseDomainFlag(args),
        targetRef: flagString(args, 'target-ref'),
        sinceDays: flagString(args, 'since-days'),
        maxPages: flagString(args, 'max-pages'),
        name: flagString(args, 'name'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'mcp': {
      const { mcpCommand } = await import('./commands/mcp.js')
      return mcpCommand({ stdio: hasFlag(args, 'stdio') })
    }
    case 'doctor': {
      const { doctorCommand } = await import('./commands/doctor.js')
      return doctorCommand({ json: hasFlag(args, 'json') })
    }
    case 'version': {
      const { versionCommand } = await import('./commands/version.js')
      return versionCommand({ json: hasFlag(args, 'json') })
    }
    case undefined:
    case 'help':
      console.log(USAGE)
      return command === undefined ? 2 : 0
    default:
      console.error(`未知子命令：${command}\n\n${USAGE}`)
      return 2
  }
}

main()
  .then((code) => {
    process.exitCode = code
  })
  .catch((e) => {
    // fail-loud：未捕获错误带完整信息退出非零
    console.error(`CLI 执行失败：${e instanceof Error ? (e.stack ?? e.message) : String(e)}`)
    process.exitCode = 1
  })
