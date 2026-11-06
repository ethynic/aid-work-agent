/**
 * aid-wecom CLI 入口。
 *
 * M1 实现：probe / add-customer / mcp / doctor / version。
 * M2 增加：search / send。
 * M3 增加：unread / read / watch。
 * M5 增加：select。
 * M7 增加：send-image。
 * M8 增加：send-file。
 * M9：read 改名 read-session（旧 read 动词直接废弃，无别名）。
 * M11a：send / send-image / send-file / read-session 增加 --target-name 直达模式
 * （与 --target-ref 二选一：文本发送先由本地库唯一解析目标）。
 * 动词子命令集合固定；不存在按对象命名的子命令——`aid-wecom contacts` 之类
 * 一律落入 default 报「未知子命令」。
 *
 * 用法：
 *   node dist/src/cli/index.js probe [--verbose] [--json]
 *   node dist/src/cli/index.js search --query <词> [--type contact|group|any] [--limit N] [--json]
 *   node dist/src/cli/index.js select --target-ref <ref> [--json]
 *   node dist/src/cli/index.js send (--target-ref <ref> | --target-name <会话名> [--subtitle <副标题>]) --text <文本> [--json]
 *   node dist/src/cli/index.js send-image (--target-ref <ref> | --target-name <会话名>) --image <本地绝对路径> [--json]
 *   node dist/src/cli/index.js send-file (--target-ref <ref> | --target-name <会话名>) --file <本地绝对路径> [--json]
 *   node dist/src/cli/index.js unread [--name <名>] [--channel local-db] [--focus <名1,名2>] [--json]
 *   node dist/src/cli/index.js read-session (--target-ref <ref> | --target-name <会话名>) [--channel local-db] [--limit N] [--cursor <cursor>] [--since-days N] [--json]
 *   node dist/src/cli/index.js read-session --session <ws1.…> (--date YYYY-MM-DD | --cursor <wd1.…>) [--timezone IANA] [--include-context] [--limit N] [--json]
 *   node dist/src/cli/index.js watch [--interval 秒] [--once] [--channel local-db] [--focus <名1,名2>]
 *   node dist/src/cli/index.js add-customer --phone <11位手机号> --yes [--json]
 *   node dist/src/cli/index.js mcp --stdio
 *   node dist/src/cli/index.js doctor [--json] [--localdb]
 *   node dist/src/cli/index.js version [--json]
 */
import { flagString, parseArgs, hasFlag } from './args.js'

const USAGE = `aid-wecom — 企业微信操作 CLI / MCP Provider（M1）

操作命令：
  read-session --session <会话名或稳定标识> [--cursor <原游标>] [--limit N] [--attachment <JSON>] [--json]
  read-session --session <ws1.稳定标识> (--date YYYY-MM-DD | --cursor <wd1.日期游标>) [--timezone IANA] [--include-context] [--limit N≤100] [--json]  （日期模式：续页只传日期游标）
  read-sessions [--sessions <JSON字符串数组>] [--cursor <原游标>] [--limit N] [--json]
                            DB批量聊天记录与待回复候选，单批最多50条/16KiB
  send --session <会话名或稳定标识> --text <文本> [--json]
                            本地库准入、双点缓存标定、复制标题确认、发送后本地库核验
  calibrate [--json]         用本人会话标定标题首字和文本输入区中心并缓存窗口相对坐标

兼容与诊断命令：
  poll-group --target-name <完整群名> [--cursor <原游标>] [--json]
                            本地库独立增量读取；首次历史仅作上下文，后续原样回传各群游标
  list-groups [--query <关键词>] [--limit N] [--json]
                            本地库搜索/列出已同步群（默认100），不操作界面；has_more标注截断
  read-group --target-name <完整群名> [--limit N] [--from-time <时间>] [--to-time <时间>] [--json]
                            本地库读取最近N条（默认100），按时间升序；时间区间含两端
                            时间：YYYY-MM-DD HH:mm:ss（北京时间）或带时区RFC3339
  probe [--verbose] [--json]
                            只读环境探测：平台 / 交互会话 / PowerShell / WXWork.exe 进程 /
                            主窗口解析 / 登录态 / 当前内容页（不激活窗口、不发送输入、不改剪贴板）
  add-customer --phone <11位手机号> --yes [--json]
                            按手机号检索并发送添加客户邀请（写动作；--yes 显式确认；
                            失败不自动重试，effect=unknown 时请人工核对）
  search --query <词> [--type contact|group|any] [--limit N] [--json]
                            搜索联系人/群聊（只读），返回带 target_ref 的候选（5 分钟有效）
  select --target-ref <ref> [--json]
                            点击 search 返回的搜索结果进入会话（动作；不发送消息，
                            进入会话会清除其未读角标并切换当前会话视图；
                            前置：搜索面板仍打开，ref 5 分钟内有效）
  send (--target-ref <ref> | --target-name <会话名> [--subtitle <副标题>]) --text <文本> [--json]
                            向目标发送 1 条文本消息（写动作；本地库唯一解析目标；
                            无法消歧时拒绝；发送后本地库核验失败不自动重试，
                            effect=unknown 时请人工核对）
  send-status --receipt-id <ID> [--json]
                            只读对账原发送凭据；unknown 时勿重发，不切群、不输入、不发消息
  send-image (--target-ref <ref> | --target-name <会话名>) --image <本地绝对路径> [--json]
                            向目标发送 1 张图片（写动作；png/jpg/jpeg/bmp/gif
                            ≤20MB，路径须为装有 wecom-cli 的机器上的本地绝对路径，调用方
                            负责落盘；粘贴经剪贴板通道会覆盖用户剪贴板；发送后校验失败
                            不自动重试，effect=unknown 时请人工核对）
  send-file (--target-ref <ref> | --target-name <会话名>) --file <本地绝对路径> [--json]
                            向目标发送 1 个文件（写动作；扩展名不限，
                            ≤100MB，路径须为装有 wecom-cli 的机器上的本地绝对路径，
                            文件名去空白后须 ≥3 字符（过短拒发），调用方负责落盘；
                            粘贴经剪贴板通道会覆盖用户剪贴板；
                            发送后校验失败不自动重试，effect=unknown 时请人工核对）
  unread [--name <名>] [--channel local-db] [--focus <名1,名2>] [--json]
                            从本地库读取待回复会话快照，可发现已读未回复；不切会话或清角标。
                            --name 按显示名子串过滤；--focus 逐名解析，失败进 failed。
  read-session (--target-ref <ref> | --target-name <会话名>) [--channel local-db] [--limit N] [--cursor <cursor>] [--since-days N] [--json]
                            从本地库读取消息与增量游标；同名目标须消歧，不切会话或清角标。
  watch [--interval 秒] [--once] [--channel local-db] [--focus <名1,名2>]
                            本地库新消息跟踪循环：事件 NDJSON 逐行写 stdout，Ctrl+C 退出。
                            DB 不可用时保留水位并报错，下轮重试；--focus 首次解析后绑定会话身份。
                            以上读取只支持已验证的企业微信版本，旧 --channel ui 会明确报错。

Provider 命令：
  mcp --stdio               启动标准本地 MCP server（stdout 只承载协议，供 agent Host 使用）
  doctor [--json] [--localdb]
                          只读环境检查（平台 / 交互会话 / PowerShell / artifact 目录），任一失败退出码 1；
                          --localdb 追加本地库深检段（结论写 localdb-state.json）
  version [--json]          版本信息；--json 输出机器可读 manifest（含 schema_digest）

命名约定：所有命令以动词开头；不存在按对象命名的子命令。
`

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2))
  const command = args.positional[0]

  switch (command) {
    case 'list-groups':
    case 'poll-group':
    case 'read-group': {
      const { groupsCommand } = await import('./commands/groups.js')
      return groupsCommand(args)
    }
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
    case 'select': {
      const { selectCommand } = await import('./commands/select.js')
      return selectCommand({
        targetRef: flagString(args, 'target-ref'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'send': {
      const { sendCommand } = await import('./commands/send.js')
      return sendCommand({
        session: flagString(args, 'session'),
        groupCursor: flagString(args, 'group-cursor'),
        targetRef: flagString(args, 'target-ref'),
        targetName: flagString(args, 'target-name'),
        subtitle: flagString(args, 'subtitle'),
        text: flagString(args, 'text'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'calibrate': {
      const { calibrateCommand } = await import('./commands/calibrate.js')
      return calibrateCommand({ json: hasFlag(args, 'json') })
    }
    case 'send-status': {
      const { sendStatusCommand } = await import('./commands/sendStatus.js')
      return sendStatusCommand({ receiptId: flagString(args, 'receipt-id'), json: hasFlag(args, 'json') })
    }
    case 'send-image': {
      const { sendImageCommand } = await import('./commands/sendImage.js')
      return sendImageCommand({
        targetRef: flagString(args, 'target-ref'),
        targetName: flagString(args, 'target-name'),
        image: flagString(args, 'image'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'send-file': {
      const { sendFileCommand } = await import('./commands/sendFile.js')
      return sendFileCommand({
        targetRef: flagString(args, 'target-ref'),
        targetName: flagString(args, 'target-name'),
        file: flagString(args, 'file'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'unread': {
      const { unreadCommand } = await import('./commands/unread.js')
      return unreadCommand({
        name: flagString(args, 'name'),
        channel: flagString(args, 'channel'),
        focus: flagString(args, 'focus'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'read-session': {
      const { readSessionCommand } = await import('./commands/readSession.js')
      return readSessionCommand({
        session: flagString(args, 'session'),
        targetRef: flagString(args, 'target-ref'),
        targetName: flagString(args, 'target-name'),
        maxPages: flagString(args, 'max-pages'),
        sinceDays: flagString(args, 'since-days'),
        channel: flagString(args, 'channel'),
        limit: flagString(args, 'limit'),
        cursor: flagString(args, 'cursor'),
        attachment: flagString(args, 'attachment'),
        date: flagString(args, 'date'),
        timezone: flagString(args, 'timezone'),
        includeContext: hasFlag(args, 'include-context'),
        json: hasFlag(args, 'json'),
      })
    }
    case 'read-sessions': {
      const { readSessionsCommand } = await import('./commands/readSessions.js')
      return readSessionsCommand(args)
    }
    case 'watch': {
      const { watchCommand } = await import('./commands/watch.js')
      return watchCommand({
        interval: flagString(args, 'interval'),
        once: hasFlag(args, 'once'),
        channel: flagString(args, 'channel'),
        focus: flagString(args, 'focus'),
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
      return doctorCommand({ json: hasFlag(args, 'json'), localdb: hasFlag(args, 'localdb') })
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
