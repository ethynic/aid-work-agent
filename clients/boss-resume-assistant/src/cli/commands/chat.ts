/**
 * CLI 子命令 chat：交互式对话模式（演示形态）。
 *
 * 用法：
 *   node dist/src/cli/index.js chat
 *
 * 启动后进入 REPL：直接用中文提要求（「本科以上，5年经验，月薪15-20K，筛选简历」
 * 「再打一个」「看看谁给我发简历了」），LLM 分类意图 → 自动操作 BOSS 页面 → 中文回答。
 * 输入「退出」/exit/q 结束。真实写动作 + Win32 真实鼠标：操作期间请勿移动鼠标。
 */
import readline from 'node:readline'
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { PageNavigator } from '../../main/boss/PageNavigator.js'
import { FilterSetter, viewportOf } from '../../main/boss/FilterSetter.js'
import { GreetExecutor } from '../../main/boss/GreetExecutor.js'
import { ResumeConsentExecutor } from '../../main/boss/ResumeConsentExecutor.js'
import { ChatRejectExecutor } from '../../main/boss/ChatRejectExecutor.js'
import { InterviewDemoExecutor } from '../../main/boss/InterviewDemoExecutor.js'
import { translateFilterRequest } from '../../main/boss/NlFilterTranslator.js'
import { ChatOrchestrator, CAPABILITY_HINT, type ChatHandlers } from '../../main/boss/ChatOrchestrator.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import { tryLoadEnv } from '../envLoader.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

export interface ChatCommandOptions {
  cdpPort?: number
}

const EXIT_WORDS = new Set(['退出', 'exit', 'quit', 'q'])

export async function chatCommand(opts: ChatCommandOptions): Promise<number> {
  tryLoadEnv()
  const endpoint = `http://127.0.0.1:${opts.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()
  try {
    await gw.connect(endpoint)
    await gw.attachToRecommendPage()
    const getUrl = async () => {
      const targets = await gw.getTargets()
      return targets.find((t) => t.type === 'page' && t.url.includes('zhipin.com'))?.url ?? ''
    }
    const snapshot = async () => (await gw.captureDomSnapshot()) as DomSnapshot
    const click = (point: { x: number; y: number }, viewport: { width: number; height: number }) =>
      clicker.click(point, viewport)
    const navigator = new PageNavigator({ snapshot, click, getUrl })
    const setter = new FilterSetter({ snapshot, click })

    // 滚动点取实际视口中心（同 greet.ts：硬编码 960/950 在小窗口会误判到底）
    const recommendScroll = async (deltaY: number) => {
      const vp = viewportOf(await snapshot())
      await gw.dispatchMouse({
        type: 'mouseWheel',
        x: Math.floor(vp.width / 2),
        y: Math.floor(vp.height / 2),
        deltaX: 0,
        deltaY,
      })
    }

    const handlers: ChatHandlers = {
      gotoPage: async (target) => {
        const pattern = target === 'recommend' ? '/web/chat/recommend' : '/web/chat/index'
        if (!(await getUrl()).includes(pattern)) await navigator.navigate(target)
      },
      probePanel: async () => {
        await setter.ensurePanelOpen()
        return setter.describePanel(await snapshot())
      },
      translateFilter: (request, panel) => translateFilterRequest(request, panel),
      applyFilter: (spec) => setter.apply(spec),
      greet: (limit) =>
        new GreetExecutor({ snapshot, click, scroll: recommendScroll }).greetVisible({ limit }),
      acceptResumes: async () => {
        const executor = new ResumeConsentExecutor({
          snapshot,
          click,
          scroll: async (deltaY) => {
            await gw.dispatchMouse({ type: 'mouseWheel', x: 550, y: 900, deltaX: 0, deltaY })
          },
          pressEscape: async () => {
            await gw.dispatchKey({ type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
            await gw.dispatchKey({ type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
          },
        })
        const r = await executor.acceptAll({ limit: 20, preview: true })
        return { accepted: r.accepted, previewed: r.previewed }
      },
      rejectCurrent: () => new ChatRejectExecutor({ snapshot, click }).rejectCurrent(),
      interviewDemo: () =>
        new InterviewDemoExecutor({
          snapshot,
          click,
          typeChar: async (ch) => {
            await gw.dispatchKey({ type: 'char', key: ch, text: ch })
          },
        }).run(),
      clearFilter: () => setter.clear(),
    }
    const orchestrator = new ChatOrchestrator({ handlers })

    console.log(`已连接 BOSS 页面（${endpoint}），进入对话模式。`)
    console.log(CAPABILITY_HINT)
    console.log('⚠️ 执行筛选/打招呼/同意接收时会借用真实鼠标：操作期间请勿移动鼠标，勿遮挡 BOSS 窗口。')

    const rl = readline.createInterface({ input: process.stdin, output: process.stdout, prompt: '\n你> ' })
    rl.prompt()
    return await new Promise<number>((resolve) => {
      // 输入队列 + 串行处理：管道/快速输入时 readline 会把已缓冲的行全部立即派发
      //（rl.pause 挡不住），且 stdin EOF 会让 rl 立刻触发 close——若直接在 close 里退出，
      // 进行中的操作会被 gw.close 半途中断。所以 close 只置标志，等队列排空才真正退出。
      const EXIT = Symbol('exit')
      const queue: (string | typeof EXIT)[] = []
      let busy = false
      let closed = false
      const drain = (): void => {
        if (busy) return
        const item = queue.shift()
        if (item === EXIT) {
          rl.close()
          resolve(0)
          return
        }
        if (item === undefined) {
          if (closed) resolve(0)
          else rl.prompt()
          return
        }
        busy = true
        orchestrator
          .handle(item)
          .then((lines) => {
            for (const l of lines) console.log(`助手> ${l}`)
          })
          .catch((e) => {
            // fail-loud 但 REPL 保持存活：演示中一次失败不能退出会话
            console.error(`助手> ❌ 操作失败：${e instanceof Error ? e.message : String(e)}`)
          })
          .finally(() => {
            busy = false
            drain()
          })
      }
      rl.on('line', (line) => {
        const text = line.trim()
        if (!text) {
          if (!busy && !closed) rl.prompt()
          return
        }
        queue.push(EXIT_WORDS.has(text.toLowerCase()) || EXIT_WORDS.has(text) ? EXIT : text)
        drain()
      })
      rl.on('close', () => {
        closed = true
        drain()
      })
    })
  } finally {
    await gw.close().catch(() => {})
  }
}
