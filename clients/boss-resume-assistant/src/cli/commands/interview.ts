/**
 * CLI 子命令 interview：约面试表单填充演示（只填不发送）。
 *
 * 用法：
 *   node dist/src/cli/index.js interview [--remark "备注内容"]
 *
 * 沟通页当前会话 → 点「约面试」→ 备注事项逐字输入（默认「请带好身份证和简历准时面试」）→
 * 面试时间选明天 → 点「取消」关闭。⚠️ 演示用途：绝不点击「发送」。
 * 不在沟通页时自动先跳转。Win32 真实鼠标：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { PageNavigator } from '../../main/boss/PageNavigator.js'
import { InterviewDemoExecutor } from '../../main/boss/InterviewDemoExecutor.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

export interface InterviewCommandOptions {
  remark?: string
  cdpPort?: number
}

export async function interviewCommand(opts: InterviewCommandOptions): Promise<number> {
  const endpoint = `http://127.0.0.1:${opts.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()
  console.log('约面试演示：打开当前会话的面试邀请表单 → 逐字填备注 → 选明天日期 → 取消关闭。')
  console.log('⚠️ 仅演示填充，绝不点击「发送」；借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
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

    // 不在沟通页时先跳过去（复用 goto 能力，与 accept/reject 一致）
    const url = await getUrl()
    if (!url.includes('/web/chat/index')) {
      console.log('当前不在沟通页，先点击左侧菜单跳转…')
      const navigator = new PageNavigator({ snapshot, click, getUrl })
      await navigator.navigate('chat')
    }

    const executor = new InterviewDemoExecutor({
      snapshot,
      click,
      typeChar: async (ch) => {
        await gw.dispatchKey({ type: 'char', key: ch, text: ch })
      },
    })
    const result = await executor.run({ remark: opts.remark })
    console.log(`✅ 完成：备注「${result.remark}」+ 日期 ${result.date} 已填入并取消关闭（未发送）`)
    return 0
  } finally {
    await gw.close().catch(() => {})
  }
}
