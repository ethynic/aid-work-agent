#!/usr/bin/env node
/**
 * BOSS 招聘操作 CLI 入口。
 *
 * 操作命令（filter / greet / goto / accept / reject / interview / send-to / send-current /
 * list-jobs / select-job / resume-detail / resume-batch，其中 filter 含设置与 --clear 两种用法）+ 3 个标准 Provider 命令（mcp / doctor / version）。
 * 操作命令只是薄 renderer，业务能力在 src/main/operations/，与 MCP tool handler 共享。
 *
 * 用法：
 *   node dist/src/cli/index.js filter [--experience 5-10年] [--education 本科,硕士,博士] [--salary 10-20K]
 *   node dist/src/cli/index.js filter --clear
 *   node dist/src/cli/index.js greet --names 冯修业,李四 [--limit N]
 *   node dist/src/cli/index.js greet --all [--limit N]
 *   node dist/src/cli/index.js goto recommend|chat
 *   node dist/src/cli/index.js accept [--limit N] [--no-preview]
 *   node dist/src/cli/index.js reject
 *   node dist/src/cli/index.js interview [--remark "..."]
 *   node dist/src/cli/index.js send-to <姓名> --message <消息> [--dry-run]
 *   node dist/src/cli/index.js send-current --message <消息> [--dry-run]
 *   node dist/src/cli/index.js list-jobs
 *   node dist/src/cli/index.js select-job <职位名>
 *   node dist/src/cli/index.js resume-detail [--name <姓名>] [--save-image <path>]
 *   node dist/src/cli/index.js resume-batch [--limit N] [--save-dir <目录>]
 *   node dist/src/cli/index.js mcp --stdio
 *   node dist/src/cli/index.js doctor
 *   node dist/src/cli/index.js version [--json]
 *
 * Chrome 接入：CLI 不启动 Chrome，只 attach 你日常登录 BOSS、带调试端口启动的 Chrome，例如：
 *   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
 * 退出只断开连接，绝不关闭你的 Chrome。
 */
import { parseArgs, flagString, hasFlag } from './args.js'
import { parseGreetNames, validateCommandArgs } from './validate.js'

const USAGE = `BOSS 招聘操作 CLI

操作命令：
  filter-options                 查询筛选面板全部可选档位（只读；给 AI/用户选精确档位用；开收面板借鼠标约 2 秒）
  filter [--experience 5-10年] [--education 本科,硕士,博士] [--salary 10-20K]   自动设置筛选面板（Win32 真实鼠标，期间勿动鼠标）
  filter --clear              清除全部筛选（开面板 → 清除 → 确定，期间勿动鼠标）
  greet --names <姓名>[,<姓名2>,<姓名3>] [--limit N]   定向打招呼：只打姓名精确匹配的人（1-3 人，半角/全角逗号分隔；配对失败一律跳过——宁可不打，不能打错；真实写动作，期间勿动鼠标）
  greet --all [--limit N]       全量打招呼：从推荐列表顶部逐个点击（默认 10 上限，最大 100；当前屏点完自动滚动，到底结束；必须显式 --all 确认全量意图；真实写动作，期间勿动鼠标）
  goto recommend|chat         点击左侧菜单跳转页面：recommend=推荐牛人，chat=沟通（看打招呼回复）；已在目标页自动跳过
  accept [--limit N] [--no-preview]   逐个打开「对方想发送附件简历」的会话并点「同意」接收简历，同意后自动点开预览再关闭（默认 20 上限，最大 100；不在沟通页自动先跳转）
  reject                    把沟通页当前会话的候选人标记为「不合适」（弹确认层自动点确定；真实写动作，期间勿动鼠标）
  interview [--remark "..."]  约面试表单填充演示：逐字填备注+选明天日期后点取消关闭（绝不点发送；期间勿动鼠标）
  send-to <姓名> --message <消息> [--dry-run]   搜索找人 → 进入对话 → 输入并发送消息（默认真发送；--dry-run 只输入不发送；期间勿动鼠标）
  send-current --message <消息> [--dry-run]     向当前已选会话输入并发送消息（前提已选会话；默认真发送；--dry-run 只输入不发送；期间勿动鼠标）
  list-jobs                    列出当前招聘者的所有职位（打开职位下拉解析；只读，但借用真实鼠标点开下拉，期间勿动鼠标）
  select-job <职位名>          切换到指定职位（精确职位名，可用 list-jobs 查看；真实写动作，期间勿动鼠标）
  resume-detail [--name <姓名>] [--save-image <path>]   读取当前打开的候选人简历详情（canvas 截图拼接 OCR；--name 候选人姓名，缺省从 OCR 首行自动识别；前提先点开候选人详情；只读，但滚动借用真实鼠标，期间勿动鼠标）
  resume-batch [--limit N] [--save-dir <目录>]   批量打开推荐牛人卡片并读取简历（默认 1 份，最大 10；逐个点开→读取→关闭→下一份；只读，但滚动借用真实鼠标约 30 秒/份，期间勿动鼠标）

Provider 命令：
  mcp --stdio                 启动标准本地 MCP server（stdout 只承载协议，供 Codex/WorkBuddy 等 Host 使用）
  doctor                      只读环境检查（点击脚本 / CDP 连接 / BOSS 页面 / 登录态），任一失败退出码 1
  version [--json]            版本信息；--json 输出机器可读 manifest（含 schema_digest）

全局参数：
  --cdp-port <端口>             Chrome 调试端口（默认 9222）
  --help / -h                   查看本用法说明（任何命令可用；未知参数会被拒绝而不是静默忽略）

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

  // --help/-h 短路：任何命令（含未知命令）先打用法退出 0，必须先于参数校验生效。
  // 真机事故根因之一：`greet --help` 的 help 曾被静默忽略、按默认参数执行了真实写动作
  if (args.flags.has('help') || args.flags.has('h')) {
    console.log(USAGE)
    return 0
  }

  // 参数校验 fail-loud（validate.ts）：未知 flag / 多余位置参数 / greet 缺显式意图 → 拒绝执行。
  // 解析器曾静默忽略未知参数，是两起真机事故（greet --help 误执行 / greet 冯修业 姓名被丢弃）的共同根因
  const validation = validateCommandArgs(command, args)
  if (!validation.ok) {
    console.error(validation.message)
    return 2
  }

  switch (command) {
    case 'filter-options': {
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { filterOptionsCommand } = await import('./commands/filterOptions.js')
      return filterOptionsCommand({ cdpPort })
    }
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
      // 定向名单取值（合法性已由 validateCommandArgs 用同一 parseGreetNames 校验过；
      // 此处再查一次是为防御校验层被绕过的直接调用路径，fail-loud 不吞错）
      const namesRaw = flagString(args, 'names')
      let names: string[] | undefined
      if (namesRaw !== undefined) {
        const parsed = parseGreetNames(namesRaw)
        if (!parsed.ok) {
          console.error(parsed.message)
          return 2
        }
        names = parsed.names
      }
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { greetCommand } = await import('./commands/greet.js')
      return greetCommand({ limit, names, all: hasFlag(args, 'all'), cdpPort })
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
    case 'resume-detail': {
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { resumeDetailCommand } = await import('./commands/resumeDetail.js')
      return resumeDetailCommand({
        name: flagString(args, 'name'),
        saveImage: flagString(args, 'save-image'),
        cdpPort,
      })
    }
    case 'resume-batch': {
      const limitRaw = flagString(args, 'limit')
      let limit: number | undefined
      if (limitRaw !== undefined) {
        limit = Number(limitRaw)
        if (!Number.isInteger(limit) || limit <= 0 || limit > 10) {
          console.error(`resume-batch --limit 必须是 1-10 的整数（默认 1）：${limitRaw}`)
          return 2
        }
      }
      const saveDir = flagString(args, 'save-dir')
      if (saveDir === '') {
        console.error('resume-batch --save-dir 不能为空')
        return 2
      }
      const cdpPort = parseCdpPort(args)
      if (cdpPort === 'invalid') return 2
      const { resumeBatchCommand } = await import('./commands/resumeBatch.js')
      return resumeBatchCommand({ limit, saveDir, cdpPort })
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
