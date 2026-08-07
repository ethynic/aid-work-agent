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

export class CdpGateway {
  private socket: CdpSocket
  private readonly fetchImpl: typeof fetch
  private pageSessionId?: string
  private pageTargetId?: string

  constructor(opts: GatewayOptions = {}) {
    this.socket = new CdpSocket({
      timeoutMs: opts.timeoutMs,
      auditWriter: opts.auditWriter,
      socketFactory: opts.socketFactory,
    })
    this.fetchImpl = opts.fetchImpl ?? fetch
  }

  /** 连接到 browser-level CDP。httpEndpoint 形如 http://127.0.0.1:9222 */
  async connect(httpEndpoint: string): Promise<void> {
    const version = await this.fetchImpl(`${httpEndpoint}/json/version`).then((r) => r.json()) as {
      webSocketDebuggerUrl: string
    }
    if (!version.webSocketDebuggerUrl) {
      throw new Error('CDP /json/version missing webSocketDebuggerUrl')
    }
    await this.socket.connect(version.webSocketDebuggerUrl)
  }

  get isConnected(): boolean {
    return this.socket.isConnected
  }

  /** 列出所有 target */
  async getTargets(): Promise<CdpTargetInfo[]> {
    const result = await this.socket.send<{ targetInfos: CdpTargetInfo[] }>('Target.getTargets')
    return result.targetInfos ?? []
  }

  /** 找到推荐页 target 并 attach，返回 sessionId（后续方法自动带） */
  async attachToRecommendPage(): Promise<AttachResult> {
    const targets = await this.getTargets()
    const page = targets.find(
      (t) => t.type === 'page' && t.url.includes('zhipin.com'),
    )
    if (!page) {
      throw new Error(`no BOSS page target found among ${targets.length} targets`)
    }
    return this.attachToTarget(page.targetId)
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

  async captureDomSnapshot(): Promise<unknown> {
    return this.socket.send(
      'DOMSnapshot.captureSnapshot',
      { computedStyles: [], includePaintOrder: false, includeDOMRects: false },
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
  }): Promise<void> {
    await this.socket.send(
      'Input.dispatchKeyEvent',
      {
        type: opts.type,
        key: opts.key,
        code: opts.code,
        windowsVirtualKeyCode: opts.windowsVirtualKeyCode,
        text: opts.text,
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
