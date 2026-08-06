/**
 * CLI 子命令 reject：沟通页把当前会话的候选人标记为「不合适」（写动作）。
 *
 * 用法：
 *   node dist/src/cli/index.js reject
 *
 * 定位当前会话右侧面板底部的「不合适」按钮并点击；弹出确认层时自动点「确定」。
 * 不在沟通页时自动先跳转。真实写动作 + Win32 真实鼠标：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { PageNavigator } from '../../main/boss/PageNavigator.js'
import { ChatRejectExecutor } from '../../main/boss/ChatRejectExecutor.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

export interface RejectCommandOptions {
  cdpPort?: number
}

export async function rejectCommand(opts: RejectCommandOptions): Promise<number> {
  const endpoint = `http://127.0.0.1:${opts.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()
  console.log('标记不合适：点击当前会话右侧面板的「不合适」按钮（仅当前会话，一次一个）。')
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

    // 不在沟通页时先跳过去（复用 goto 能力，与 accept 一致）
    const url = await getUrl()
    if (!url.includes('/web/chat/index')) {
      console.log('当前不在沟通页，先点击左侧菜单跳转…')
      const navigator = new PageNavigator({ snapshot, click, getUrl })
      await navigator.navigate('chat')
    }

    const executor = new ChatRejectExecutor({ snapshot, click })
    await executor.rejectCurrent()
    console.log('✅ 完成：已把当前会话的候选人标记为不合适')
    return 0
  } finally {
    await gw.close().catch(() => {})
  }
}
