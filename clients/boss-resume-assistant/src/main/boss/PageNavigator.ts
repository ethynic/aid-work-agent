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
 * - 点击正常返回但URL未匹配时，复用Page.navigate作为一次普通重试；仍不匹配保留明确负后验。
 * - 定位或输入直接异常不再追加导航，交Runner共用视觉恢复；已有目标页保持幂等。
 */
import {
  type DomSnapshot,
  type ClickPoint,
  findNodesByString,
  accumulateOwnerOffset,
  boundsCenter,
} from './domSnapshot.js'
import { viewportOf } from './FilterSetter.js'
import { CancelledError, CodedOperationError } from '../operations/types.js'
import { PagePostconditionError } from '../operations/pageOperation.js'

export class NavError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'NavError'
  }
}

export type NavTarget = 'recommend' | 'chat'

/** 一次跳转的完成方式：already=已在目标页（幂等跳过）；menu=点击左侧菜单生效；url-jump=直跳兜底生效 */
export type NavVia = 'already' | 'menu' | 'url-jump'

export interface NavResult {
  clicked: boolean
  via: NavVia
}

const TARGETS: Record<NavTarget, { menuTexts: readonly [string, ...string[]]; urlPattern: string }> = {
  recommend: { menuTexts: ['推荐牛人'], urlPattern: '/web/chat/recommend' },
  chat: { menuTexts: ['消息', '沟通'], urlPattern: '/web/chat/index' },
}

export function isBossPage(url: string, target: NavTarget): boolean {
  try {
    const page = new URL(url)
    return page.protocol === 'https:' && ['www.zhipin.com', 'zhipin.com'].includes(page.hostname)
      && page.pathname === TARGETS[target].urlPattern
  } catch { return false }
}

export function assertNotBossLoginPage(url: string): void {
  let page: URL
  try { page = new URL(url) } catch { return }
  if (page.protocol !== 'https:') return
  const route = page.pathname.replace(/\/$/, '')
  const isLogin = (page.hostname === 'passport.zhipin.com' && route === '/login')
    || (['www.zhipin.com', 'zhipin.com'].includes(page.hostname)
      && (route === '/login' || route === '/web/user/login'
        || (route === '/web/user' && page.searchParams.get('ka') === 'header-login')))
  if (isLogin) throw new CodedOperationError('NOT_LOGGED_IN', 'BOSS 页面要求登录，请在原浏览器完成 BOSS 登录')
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
  /** 菜单点击正常返回但URL未生效时的现有直跳能力，消费同一次普通重试预算。 */
  pageNavigate?(url: string): Promise<void>
  /** 协作式取消信号：入口检查一次，触发即抛 CancelledError */
  signal?: AbortSignal
  sleep?(ms: number): Promise<void>
  /** Existing URL fallback consumes the command's single ordinary retry. */
  onRetry?(): void
}

export class PageNavigator {
  private readonly sleep: (ms: number) => Promise<void>

  constructor(private readonly deps: NavDeps) {
    this.sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)))
  }

  /**
   * 跳转到目标页；已在目标页则跳过。返回是否发生跳转及完成方式。
   *
   * 菜单点击正常返回而URL未达目标才走直跳；直接异常留给共用恢复。
   */
  async navigate(target: NavTarget): Promise<NavResult> {
    if (this.deps.signal?.aborted) throw new CancelledError()
    const spec = TARGETS[target]
    const [menuText] = spec.menuTexts
    const urlBefore = await this.deps.getUrl()
    assertNotBossLoginPage(urlBefore)
    if (isBossPage(urlBefore, target)) {
      return { clicked: false, via: 'already' }
    }

    const snap = await this.deps.snapshot()
    const point = this.locateMenuItem(snap, spec.menuTexts)
    await this.deps.click(point, viewportOf(snap))
    await this.sleep(1500)
    let url = await this.deps.getUrl()
    assertNotBossLoginPage(url)
    if (isBossPage(url, target)) {
      return { clicked: true, via: 'menu' }
    }

    // 直跳兜底：登录态有效时目标 URL 即目标页，不依赖菜单可点、不限当前所在页面
    if (this.deps.pageNavigate) {
      if (this.deps.signal?.aborted) throw new CancelledError()
      this.deps.onRetry?.()
      await this.deps.pageNavigate(`https://www.zhipin.com${spec.urlPattern}`)
      await this.sleep(1500)
      url = await this.deps.getUrl()
      assertNotBossLoginPage(url)
      if (isBossPage(url, target)) {
        return { clicked: true, via: 'url-jump' }
      }
      throw new PagePostconditionError(
        `点击菜单与直跳 ${spec.urlPattern} 后都未到达「${menuText}」页（当前 URL: ${url}），请检查页面状态`,
        { status: 'unmet', evidence: [`直跳后可读URL为${url}，仍非${spec.urlPattern}`] },
      )
    }

    // 无导航能力（旧测试替身/精简会话）：保持原 fail-loud 语义
    throw new PagePostconditionError(
      `点击左侧菜单「${menuText}」后页面未跳转（当前 URL: ${url}）：` +
        '点击可能被拦截或页面结构已变，请人工查看',
      { status: 'unmet', evidence: [`点击后可读URL为${url}，仍非${spec.urlPattern}`] },
    )
  }

  /** 左侧菜单项唯一定位：仅主文档、精确文案、侧栏候选区内、视口内，取 x 最小命中 */
  private locateMenuItem(snap: DomSnapshot, menuTexts: readonly string[]): ClickPoint {
    const viewport = viewportOf(snap)
    const doc = snap.documents[0]!
    for (const menuText of menuTexts) {
      const stringIndex = snap.strings.findIndex((s) => s.trim() === menuText)
      if (stringIndex < 0) continue
      const hits: ClickPoint[] = []
      for (const { bounds } of findNodesByString(doc, stringIndex)) {
        if (bounds[2] <= 0 || bounds[3] <= 0) continue
        const c = boundsCenter(bounds)
        const x = c.x - (doc.scrollOffsetX ?? 0)
        const y = c.y - (doc.scrollOffsetY ?? 0)
        if (x <= 0 || x >= viewport.width * SIDEBAR_RELATIVE_MAX || y <= 0 || y >= viewport.height) continue
        hits.push({ x, y })
      }
      if (hits.length === 0) continue
      // 真菜单在侧栏最左，排除相同文案的顶部命中。
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
    throw new NavError(`左侧导航栏中找不到菜单文案 ${menuTexts.map((text) => `「${text}」`).join('或')} 的可点击位置，页面布局可能已变`)
  }
}
