/**
 * 页面导航器（CLI goto 子命令）。
 *
 * 点击 BOSS 左侧导航菜单跳转页面：推荐牛人 / 沟通。
 * 典型场景：页面不在推荐牛人时先跳过去再 filter/greet；打完招呼后跳到沟通看回复。
 *
 * 定位规则（真机 2026-08-05 校准）：
 * - 左侧菜单在主文档 doc0，菜单项精确文案命中，屏幕 x < 200 为侧栏区
 *   （页面顶部有同文案诱饵，如「沟通」x=303、「推荐牛人」x=324）
 * - 跳转结果用 target URL 校验（推荐牛人=/web/chat/recommend，沟通=/web/chat/index），
 *   不用页面文本——沟通页 DOM 仍挂载推荐 iframe（含「筛选」「打招呼」），文本判定会骗过（§17 坑 17）
 * - 已在目标页时跳过点击（幂等，避免打扰列表滚动位置）
 * - 幂等兜底（2026-09-01）：点击后 URL 未变且当前已在 /web/chat 区时，用 CDP
 *   Page.navigate 页内直跳目标页再校验（SPA 菜单点击在沟通模块内不触发路由变化）
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError } from '../operations/types.js'

export class NavError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'NavError'
  }
}

export type NavTarget = 'recommend' | 'chat'

const TARGETS: Record<NavTarget, { menuText: string; urlPattern: string; label: string }> = {
  recommend: { menuText: '推荐牛人', urlPattern: '/web/chat/recommend', label: '推荐牛人' },
  chat: { menuText: '沟通', urlPattern: '/web/chat/index', label: '沟通' },
}

/**
 * 侧栏候选区相对上限：命中 x < 视口宽×35%（2026-09-01 相对化修订：旧规则绝对像素
 * x<200 按 1278 宽窗口校准——菜单 cx≈93~114、顶部诱饵 cx≥303；窗口更宽/页面居中时
 * 侧栏整体右移，绝对阈值必然失配，goto 报「侧栏无命中」）。过滤后取 x 最小者：
 * 左侧栏是整页最左区域，真菜单恒为最小 x 命中；顶部诱饵在其右侧（Δx≈200+）被排除。
 */
const SIDEBAR_RELATIVE_MAX = 0.35

export interface NavDeps {
  snapshot(): Promise<DomSnapshot>
  click(point: ClickPoint, viewport: { width: number; height: number }): Promise<void>
  /** 当前 BOSS 标签页 URL（Target.getTargets 实时取） */
  getUrl(): Promise<string>
  /** CDP 页内导航（Page.navigate）兜底，可选。点击菜单未触发 SPA 路由变化且当前已在
   *  /web/chat 区（如推荐页点「沟通」停在 /web/chat/recommend）时，直跳目标页再校验；
   *  缺省（旧测试替身/精简会话）保持原 fail-loud 报错 */
  pageNavigate?(url: string): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  sleep?(ms: number): Promise<void>
}

export class PageNavigator {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: NavDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /** 跳转到目标页；已在目标页则跳过。返回是否发生了点击跳转 */
  async navigate(target: NavTarget): Promise<{ clicked: boolean }> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const spec = TARGETS[target]
    const urlBefore = await this.deps.getUrl()
    if (urlBefore.includes(spec.urlPattern)) {
      return { clicked: false }
    }

    const snap = await this.deps.snapshot()
    const point = this.locateMenuItem(snap, spec.menuText)
    await this.deps.click(point, viewportOf(snap))
    await this.sleep(1500)

    let urlAfter = await this.deps.getUrl()
    if (!urlAfter.includes(spec.urlPattern)) {
      // 幂等兜底（2026-09-01）：已在沟通模块内点菜单不触发路由变化（如停在 /web/chat/recommend
      // 点「沟通」URL 仍不变，真实页面其实已可操作）。此时若当前以 /web/chat 开头且会话提供
      // 页内导航能力，用 CDP Page.navigate 直跳目标页再校验（不用鼠标键盘，绕开反作弊拦截）；
      // 不在 /web/chat 区（如职位详情页）或无导航能力时保持原 fail-loud 报错
      if (this.deps.pageNavigate && urlAfter.includes('/web/chat')) {
        await this.deps.pageNavigate(`https://www.zhipin.com${spec.urlPattern}`)
        await this.sleep(1500)
        urlAfter = await this.deps.getUrl()
      }
      if (!urlAfter.includes(spec.urlPattern)) {
        throw new NavError(
          `点击左侧菜单「${spec.menuText}」后页面未跳转（当前 URL: ${urlAfter}）：` +
            '点击可能被拦截或页面结构已变，请人工查看',
        )
      }
    }
    return { clicked: true }
  }

  /** 左侧菜单项唯一定位：仅主文档、精确文案、侧栏候选区内、视口内，取 x 最小命中 */
  private locateMenuItem(snap: DomSnapshot, menuText: string): ClickPoint {
    const viewport = viewportOf(snap)
    const stringIndex = snap.strings.findIndex((s) => s.trim() === menuText)
    if (stringIndex < 0) {
      throw new NavError(`页面上找不到菜单文案「${menuText}」（不在 DOM 文本表中），请确认已登录 BOSS`)
    }
    const hits: ClickPoint[] = []
    for (const { bounds } of findNodesByString(snap.documents[0]!, stringIndex)) {
      if (bounds[2] <= 0 || bounds[3] <= 0) continue
      const c = boundsCenter(bounds)
      const x = c.x - (snap.documents[0]!.scrollOffsetX ?? 0)
      const y = c.y - (snap.documents[0]!.scrollOffsetY ?? 0)
      if (x <= 0 || x >= viewport.width * SIDEBAR_RELATIVE_MAX || y <= 0 || y >= viewport.height) continue
      hits.push({ x, y })
    }
    if (hits.length === 0) {
      throw new NavError(
        `左侧导航栏中找不到「${menuText}」菜单项（视口左 35% 区域无命中），页面布局可能已变`,
      )
    }
    // 取 x 最小命中：真菜单在侧栏最左，顶部/其他区域同文案诱饵恒在其右侧被排除
    hits.sort((a, b) => a.x - b.x)
    const best = hits[0]!
    const ambiguous = hits.filter((p) => p.x - best.x < 5)
    if (ambiguous.length > 1) {
      throw new NavError(
        `左侧导航栏中「${menuText}」最左命中不唯一（${ambiguous.map((p) => `(${Math.round(p.x)},${Math.round(p.y)})`).join(' ')}），无法确定，已停止`,
      )
    }
    return best
  }
}
