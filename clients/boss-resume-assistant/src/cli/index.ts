/**
 * BOSS 简历筛选助手 CLI 入口（Phase 9，设计文档 §16 决策 6）。
 *
 * 用法：
 *   node dist/src/cli/index.js run --job <配置文件.yaml|json> [--cdp-port 9222]
 *   node dist/src/cli/index.js filter [--experience 5-10年] [--education 本科,硕士,博士] [--salary 10-20K]
 *   node dist/src/cli/index.js greet [--limit N]
 *   node dist/src/cli/index.js review [--all] [--open]
 *   node dist/src/cli/index.js review override <评估编号> <QUALIFIED|REJECTED> --reason "..."
 *   node dist/src/cli/index.js export [--format csv|json] [--out 路径]
 *   node dist/src/cli/index.js audit [--limit N]
 *
 * 数据目录默认 clients/boss-resume-assistant/data/（--data-dir 覆盖）。
 *
 * Chrome 接入（run）：CLI 不启动 Chrome。先关闭所有 Chrome 窗口，再用你日常
 * 登录 BOSS 的 Chrome 带调试端口启动，例如：
 *   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
 * 确认已登录 BOSS 并打开「推荐」页后回车连接；CLI 只 attach，退出绝不关闭你的 Chrome。
 */
import path from 'node:path'
import { defaultDataDir } from './cliRuntime.js'
import { parseArgs, flagString, hasFlag } from './args.js'
import type { Conclusion } from '../main/screening/ScreeningEngine.js'

const USAGE = `BOSS 简历筛选助手 CLI

用法：
  run --job <配置文件> [--cdp-port 9222]   attach 日常 Chrome 跑筛选任务（运行中 p=暂停 r=恢复 s=停止 q=退出）
  filter [--experience 5-10年] [--education 本科,硕士,博士] [--salary 10-20K]   自动设置筛选面板（Win32 真实鼠标，期间勿动鼠标）
  filter --probe              只读模式：打印筛选面板各行全部选项及坐标，不改动筛选（可用于核对选项合法值）
  filter --clear              清除全部筛选（开面板 → 清除 → 确定，期间勿动鼠标）
  greet [--limit N]           逐个打招呼（默认 10 上限，最大 100；当前屏点完自动滚动，到底结束；真实写动作，期间勿动鼠标）
  review [--all] [--open]       列出 UNCERTAIN 复核队列并生成静态 HTML 复核报告
  review override <编号> <QUALIFIED|REJECTED> --reason "..."  改判复核项
  export [--format csv|json] [--out 路径]   导出评估+动作（默认 ./exports/）
  audit [--limit N]             最近动作 + CDP 审计摘要

全局参数：
  --data-dir <目录>             数据目录（默认 clients/boss-resume-assistant/data/）
  --exports-dir <目录>          复核报告/导出默认输出目录（默认 ./exports/，相对当前工作目录）
  --cdp-port <端口>             Chrome 调试端口（默认 9222，run/filter/greet 生效）

run 的 Chrome 准备（CLI 不启动 Chrome，只 attach 你日常的 Chrome）：
  1. 关闭所有 Chrome 窗口
  2. 带调试端口启动日常 Chrome，例如：
     "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
  3. 确认已登录 BOSS 直聘并打开「推荐」页，回到本终端按回车
  退出（q / Ctrl+C）只断开连接，绝不关闭你的 Chrome。
`

async function main(): Promise<number> {
  const args = parseArgs(process.argv.slice(2))
  const command = args.positional[0]
  const dataDir = path.resolve(flagString(args, 'data-dir') ?? defaultDataDir())
  const exportsDir = path.resolve(flagString(args, 'exports-dir') ?? 'exports')

  switch (command) {
    case 'run': {
      const jobFile = flagString(args, 'job')
      if (!jobFile) {
        console.error('run 缺少 --job <配置文件>')
        return 2
      }
      const { runCommand } = await import('./commands/run.js')
      const cdpPortRaw = flagString(args, 'cdp-port')
      let cdpPort: number | undefined
      if (cdpPortRaw !== undefined) {
        cdpPort = Number(cdpPortRaw)
        if (!Number.isInteger(cdpPort) || cdpPort <= 0 || cdpPort > 65535) {
          console.error(`--cdp-port 必须是 1-65535 的整数：${cdpPortRaw}`)
          return 2
        }
      }
      return runCommand({ jobFile: path.resolve(jobFile), dataDir, cdpPort })
    }
    case 'filter': {
      const educationRaw = flagString(args, 'education')
      const educations = educationRaw
        ? educationRaw.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
        : undefined
      const cdpPortRaw = flagString(args, 'cdp-port')
      let cdpPort: number | undefined
      if (cdpPortRaw !== undefined) {
        cdpPort = Number(cdpPortRaw)
        if (!Number.isInteger(cdpPort) || cdpPort <= 0 || cdpPort > 65535) {
          console.error(`--cdp-port 必须是 1-65535 的整数：${cdpPortRaw}`)
          return 2
        }
      }
      const { filterCommand } = await import('./commands/filter.js')
      return filterCommand({
        experience: flagString(args, 'experience'),
        educations,
        salary: flagString(args, 'salary'),
        cdpPort,
        probe: hasFlag(args, 'probe'),
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
      const cdpPortRaw = flagString(args, 'cdp-port')
      let cdpPort: number | undefined
      if (cdpPortRaw !== undefined) {
        cdpPort = Number(cdpPortRaw)
        if (!Number.isInteger(cdpPort) || cdpPort <= 0 || cdpPort > 65535) {
          console.error(`--cdp-port 必须是 1-65535 的整数：${cdpPortRaw}`)
          return 2
        }
      }
      const { greetCommand } = await import('./commands/greet.js')
      return greetCommand({ limit, cdpPort })
    }
    case 'review': {
      const { reviewListCommand, reviewOverrideCommand } = await import('./commands/review.js')
      if (args.positional[1] === 'override') {
        const idRaw = args.positional[2]
        const conclusionRaw = args.positional[3]
        const reason = flagString(args, 'reason')
        const evaluationId = Number(idRaw)
        if (!idRaw || !Number.isInteger(evaluationId) || evaluationId <= 0) {
          console.error(`review override 编号必须是正整数（evaluations.id）：${idRaw ?? '(缺失)'}`)
          return 2
        }
        if (conclusionRaw !== 'QUALIFIED' && conclusionRaw !== 'REJECTED') {
          console.error(`review override 结论必须是 QUALIFIED 或 REJECTED：${conclusionRaw ?? '(缺失)'}`)
          return 2
        }
        if (!reason || !reason.trim()) {
          console.error('review override 缺少 --reason "改判理由"')
          return 2
        }
        return reviewOverrideCommand({
          dataDir,
          evaluationId,
          conclusion: conclusionRaw as Conclusion,
          reason,
        })
      }
      return reviewListCommand({ dataDir, exportsDir, all: hasFlag(args, 'all'), open: hasFlag(args, 'open') })
    }
    case 'export': {
      const formatRaw = flagString(args, 'format') ?? 'csv'
      if (formatRaw !== 'csv' && formatRaw !== 'json') {
        console.error(`export --format 只支持 csv|json：${formatRaw}`)
        return 2
      }
      const { exportCommand } = await import('./commands/export.js')
      return exportCommand({ dataDir, format: formatRaw, out: flagString(args, 'out'), exportsDir })
    }
    case 'audit': {
      const limitRaw = flagString(args, 'limit')
      const limit = limitRaw ? Number(limitRaw) : 20
      if (!Number.isInteger(limit) || limit <= 0) {
        console.error(`audit --limit 必须是正整数：${limitRaw ?? ''}`)
        return 2
      }
      const { auditCommand } = await import('./commands/audit.js')
      return auditCommand({ dataDir, limit })
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
