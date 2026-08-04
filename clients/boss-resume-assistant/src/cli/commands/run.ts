/**
 * CLI 子命令 run：加载岗位配置 → attach 用户日常 Chrome → 登录确认 → 跑筛选会话。
 * Chrome 接入方式：用户自己用日常 Chrome 加 --remote-debugging-port 启动（spawn 临时
 * profile 扫码登录会触发 BOSS 风控封号，已废弃），CLI 只 attach，绝不 kill 用户 Chrome。
 * 登录门禁保持：回车确认前绝不建立 CDP WebSocket（设计 §5.2 / §16 决策 6）。
 * 运行期 stdin 命令：p=暂停 r=恢复 s=停止 q=退出（退出走 INTERRUPTED 清扫 + 断开 CDP）。
 */
import readline from 'node:readline'
import { getClient } from '../../../db/client.js'
import { JobStore } from '../../main/storage/jobStore.js'
import type { SessionJobConfig, SessionEvent } from '../../main/workflow/ScreeningSession.js'
import { diagnoseAttachError, chromeLaunchCommandHint, DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { loadJobConfigFile, type JobFileConfig } from '../jobConfig.js'
import { renderEventLine, renderStatsLine } from '../progress.js'
import { parseStdinCommand, STDIN_USAGE } from '../stdinCommands.js'
import * as rt from '../cliRuntime.js'

/** 配置文件 → 落库岗位（同名更新，否则新建），返回编排层岗位配置 */
function upsertJob(config: JobFileConfig): SessionJobConfig {
  const store = new JobStore(getClient())
  const input = {
    name: config.name,
    hardRules: Object.keys(config.hardRules).length ? JSON.stringify(config.hardRules) : null,
    // JobStore 要求 preferences 为 JSON 文本（GUI 存 JSON）；CLI 配置是多行自由文本，序列化为 JSON 字符串标量
    preferences: config.preferences ? JSON.stringify(config.preferences) : null,
    actionLimitSession: config.actionLimitSession,
    actionLimitDay: config.actionLimitDay,
  }
  const existing = store.list().find((j) => j.name === config.name)
  const record = existing ? store.update(existing.id, input) : store.create(input)
  return {
    id: record.id,
    name: record.name,
    hardRules: record.hardRules,
    knowledgeVersion: record.knowledgeVersion,
    ruleVersion: record.ruleVersion,
    actionLimitSession: record.actionLimitSession,
    actionLimitDay: record.actionLimitDay,
  }
}

/** readline 等一次回车（或输入 q 返回 false） */
function waitEnter(rl: readline.Interface, prompt: string): Promise<boolean> {
  return new Promise((resolve) => {
    rl.question(prompt, (answer) => {
      resolve(answer.trim().toLowerCase() !== 'q')
    })
  })
}

export async function runCommand(opts: { jobFile: string; dataDir: string; cdpPort?: number }): Promise<number> {
  const cdpPort = opts.cdpPort ?? DEFAULT_CDP_PORT
  // 配置校验 fail-loud（在启动任何资源之前）
  const config = loadJobConfigFile(opts.jobFile)
  console.log(`岗位配置已加载：${config.name}`)

  rt.initCliRuntime({ dataDir: opts.dataDir })

  // 会话事件 → 终端进度行；候选人结论后附统计行
  rt.setEventSink((e: SessionEvent) => {
    console.log(renderEventLine(e))
    if (e.type === 'candidate' || e.state === 'PAUSED' || e.state === 'COMPLETED' || e.state === 'STOPPED') {
      console.log(renderStatsLine(rt.sessionStatus().stats))
    }
  })

  const job = upsertJob(config)
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout })

  // 强制退出（q / Ctrl+C 共用）。必须 cleanup 后 process.exit，不能走正常返回：
  // runLoop 可能还在候选人中途（OCR 可达数十秒），若只关 DB 后等事件循环排空，
  // 在途循环会继续写已关闭的 DB → unhandled rejection 崩溃退出码非零。
  // 在途会话由 shutdownCliRuntime 统一记 INTERRUPTED（与 GUI will-quit 同语义）。
  let quitting = false
  const quitNow = (code: number): void => {
    if (quitting) return
    quitting = true
    console.log('退出中：在途会话记 INTERRUPTED，断开 CDP 连接（不会关闭你的 Chrome）…')
    void (async () => {
      try {
        rl.close()
        await rt.shutdownCliRuntime()
      } finally {
        process.exit(code)
      }
    })()
  }
  // Ctrl+C：无监听时进程立即终止，finally 不执行 → CDP 连接与在途会话未清扫
  process.on('SIGINT', () => quitNow(130))

  try {
    // attach 引导：必须使用你日常登录 BOSS 的 Chrome（临时 profile 扫码登录会触发风控封号）
    console.log('请按以下步骤准备 Chrome（必须使用你日常登录 BOSS 直聘的 Chrome）：')
    console.log('  1. 关闭所有 Chrome 窗口')
    console.log('  2. 带调试端口重新启动 Chrome（命令示例，按实际安装路径调整）：')
    console.log(`     ${chromeLaunchCommandHint(cdpPort)}`)
    console.log('  3. 在打开的 Chrome 中确认已登录 BOSS 直聘，并打开「推荐」页')

    // attach + 登录确认门禁：回车确认 → 探测端点 → 连 CDP；失败可回车重试，q 放弃
    for (;;) {
      const proceed = await waitEnter(rl, `完成上述步骤后按回车连接 Chrome（端口 ${cdpPort}，输入 q 退出）: `)
      if (!proceed) return 0
      try {
        const info = await rt.attachChrome({ port: cdpPort })
        console.log(`已连接 Chrome（${info.browser || '版本未知'}，调试端口=${cdpPort}）`)
      } catch (e) {
        console.error(diagnoseAttachError(e, `http://127.0.0.1:${cdpPort}`))
        continue
      }
      const status = await rt.confirmLogin()
      if (status.state !== 'PAUSED') break
      console.error(`连接失败：${status.pauseReason ?? '未知原因'}，可修正后重试`)
    }

    rt.startSession(job)
    console.log(`任务已开始（岗位 #${job.id} ${job.name}）。${STDIN_USAGE}`)

    // 运行期命令循环：直到会话到达终态或用户退出
    const exitCode = await new Promise<number>((resolve) => {
      let settled = false
      // 终态检测：轮询状态（事件渲染走 setEventSink，这里只负责收尾）
      const timer = setInterval(() => {
        const state = rt.sessionStatus().state
        if (state === 'STOPPED' || state === 'COMPLETED') {
          console.log(`任务${state === 'COMPLETED' ? '完成' : '已停止'}。`)
          finish(0)
        }
      }, 500)
      const finish = (code: number) => {
        if (!settled) {
          settled = true
          clearInterval(timer)
          resolve(code)
        }
      }

      rl.on('line', (line) => {
        const cmd = parseStdinCommand(line)
        if (!cmd) {
          if (line.trim()) console.log(STDIN_USAGE)
          return
        }
        try {
          if (cmd === 'pause') rt.pauseSession()
          else if (cmd === 'resume') rt.resumeSession()
          else if (cmd === 'stop') rt.stopSession()
          else if (cmd === 'quit') {
            quitNow(0)
          }
        } catch (e) {
          console.error(`命令执行失败：${e instanceof Error ? e.message : String(e)}`)
        }
      })
      rl.on('close', () => finish(0))
    })
    return exitCode
  } finally {
    rl.close()
    await rt.shutdownCliRuntime()
  }
}
