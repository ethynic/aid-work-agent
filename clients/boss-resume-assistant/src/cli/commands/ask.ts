/**
 * CLI 子命令 ask：自然语言驱动的一站式演示流程。
 *
 * 用法：
 *   node dist/src/cli/index.js ask "筛选简历：本科以上学历，5年工作经验，月薪15000到20000"
 *
 * 流程：attach → 自动跳「推荐牛人」→ probe 筛选面板合法选项 → LLM 翻译成筛选条件
 * → 设置筛选（徽章校验）→ 给最近一个可打招呼的人打招呼（--limit 调整，默认 1）。
 * 全程 Win32 真实鼠标：期间请勿移动鼠标，勿遮挡 BOSS 窗口。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { PageNavigator } from '../../main/boss/PageNavigator.js'
import { FilterSetter, viewportOf } from '../../main/boss/FilterSetter.js'
import { GreetExecutor } from '../../main/boss/GreetExecutor.js'
import { translateFilterRequest } from '../../main/boss/NlFilterTranslator.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import { tryLoadEnv } from '../envLoader.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

export interface AskCommandOptions {
  request: string
  /** 打招呼人数，默认 1（演示场景：最近一个可打招呼的人） */
  limit?: number
  cdpPort?: number
}

export async function askCommand(opts: AskCommandOptions): Promise<number> {
  tryLoadEnv()
  const endpoint = `http://127.0.0.1:${opts.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()
  console.log(`收到要求：「${opts.request}」`)
  console.log('⚠️ 将执行真实写动作（设置筛选 + 打招呼）并借用真实鼠标：请勿移动鼠标，勿遮挡 BOSS 窗口。')
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

    // 1. 确保在推荐牛人页
    if (!(await getUrl()).includes('/web/chat/recommend')) {
      console.log('① 当前不在推荐牛人页，点击左侧菜单跳转…')
      const navigator = new PageNavigator({ snapshot, click, getUrl })
      await navigator.navigate('recommend')
      console.log('   已跳转')
    }

    // 2. probe 面板合法选项 → LLM 翻译
    const setter = new FilterSetter({ snapshot, click })
    await setter.ensurePanelOpen()
    const panel = setter.describePanel(await snapshot())
    console.log(`② 筛选面板合法选项：${panel.map((r) => `${r.label}(${r.options.length}项)`).join(' ')}`)
    const spec = await translateFilterRequest(opts.request, panel)
    console.log(
      `③ LLM 翻译结果：经验=${spec.experience ?? '不限'} 学历=${spec.educations?.join('/') ?? '不限'} 薪资=${spec.salary ?? '不限'}`,
    )

    // 3. 应用筛选（apply 内含徽章计数校验）
    const { filterCount } = await setter.apply(spec)
    console.log(`④ 筛选已生效（徽章 筛选·${filterCount}）`)

    // 4. 给最近可打招呼的人打招呼（滚动点取实际视口中心，硬编码坐标在小窗口会误判到底）
    const executor = new GreetExecutor({
      snapshot,
      click,
      scroll: async (deltaY) => {
        const vp = viewportOf(await snapshot())
        await gw.dispatchMouse({
          type: 'mouseWheel',
          x: Math.floor(vp.width / 2),
          y: Math.floor(vp.height / 2),
          deltaX: 0,
          deltaY,
        })
      },
    })
    const result = await executor.greetVisible({ limit: opts.limit ?? 1 })
    console.log(`⑤ ✅ 完成：成功打招呼 ${result.greeted} 人（${result.reachedEnd ? '已滚到底' : '达到上限'}）`)
    return 0
  } finally {
    await gw.close().catch(() => {})
  }
}
