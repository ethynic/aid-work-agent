/**
 * CLI 子命令 greet：逐个点击推荐列表中可见的「打招呼」按钮（写动作）。
 *
 * 用法：
 *   node dist/src/cli/index.js greet [--limit N]
 *
 * 只点当前视口内可见按钮（不滚动）；每个点击后校验按钮数减少，否则 fail-loud 停止。
 * 走 Win32 真实鼠标通道，执行期间用户手必须离开鼠标、BOSS 窗口不要被遮挡。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { GreetExecutor } from '../../main/boss/GreetExecutor.js'
import { viewportOf } from '../../main/boss/FilterSetter.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

export interface GreetCommandOptions {
  /** 上限，默认 10，最大 100（CLI 层校验） */
  limit?: number
  cdpPort?: number
}

export async function greetCommand(opts: GreetCommandOptions): Promise<number> {
  const endpoint = `http://127.0.0.1:${opts.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()
  console.log(
    `打招呼：逐个点击「打招呼」按钮（上限 ${opts.limit ?? 10} 个，当前屏点完自动向下滚动，到底结束）。`,
  )
  console.log('⚠️ 这是真实写动作（会向候选人发招呼），且借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
  try {
    await gw.connect(endpoint)
    await gw.attachToRecommendPage()
    console.log(`已 attach BOSS 页面（${endpoint}）`)
    // 前置校验：必须在推荐牛人列表页（否则 0 按钮会被误报成「已滚到底」）
    const probe = (await gw.captureDomSnapshot()) as DomSnapshot
    if (!probe.strings.some((s) => /^筛选(·\d+)?$/.test(s.trim()))) {
      console.error('❌ 当前页面不是推荐牛人列表页（未找到「筛选」按钮），请切换到「推荐牛人」页后重试')
      return 1
    }
    // 滚动点取实际视口中心（推荐列表水平居中）：不能硬编码 960/950（= 真机 1917x1905 的半宽半高），
    // 窗口矮于 950 时落点出视口，滚轮不生效会误判「已滚到底」
    const vp = viewportOf(probe)
    const scrollPoint = { x: Math.floor(vp.width / 2), y: Math.floor(vp.height / 2) }
    const executor = new GreetExecutor({
      snapshot: async () => (await gw.captureDomSnapshot()) as DomSnapshot,
      click: (point, viewport) => clicker.click(point, viewport),
      // 列表区域中心滚动（浏览类操作走 CDP，不占用真实鼠标）
      scroll: async (deltaY) => {
        await gw.dispatchMouse({ type: 'mouseWheel', x: scrollPoint.x, y: scrollPoint.y, deltaX: 0, deltaY })
      },
    })
    const result = await executor.greetVisible({ limit: opts.limit })
    console.log(`✅ 完成：成功打招呼 ${result.greeted} 人（${result.reachedEnd ? '已滚到底' : '达到上限'}）`)
    return 0
  } finally {
    await gw.close().catch(() => {})
  }
}
