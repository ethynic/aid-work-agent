/**
 * CLI 子命令 filter：自动设置推荐牛人页筛选面板（Phase 10，设计 §10.3 / §16 决策 8）。
 *
 * 用法：
 *   node dist/src/cli/index.js filter --experience 5-10年 --education 本科,硕士,博士 --salary 10-20K
 *
 * 筛选类控件被 BOSS 风控拦截 CDP 合成点击，全部走 Win32 真实鼠标通道：
 * DOMSnapshot 结构化定位坐标 → win-click.ps1（GetWindowRect 校准 + WindowFromPoint 守卫）。
 * 执行期间借用真实光标，用户手必须离开鼠标、BOSS 窗口不要被遮挡。
 *
 * 与 run 的区别：本命令只做筛选设置，不建会话、不写 DB、不做登录回车门禁
 * （用户显式发起的即时操作），完成后立即断开 CDP。
 */
import { CdpGateway } from '../../main/cdp/CdpGateway.js'
import { DEFAULT_CDP_PORT } from '../../main/chrome/ChromeAttacher.js'
import { FilterSetter, type FilterSpec } from '../../main/boss/FilterSetter.js'
import { WinMouseClicker } from '../../main/input/WinMouseClicker.js'
import type { DomSnapshot } from '../../main/boss/domSnapshot.js'

export interface FilterCommandOptions {
  experience?: string
  educations?: string[]
  salary?: string
  cdpPort?: number
  /** 探针模式：只打印面板各行选项及坐标，不点击任何选项/确定 */
  probe?: boolean
  /** 清除模式：开面板 → 清除 → 确定，清空全部筛选条件 */
  clear?: boolean
}

export async function filterCommand(opts: FilterCommandOptions): Promise<number> {
  const endpoint = `http://127.0.0.1:${opts.cdpPort ?? DEFAULT_CDP_PORT}`
  const gw = new CdpGateway({ timeoutMs: 10000 })
  const clicker = new WinMouseClicker()

  if (opts.clear) {
    console.log('清除筛选：打开面板 → 清除 → 确定。')
    console.log('⚠️ 即将通过 Win32 真实鼠标操作筛选面板：请勿移动鼠标，勿遮挡 BOSS 窗口。')
    try {
      await gw.connect(endpoint)
      await gw.attachToRecommendPage()
      console.log(`已 attach BOSS 页面（${endpoint}）`)
      const setter = new FilterSetter({
        snapshot: async () => (await gw.captureDomSnapshot()) as DomSnapshot,
        click: (point, viewport) => clicker.click(point, viewport),
      })
      await setter.clear()
      console.log('✅ 筛选已清除（徽章无计数）')
      return 0
    } finally {
      await gw.close().catch(() => {})
    }
  }
  if (opts.probe) {
    console.log('探针模式：只读取面板结构并打印选项坐标，不改动任何筛选。')
    console.log('⚠️ 若面板未打开会点一次「筛选」按钮：请勿移动鼠标，勿遮挡 BOSS 窗口。')
    try {
      await gw.connect(endpoint)
      await gw.attachToRecommendPage()
      console.log(`已 attach BOSS 页面（${endpoint}）`)
      const setter = new FilterSetter({
        snapshot: async () => (await gw.captureDomSnapshot()) as DomSnapshot,
        click: (point, viewport) => clicker.click(point, viewport),
      })
      const snap = await setter.ensurePanelOpen()
      const rows = setter.describePanel(snap)
      if (rows.length === 0) {
        console.log('未识别到任何支持的筛选行（经验要求/学历要求/薪资待遇）')
        return 1
      }
      for (const row of rows) {
        console.log(`【${row.label}】`)
        for (const o of row.options) {
          console.log(`  ${o.text}  (${Math.round(o.point.x)}, ${Math.round(o.point.y)})`)
        }
      }
      return 0
    } finally {
      await gw.close().catch(() => {})
    }
  }

  const spec: FilterSpec = {
    experience: opts.experience,
    educations: opts.educations,
    salary: opts.salary,
  }
  if (!spec.experience && !(spec.educations && spec.educations.length > 0) && !spec.salary) {
    console.error('filter 至少需要一个条件：--experience / --education / --salary（或 --probe 只读面板结构）')
    return 2
  }

  console.log('筛选条件:', JSON.stringify(spec))
  console.log('⚠️ 即将通过 Win32 真实鼠标操作筛选面板：请勿移动鼠标，勿遮挡 BOSS 窗口。')

  try {
    await gw.connect(endpoint)
    await gw.attachToRecommendPage()
    console.log(`已 attach BOSS 页面（${endpoint}）`)

    const setter = new FilterSetter({
      snapshot: async () => (await gw.captureDomSnapshot()) as DomSnapshot,
      click: (point, viewport) => clicker.click(point, viewport),
    })
    const result = await setter.apply(spec)
    console.log(`✅ 筛选已应用并校验通过：筛选·${result.filterCount}`)
    return 0
  } finally {
    await gw.close().catch(() => {})
  }
}
