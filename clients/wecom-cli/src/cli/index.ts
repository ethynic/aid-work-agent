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
 * （与 --target-ref 二选一：内部自动 search 定位，data 附 resolved_target）。
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
 *   node dist/src/cli/index.js unread [--name <名>] [--channel local-db|ui] [--focus <名1,名2>] [--json]
 *   node dist/src/cli/index.js read-session (--target-ref <ref> | --target-name <会话名>) [--channel local-db|ui] [--limit N] [--cursor <cursor>] [--max-pages N] [--since-days N] [--json]
 *   node dist/src/cli/index.js watch [--interval 秒] [--once] [--channel local-db|ui] [--focus <名1,名2>]
 *   node dist/src/cli/index.js add-customer --phone <11位手机号> --yes [--json]
 *   node dist/src/cli/index.js mcp --stdio
 *   node dist/src/cli/index.js doctor [--json] [--localdb]
 *   node dist/src/cli/index.js version [--json]
 */
import { flagString, parseArgs, hasFlag } from './args.js'

const USAGE = `aid-wecom — 企业微信操作 CLI / MCP Provider（M1）

操作命令：
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
                            向目标发送 1 条文本消息（写动作；target-ref 与 target-name
                            二选一——name 直达模式内部自动 search 定位，同名多候选
                            无法消歧时拒绝；发送后校验失败不自动重试，
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
  unread [--name <名>] [--channel local-db|ui] [--focus <名1,名2>] [--json]
                            未读/待回复会话快照（只读，不开会话不清角标）：
                            缺省按 doctor 状态分派——本地库可用返回待回复快照
                            （judgment_basis=last_sender，可发现已读未回复）；否则 UI 角标
                            快照（judgment_basis=badge，附能力差异说明）。
                            --channel 显式覆盖（local-db 失败不兜底）；--name 按会话显示名
                            子串过滤（两通道一致；本地库通道无显示名的会话会被排除）；
                            --focus 关注会话名列表（本地库逐名解析，失败进 failed；UI 通道忽略）
  read-session (--target-ref <ref> | --target-name <会话名>) [--channel local-db|ui] [--limit N] [--cursor <cursor>] [--max-pages N] [--since-days N] [--json]
                            读会话消息（只读内容；进入会话会清除其未读角标）：
                            智能分发进会话（当前会话对→直读；不对→自动 search+select
                            切换后读）→ 滚动截屏 OCR + 页间去重，返回
                            {title, messages:[{side,text,time?}], pages_read}
                            （M9 由 read 改名，旧 read 动词废弃）
  watch [--interval 秒] [--once] [--channel local-db|ui] [--focus <名1,名2>]
                            新消息跟踪循环：事件 NDJSON 逐行写 stdout，Ctrl+C 干净退出；
                            --once 单轮。--channel 显式通道（缺省按 doctor 状态分派：
                            本地库可用走 local-db 冻结点 4 增量事件 {channel,new_messages,
                            last_sender}，含消息 ID 与精确方向、不切会话清角标，DB 不可用
                            轮不落 UI 等下轮）；--focus 关注输入列表（会话名或账号绑定
                            target_ref；仅本地库通道生效，建立时一次性解析持久绑定、后续
                            按绑定轮询（改名不影响跟踪），解析失败进 failed；UI 模式忽略）

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
        groupCursor: flagString(args, 'group-cursor'),
        targetRef: flagString(args, 'target-ref'),
        targetName: flagString(args, 'target-name'),
        subtitle: flagString(args, 'subtitle'),
        text: flagString(args, 'text'),
        json: hasFlag(args, 'json'),
      })
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
        targetRef: flagString(args, 'target-ref'),
        targetName: flagString(args, 'target-name'),
        maxPages: flagString(args, 'max-pages'),
        sinceDays: flagString(args, 'since-days'),
        channel: flagString(args, 'channel'),
        limit: flagString(args, 'limit'),
        cursor: flagString(args, 'cursor'),
        json: hasFlag(args, 'json'),
      })
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
