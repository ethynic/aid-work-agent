/**
 * CLI 子命令 accept：沟通页批量「同意」接收附件简历。
 *
 * 用法：
 *   node dist/src/cli/index.js accept [--limit N]
 *
 * 找到左列最后一条消息为「对方想发送（加密）附件简历给您，您是否同意」的会话，
 * 逐个打开并点击底部处理条的「同意」。不在沟通页时自动先跳转。
 * 真实写动作 + Win32 真实鼠标：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { PageNavigator } from '../../main/boss/PageNavigator.js'
import { ResumeConsentExecutor } from '../../main/boss/ResumeConsentExecutor.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

export interface AcceptCommandOptions {
  /** 上限，默认 20，最大 100（CLI 层校验） */
  limit?: number
  /** 同意后接着点开附件简历预览再关闭（默认开启，--no-preview 关闭） */
  preview?: boolean
  cdpPort?: number
}

export async function acceptCommand(opts: AcceptCommandOptions): Promise<number> {
  const endpoint = `http://127.0.0.1:${opts.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()
  console.log(`同意接收附件简历：逐个打开「对方想发送附件简历」的会话并点「同意」（上限 ${opts.limit ?? 20} 个）。`)
  console.log('⚠️ 这是真实写动作，且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
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

    // 不在沟通页时先跳过去（复用 goto 能力）
    const url = await getUrl()
    if (!url.includes('/web/chat/index')) {
      console.log('当前不在沟通页，先点击左侧菜单跳转…')
      const navigator = new PageNavigator({ snapshot, click, getUrl })
      await navigator.navigate('chat')
    }

    const executor = new ResumeConsentExecutor({
      snapshot,
      click,
      // 左列会话列表区域中心滚动（浏览类操作走 CDP，不占用真实鼠标）
      scroll: async (deltaY) => {
        await gw.dispatchMouse({ type: 'mouseWheel', x: 550, y: 900, deltaX: 0, deltaY })
      },
      pressEscape: async () => {
        await gw.dispatchKey({ type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
        await gw.dispatchKey({ type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
      },
    })
    const result = await executor.acceptAll({ limit: opts.limit, preview: opts.preview })
    console.log(
      `✅ 完成：同意接收 ${result.accepted} 人的附件简历` +
        (opts.preview ? `，已预览 ${result.previewed} 份` : '') +
        `（${result.reachedEnd ? '已滚到底' : '达到上限'}）`,
    )
    return 0
  } finally {
    await gw.close().catch(() => {})
  }
}
