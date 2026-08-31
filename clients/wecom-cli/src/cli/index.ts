/**
 * aid-wecom CLI 入口。
 *
 * M1 实现：probe / add-customer / mcp / doctor / version。
 * M2 增加：search / send。
 * M3 增加：unread / read / watch。
 * 动词子命令集合固定；不存在按对象命名的子命令——`aid-wecom contacts` 之类
 * 一律落入 default 报「未知子命令」。
 *
 * 用法：
 *   node dist/src/cli/index.js probe [--verbose] [--json]
 *   node dist/src/cli/index.js search --query <词> [--type contact|group|any] [--limit N] [--json]
 *   node dist/src/cli/index.js send --target-ref <ref> --text <文本> [--json]
 *   node dist/src/cli/index.js unread [--name <名>] [--json]
 *   node dist/src/cli/index.js read --target-ref <ref> [--max-pages N] [--since-days N] [--json]
 *   node dist/src/cli/index.js watch [--interval 秒] [--once]
 *   node dist/src/cli/index.js add-customer --phone <11位手机号> --yes [--json]
 *   node dist/src/cli/index.js mcp --stdio
 *   node dist/src/cli/index.js doctor [--json]
 *   node dist/src/cli/index.js version [--json]
 */
import { flagString, parseArgs, hasFlag } from './args.js'

const USAGE = `aid-wecom — 企业微信操作 CLI / MCP Provider（M1）

操作命令：
  probe [--verbose] [--json]
                            只读环境探测：平台 / 交互会话 / PowerShell / WXWork.exe 进程 /
                            主窗口解析 / 登录态 / 当前内容页（不激活窗口、不发送输入、不改剪贴板）
  add-customer --phone <11位手机号> --yes [--json]
                            按手机号检索并发送添加客户邀请（写动作；--yes 显式确认；
                            失败不自动重试，effect=unknown 时请人工核对）
  search --query <词> [--type contact|group|any] [--limit N] [--json]
                            搜索联系人/群聊（只读），返回带 target_ref 的候选（5 分钟有效）
  send --target-ref <ref> --text <文本> [--json]
                            向 target_ref 目标发送 1 条文本消息（写动作；
                            发送后校验失败不自动重试，effect=unknown 时请人工核对）
  unread [--name <名>] [--json]
                            未读会话快照（只读，不开会话不清角标）：
                            [{name, preview, unread_count}]，可选 --name 子串过滤
  read --target-ref <ref> [--max-pages N] [--since-days N] [--json]
                            读会话消息（只读内容；进入会话会清除其未读角标）：
                            滚动截屏 OCR + 页间去重，返回 {title, messages, pages_read}
  watch [--interval 秒] [--once]
                            新消息跟踪循环：事件 NDJSON 逐行写 stdout
                            （new_messages / tick），Ctrl+C 干净退出；--once 单轮

Provider 命令：
  mcp --stdio               启动标准本地 MCP server（stdout 只承载协议，供 agent Host 使用）
  doctor [--json]           只读环境检查（平台 / 交互会话 / PowerShell / artifact 目录），任一失败退出码 1
  version [--json]          版本信息；--json 输出机器可读 manifest（含 schema_digest）

命名约定：所有命令以动词开头；不存在按对象命名的子命令。
`

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2))
  const command = args.positional[0]

  switch (command) {
    case 'probe': {
      const { probeCommand } = await import('./commands/probe.js')
      return probeCommand({ verbose: hasFlag(args, 'verbose'), json: hasFlag(args, 'json') })
    }
    case 'search': {
      const { searchCommand } = await import('./commands/search.js')
      return searchCommand({
        query: flagString(args, 'query'),
        type: flagString(args, 'type'),
        limit: flagString(args, 'limit'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'send': {
      const { sendCommand } = await import('./commands/send.js')
      return sendCommand({
        targetRef: flagString(args, 'target-ref'),
        text: flagString(args, 'text'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'unread': {
      const { unreadCommand } = await import('./commands/unread.js')
      return unreadCommand({
        name: flagString(args, 'name'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'read': {
      const { readCommand } = await import('./commands/read.js')
      return readCommand({
        targetRef: flagString(args, 'target-ref'),
        maxPages: flagString(args, 'max-pages'),
        sinceDays: flagString(args, 'since-days'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'watch': {
      const { watchCommand } = await import('./commands/watch.js')
      return watchCommand({
        interval: flagString(args, 'interval'),
        once: hasFlag(args, 'once'),
      })
    }
    case 'add-customer': {
      const { addCustomerCommand } = await import('./commands/addCustomer.js')
      return addCustomerCommand({
        phone: flagString(args, 'phone'),
        yes: hasFlag(args, 'yes'),
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
