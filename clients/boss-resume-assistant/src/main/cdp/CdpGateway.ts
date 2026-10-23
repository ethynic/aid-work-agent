/**
 * CDP Gateway（设计文档 §6）。
 * - 业务层不直接接触 CdpSocket.send，只调用类型化方法
 * - 管理 browser-level 连接 + 目标 page 的 session attach
 * - 通过 /json/version 拿 webSocketDebuggerUrl，再连 browser WS
 * - Target.attachToTarget(flatten) 建立 page session，后续方法带 sessionId
 */
import { CdpSocket } from './CdpSocket.js'
import type { CdpEvent } from './CdpSocket.js'
import type { AuditWriter } from './audit.js'
import { browserInstanceId } from '../chrome/ChromeLauncher.js'
import { CdpTargetUnavailableError, CdpConnectionError } from './errors.js'

export interface CdpTargetInfo {
  targetId: string
  type: string
  url: string
  title: string
}

export interface AttachResult {
  targetId: string
  sessionId: string
}

export interface GatewayOptions {
  timeoutMs?: number
  auditWriter?: AuditWriter
  /** 可注入 socket 工厂（测试） */
  socketFactory?: (url: string) => import('./CdpSocket.js').CdpWebSocketLike
  /** 可注入 fetch（测试） */
  fetchImpl?: typeof fetch
}

export interface DomSnapshotOptions {
  /** Read-only paint ordering for callers checking whether a captured region is covered. */
  includePaintOrder?: boolean
  /** 仅需要实测 CSS 输入坐标的调用方开启；普通快照保持原采集成本。 */
  includeDOMRects?: boolean
  /** 原生快照计算样式；仅需要真实裁剪/控件结构的调用方按需请求。 */
  computedStyles?: string[]
}

export class CdpGateway {
  private socket: CdpSocket
  private readonly fetchImpl: typeof fetch
  private pageSessionId?: string
  private pageTargetId?: string
  private browserInstance?: string
  private readonly timeoutMs: number

  constructor(opts: GatewayOptions = {}) {
    this.socket = new CdpSocket({
      timeoutMs: opts.timeoutMs,
      auditWriter: opts.auditWriter,
      socketFactory: opts.socketFactory,
    })
    this.fetchImpl = opts.fetchImpl ?? fetch
    this.timeoutMs = opts.timeoutMs ?? 10000
  }

  /** 连接到 browser-level CDP。httpEndpoint 形如 http://127.0.0.1:9222 */
  async connect(httpEndpoint: string, signal?: AbortSignal): Promise<void> {
    const localSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(this.timeoutMs)]) : AbortSignal.timeout(this.timeoutMs)
    const version = await this.fetchImpl(`${httpEndpoint}/json/version`, { signal: localSignal }).then((r) => {
      if (r.ok === false) throw new Error('CDP version endpoint unavailable')
      return r.json()
    }).catch(() => { throw new CdpConnectionError('CDP version endpoint unavailable or timed out') }) as {
      webSocketDebuggerUrl: string
    }
    if (!version.webSocketDebuggerUrl) {
      throw new Error('CDP /json/version missing webSocketDebuggerUrl')
    }
    this.browserInstance = browserInstanceId(version.webSocketDebuggerUrl)
    await this.socket.connect(version.webSocketDebuggerUrl, signal)
  }

  get isConnected(): boolean {
    return this.socket.isConnected
  }

  getBrowserInstanceId(): string | undefined { return this.browserInstance }

  /** DevTools HTTP creates only a known BOSS business entry, without script injection. */
  async createBossPage(httpEndpoint: string, url: string): Promise<AttachResult> {
    const targetUrl = new URL(url)
    if (targetUrl.origin !== 'https://www.zhipin.com' || !['/web/chat/recommend', '/web/chat/index'].includes(targetUrl.pathname)
      || targetUrl.search || targetUrl.hash) throw new CdpTargetUnavailableError('Unsupported BOSS recovery entry')
    const result = await this.fetchImpl(`${httpEndpoint}/json/new?${encodeURIComponent(targetUrl.href)}`, {
      method: 'PUT', signal: AbortSignal.timeout(10000),
    })
    if (!result.ok) throw new CdpTargetUnavailableError('BOSS recovery page creation failed')
    const target = await result.json() as { id?: string }
    if (typeof target.id !== 'string' || !target.id) throw new CdpTargetUnavailableError('BOSS recovery page has no target')
    // The target was just created at the allowlisted entry. Attach even if it
    // redirects to login, so the recovery owner can report that actual state.
    return this.attachToTarget(target.id)
  }

  /** 列出所有 target */
  async getTargets(): Promise<CdpTargetInfo[]> {
    const result = await this.socket.send<{ targetInfos: CdpTargetInfo[] }>('Target.getTargets')
    return result.targetInfos ?? []
  }

  /** 找到推荐页 target 并 attach，返回 sessionId（后续方法自动带） */
  async attachToRecommendPage(): Promise<AttachResult> {
    const targets = await this.getTargets()
    const candidates = targets.flatMap(target => {
      try {
        const url = new URL(target.url)
        if (target.type !== 'page' || url.protocol !== 'https:' || !['www.zhipin.com', 'zhipin.com'].includes(url.hostname) || url.username || url.password) return []
        // Keep the previous order between business tabs (chat/recommend); a new home tab must not steal attach.
        return [{ target, priority: url.pathname.startsWith('/web/chat/') ? 0 : 1 }]
      } catch { return [] }
    })
    const page = candidates.sort((a, b) => a.priority - b.priority)[0]?.target
    if (!page) {
      throw new Error(`no BOSS page target found among ${targets.length} targets`)
    }
    return this.attachToTarget(page.targetId)
  }

  /** Recovery refs never fall back to a different tab, even if several BOSS URLs match. */
  async attachToBoundBossTarget(targetId: string, allowLogin = false): Promise<AttachResult> {
    const target = (await this.getTargets()).find(t => t.targetId === targetId)
    let legal = false
    try {
      const url = new URL(target?.url ?? '')
      legal = target?.type === 'page' && url.protocol === 'https:'
        && (['www.zhipin.com', 'zhipin.com'].includes(url.hostname) || allowLogin && url.hostname === 'passport.zhipin.com')
        && !url.port && !url.username && !url.password
    } catch { /* Refuse a closed/replaced/non-BOSS target. */ }
    if (!legal) throw new CdpTargetUnavailableError()
    return this.attachToTarget(targetId)
  }

  /** attach 到指定 target，建立 session */
  async attachToTarget(targetId: string): Promise<AttachResult> {
    const result = await this.socket.send<{ sessionId: string }>(
      'Target.attachToTarget',
      { targetId, flatten: true },
    )
    this.pageSessionId = result.sessionId
    this.pageTargetId = targetId
    return { targetId, sessionId: result.sessionId }
  }

  getSessionId(): string | undefined {
    return this.pageSessionId
  }

  getTargetId(): string | undefined { return this.pageTargetId }

  /** Browser-level activation remains available when a JS dialog blocks Page methods. */
  async activateTarget(): Promise<void> {
    if (!this.pageTargetId) throw new Error('No bound BOSS target to activate')
    await this.socket.send('Target.activateTarget', { targetId: this.pageTargetId })
  }

  async getPageMetrics(): Promise<{ width: number; height: number }> {
    const metrics = await this.socket.send<{ cssVisualViewport?: { clientWidth: number; clientHeight: number; scale: number } }>(
      'Page.getLayoutMetrics', {}, this.pageSessionId)
    const viewport = metrics.cssVisualViewport
    if (!viewport || !Number.isFinite(viewport.clientWidth) || !Number.isFinite(viewport.clientHeight)
      || viewport.clientWidth <= 0 || viewport.clientHeight <= 0 || viewport.scale !== 1) {
      throw new Error('无法确认网页CSS视口几何，未执行视觉坐标输入')
    }
    return { width: viewport.clientWidth, height: viewport.clientHeight }
  }

  /** 订阅 page session 事件（过滤本 session） */
  onCdpEvent(method: string, handler: (event: CdpEvent) => void): () => void {
    return this.socket.onCdpEvent(method, (event) => {
      if (event.sessionId === this.pageSessionId) handler(event)
    })
  }

  /** 订阅底层 WS 断线（Chrome 关闭/崩溃 → 编排层据此 PAUSED） */
  onDisconnect(handler: (err: Error) => void): void {
    this.socket.on('disconnect', handler)
  }

  // ===== 类型化业务方法（白名单内） =====

  async pageEnable(): Promise<void> {
    await this.socket.send('Page.enable', {}, this.pageSessionId)
  }

  async pageDisable(): Promise<void> {
    await this.socket.send('Page.disable', {}, this.pageSessionId)
  }

  async networkEnable(): Promise<void> {
    await this.socket.send('Network.enable', {}, this.pageSessionId)
  }

  async networkDisable(): Promise<void> {
    await this.socket.send('Network.disable', {}, this.pageSessionId)
  }

  async getFrameTree(): Promise<unknown> {
    return this.socket.send('Page.getFrameTree', {}, this.pageSessionId)
  }

  /** 页内导航到指定 URL（Page.navigate）。goto 幂等兜底专用：点击菜单未触发路由变化时直跳目标页 */
  async pageNavigate(url: string): Promise<void> {
    await this.socket.send('Page.navigate', { url }, this.pageSessionId)
  }

  async captureScreenshot(opts: { format?: 'png' | 'jpeg'; captureBeyondViewport?: boolean } = {}): Promise<string> {
    const result = await this.socket.send<{ data: string }>(
      'Page.captureScreenshot',
      {
        format: opts.format ?? 'png',
        captureBeyondViewport: opts.captureBeyondViewport ?? false,
      },
      this.pageSessionId,
    )
    return result.data
  }

  async captureDomSnapshot(opts: DomSnapshotOptions = {}): Promise<unknown> {
    return this.socket.send(
      'DOMSnapshot.captureSnapshot',
      { computedStyles: opts.computedStyles ?? [], includePaintOrder: opts.includePaintOrder ?? false, includeDOMRects: opts.includeDOMRects ?? false },
      this.pageSessionId,
    )
  }

  async dispatchMouse(opts: {
    type: 'mousePressed' | 'mouseReleased' | 'mouseMoved' | 'mouseWheel'
    x: number
    y: number
    button?: 'none' | 'left' | 'right' | 'middle'
    clickCount?: number
    deltaX?: number
    deltaY?: number
  }): Promise<void> {
    await this.socket.send(
      'Input.dispatchMouseEvent',
      {
        type: opts.type,
        x: opts.x,
        y: opts.y,
        // 滚轮事件不携带按键态（与真机验证过的 spike 参数一致）
        button: opts.button ?? (opts.type === 'mouseWheel' ? 'none' : 'left'),
        clickCount: opts.clickCount ?? (opts.type === 'mouseReleased' ? 1 : undefined),
        // Chrome 150 实测：mouseWheel 的 deltaX/deltaY 均为必填，缺任一个报 -32602
        deltaX: opts.deltaX ?? (opts.type === 'mouseWheel' ? 0 : undefined),
        deltaY: opts.deltaY,
      },
      this.pageSessionId,
    )
  }

  async dispatchKey(opts: {
    type: 'keyDown' | 'keyUp' | 'rawKeyDown' | 'char'
    key: string
    code?: string
    windowsVirtualKeyCode?: number
    /** type='char' 时的字符文本（逐字输入中文用，如「请」） */
    text?: string
    /** 修饰键位掩码（CDP 协议字段）：Alt=1 / Ctrl=2 / Meta=4 / Shift=8。组合键用（如搜索框清空 ctrl+a） */
    modifiers?: number
  }): Promise<void> {
    await this.socket.send(
      'Input.dispatchKeyEvent',
      {
        type: opts.type,
        key: opts.key,
        code: opts.code,
        windowsVirtualKeyCode: opts.windowsVirtualKeyCode,
        text: opts.text,
        modifiers: opts.modifiers,
      },
      this.pageSessionId,
    )
  }

  async detach(): Promise<void> {
    if (this.pageTargetId) {
      try {
        await this.socket.send('Target.detachFromTarget', { sessionId: this.pageSessionId })
      } catch {
        // detach 失败不阻塞关闭
      }
    }
    this.pageSessionId = undefined
    this.pageTargetId = undefined
  }

  async close(): Promise<void> {
    await this.detach()
    await this.socket.close()
  }
}
