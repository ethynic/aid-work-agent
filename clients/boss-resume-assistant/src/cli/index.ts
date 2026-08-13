#!/usr/bin/env node
/**
 * BOSS 招聘操作 CLI 入口。
 *
 * 操作命令（filter / greet / goto / accept / reject / interview / send-to / send-current /
 * list-jobs / select-job，其中 filter 含设置与 --clear 两种用法）+ 3 个标准 Provider 命令（mcp / doctor / version）。
 * 操作命令只是薄 renderer，业务能力在 src/main/operations/，与 MCP tool handler 共享。
 *
 * 用法：
 *   node dist/src/cli/index.js filter [--experience 5-10年] [--education 本科,硕士,博士] [--salary 10-20K]
 *   node dist/src/cli/index.js filter --clear
 *   node dist/src/cli/index.js greet [--limit N]
 *   node dist/src/cli/index.js goto recommend|chat
 *   node dist/src/cli/index.js accept [--limit N] [--no-preview]
 *   node dist/src/cli/index.js reject
 *   node dist/src/cli/index.js interview [--remark "..."]
 *   node dist/src/cli/index.js send-to <姓名> --message <消息> [--dry-run]
 *   node dist/src/cli/index.js send-current --message <消息> [--dry-run]
 *   node dist/src/cli/index.js list-jobs
 *   node dist/src/cli/index.js select-job <职位名>
 *   node dist/src/cli/index.js mcp --stdio
 *   node dist/src/cli/index.js doctor
 *   node dist/src/cli/index.js version [--json]
 *
 * Chrome 接入：CLI 不启动 Chrome，只 attach 你日常登录 BOSS、带调试端口启动的 Chrome，例如：
 *   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
 * 退出只断开连接，绝不关闭你的 Chrome。
 */
import { parseArgs, flagString, hasFlag } from './args.js'

const USAGE = `BOSS 招聘操作 CLI

操作命令：
  filter [--experience 5-10年] [--education 本科,硕士,博士] [--salary 10-20K]   自动设置筛选面板（Win32 真实鼠标，期间勿动鼠标）
  filter --clear              清除全部筛选（开面板 → 清除 → 确定，期间勿动鼠标）
  greet [--limit N]           逐个打招呼（默认 10 上限，最大 100；当前屏点完自动滚动，到底结束；真实写动作，期间勿动鼠标）
  goto recommend|chat         点击左侧菜单跳转页面：recommend=推荐牛人，chat=沟通（看打招呼回复）；已在目标页自动跳过
  accept [--limit N] [--no-preview]   逐个打开「对方想发送附件简历」的会话并点「同意」接收简历，同意后自动点开预览再关闭（默认 20 上限，最大 100；不在沟通页自动先跳转）
  reject                    把沟通页当前会话的候选人标记为「不合适」（弹确认层自动点确定；真实写动作，期间勿动鼠标）
  interview [--remark "..."]  约面试表单填充演示：逐字填备注+选明天日期后点取消关闭（绝不点发送；期间勿动鼠标）
  send-to <姓名> --message <消息> [--dry-run]   搜索找人 → 进入对话 → 输入并发送消息（默认真发送；--dry-run 只输入不发送；期间勿动鼠标）
  send-current --message <消息> [--dry-run]     向当前已选会话输入并发送消息（前提已选会话；默认真发送；--dry-run 只输入不发送；期间勿动鼠标）
  list-jobs                    列出当前招聘者的所有职位（打开职位下拉解析；只读，但借用真实鼠标点开下拉，期间勿动鼠标）
  select-job <职位名>          切换到指定职位（精确职位名，可用 list-jobs 查看；真实写动作，期间勿动鼠标）

Provider 命令：
  mcp --stdio                 启动标准本地 MCP server（stdout 只承载协议，供 Codex/WorkBuddy 等 Host 使用）
  doctor                      只读环境检查（点击脚本 / CDP 连接 / BOSS 页面 / 登录态），任一失败退出码 1
  version [--json]            版本信息；--json 输出机器可读 manifest（含 schema_digest）

全局参数：
  --cdp-port <端口>             Chrome 调试端口（默认 9222）

Chrome 准备（CLI 不启动 Chrome，只 attach 你日常的 Chrome）：
  1. 关闭所有 Chrome 窗口
  2. 带调试端口启动日常 Chrome，例如：
     "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222
  3. 确认已登录 BOSS 直聘并打开目标页面，回到本终端执行命令
  退出只断开连接，绝不关闭你的 Chrome。
`

/** 解析 --cdp-port：非法值打印错误并返回 'invalid'，未提供返回 undefined */
function parseCdpPort(args: ReturnType<typeof parseArgs>): number | undefined | 'invalid' {
  const raw = flagString(args, 'cdp-port')
  if (raw === undefined) return undefined
  const port = Number(raw)
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    console.error(`--cdp-port 必须是 1-65535 的整数：${raw}`)
    return 'invalid'
  }
  return port
}

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2))
  const command = args.positional[0]

  switch (command) {
    case 'filter': {
      const educationRaw = flagString(args, 'education')
      const educations = educationRaw
        ? educationRaw.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
        : undefined
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { filterCommand } = await import('./commands/filter.js')
      return filterCommand({
        experience: flagString(args, 'experience'),
        educations,
        salary: flagString(args, 'salary'),
        cdpPort,
        clear: hasFlag(args, 'clear'),
      })
    }
    case 'greet': {
      const limitRaw = flagString(args, 'limit')
      let limit: number | undefined
      if (limitRaw !== undefined) {
        limit = Number(limitRaw)
        if (!Number.isInteger(limit) || limit <= 0 || limit > 100) {
          console.error(`greet --limit 必须是 1-100 的整数（默认 10）：${limitRaw}`)
          return 2
        }
      }
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { greetCommand } = await import('./commands/greet.js')
      return greetCommand({ limit, cdpPort })
    }
    case 'goto': {
      const target = args.positional[1]
      if (!target) {
        console.error('goto 缺少目标页面：recommend（推荐牛人）/ chat（沟通）')
        return 2
      }
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { gotoCommand } = await import('./commands/goto.js')
      return gotoCommand({ target, cdpPort })
    }
    case 'accept': {
      const limitRaw = flagString(args, 'limit')
      let limit: number | undefined
      if (limitRaw !== undefined) {
        limit = Number(limitRaw)
        if (!Number.isInteger(limit) || limit <= 0 || limit > 100) {
          console.error(`accept --limit 必须是 1-100 的整数（默认 20）：${limitRaw}`)
          return 2
        }
      }
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { acceptCommand } = await import('./commands/accept.js')
      return acceptCommand({ limit, cdpPort, preview: !hasFlag(args, 'no-preview') })
    }
    case 'reject': {
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { rejectCommand } = await import('./commands/reject.js')
      return rejectCommand({ cdpPort })
    }
    case 'interview': {
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { interviewCommand } = await import('./commands/interview.js')
      return interviewCommand({ remark: flagString(args, 'remark'), cdpPort })
    }
    case 'send-to': {
      const to = args.positional[1]
      if (!to) {
        console.error('send-to 缺少联系人姓名：send-to <姓名> --message <消息> [--dry-run]')
        return 2
      }
      const message = flagString(args, 'message')
      if (!message) {
        console.error('send-to 缺少 --message <消息>')
        return 2
      }
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { sendToCommand } = await import('./commands/sendTo.js')
      return sendToCommand({ to, message, dryRun: hasFlag(args, 'dry-run'), cdpPort })
    }
    case 'send-current': {
      const message = flagString(args, 'message')
      if (!message) {
        console.error('send-current 缺少 --message <消息>')
        return 2
      }
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { sendCurrentCommand } = await import('./commands/sendCurrent.js')
      return sendCurrentCommand({ message, dryRun: hasFlag(args, 'dry-run'), cdpPort })
    }
    case 'list-jobs': {
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { listJobsCommand } = await import('./commands/listJobs.js')
      return listJobsCommand({ cdpPort })
    }
    case 'select-job': {
      const jobName = args.positional[1]
      if (!jobName) {
        console.error('select-job 缺少职位名：select-job <职位名>（可用 list-jobs 查看精确职位名）')
        return 2
      }
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { selectJobCommand } = await import('./commands/selectJob.js')
      return selectJobCommand({ jobName, cdpPort })
    }
    case 'mcp': {
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { mcpCommand } = await import('./commands/mcp.js')
      return mcpCommand({ stdio: hasFlag(args, 'stdio'), cdpPort })
    }
    case 'doctor': {
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { doctorCommand } = await import('./commands/doctor.js')
      return doctorCommand({ cdpPort })
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
