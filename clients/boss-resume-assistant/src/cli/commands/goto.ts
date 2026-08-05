/**
 * CLI 子命令 goto：点击左侧导航菜单跳转页面（推荐牛人 / 沟通）。
 *
 * 用法：
 *   node dist/src/cli/index.js goto recommend   # 跳到「推荐牛人」
 *   node dist/src/cli/index.js goto chat        # 跳到「沟通」（看打招呼回复）
 *
 * 走 Win32 真实鼠标通道，执行期间请勿移动鼠标。已在目标页时跳过（幂等）。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { PageNavigator, type NavTarget } from '../../main/boss/PageNavigator.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

const ALIASES: Record<string, NavTarget> = {
  recommend: 'recommend',
  推荐牛人: 'recommend',
  chat: 'chat',
  沟通: 'chat',
}

export interface GotoCommandOptions {
  target: string
  cdpPort?: number
}

export async function gotoCommand(opts: GotoCommandOptions): Promise<number> {
  const target = ALIASES[opts.target]
  if (!target) {
    console.error(`❌ 未知目标页面「${opts.target}」，支持：recommend（推荐牛人）/ chat（沟通）`)
    return 2
  }
  const endpoint = `http://127.0.0.1:${opts.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()
  console.log(`页面跳转：点击左侧菜单「${target === 'recommend' ? '推荐牛人' : '沟通'}」。请勿移动鼠标。`)
  try {
    await gw.connect(endpoint)
    await gw.attachToRecommendPage()
    const getUrl = async () => {
      const targets = await gw.getTargets()
      return targets.find((t) => t.type === 'page' && t.url.includes('zhipin.com'))?.url ?? ''
    }
    const navigator = new PageNavigator({
      snapshot: async () => (await gw.captureDomSnapshot()) as DomSnapshot,
      click: (point, viewport) => clicker.click(point, viewport),
      getUrl,
    })
    const result = await navigator.navigate(target)
    console.log(result.clicked ? '✅ 已跳转' : '✅ 已在目标页面，无需跳转')
    return 0
  } finally {
    await gw.close().catch(() => {})
  }
}
