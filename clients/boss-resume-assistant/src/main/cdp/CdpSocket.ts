/**
 * 原生 CDP WebSocket 客户端（产品化 spike）。
 * - 请求关联（id）、超时、断线清理
 * - session 路由（flatten session 的响应/事件）
 * - 事件订阅（spike 未实现，Phase 3/4 需要 Network/Page 事件）
 * - 协议策略硬拒绝（send 前 assertMethodAllowed）
 * - 脱敏审计
 *
 * 禁止引入 Playwright/Puppeteer/Selenium；禁止 Runtime.*。
 */
import { EventEmitter } from 'node:events'
import { assertMethodAllowed, ForbiddenCdpMethodError } from './methodPolicy.js'
import type { AuditWriter } from './audit.js'

export interface CdpSocketOptions {
  timeoutMs?: number
  auditWriter?: AuditWriter
  /** 可注入 WebSocket 实现（测试用 mock server） */
  WebSocketImpl?: typeof import('ws').WebSocket
  /** 可注入的 connect 工厂（测试用） */
  socketFactory?: (url: string) => CdpWebSocketLike
}

/** WebSocket 子集接口（便于 mock，宽松事件签名避免重载冲突） */
export interface CdpWebSocketLike {
  readyState: number
  CLOSED: number
  on(event: string, listener: (...args: unknown[]) => void): unknown
  once(event: string, listener: (...args: unknown[]) => void): unknown
  off(event: string, listener: (...args: unknown[]) => void): unknown
  send(data: string): void
  close(): void
  terminate?(): void
}

interface PendingRequest {
  resolve: (value: unknown) => void
  reject: (err: Error) => void
  timer: NodeJS.Timeout
  sessionId?: string
}

export interface CdpResponse {
  id: number
  result?: unknown
  error?: { code: number; message: string }
  sessionId?: string
}

export interface CdpEvent {
  method: string
  params: Record<string, unknown>
  sessionId?: string
}

/** CDP socket 事件：'event' (CdpEvent) / 'disconnect' (Error) / 'forbidden' (method) */
export class CdpSocket extends EventEmitter {
  private readonly timeoutMs: number
  private readonly auditWriter?: AuditWriter
  private readonly socketFactory?: (url: string) => CdpWebSocketLike
  private nextId = 1
  private pending = new Map<number, PendingRequest>()
  private socket?: CdpWebSocketLike

  constructor(opts: CdpSocketOptions = {}) {
    super()
    this.timeoutMs = opts.timeoutMs ?? 8000
    this.auditWriter = opts.auditWriter
    this.socketFactory = opts.socketFactory
  }

  async connect(webSocketUrl: string): Promise<void> {
    if (this.socket) throw new Error('CDP socket is already connected')
    const socket = this.socketFactory
      ? this.socketFactory(webSocketUrl)
      : await this.createDefaultSocket(webSocketUrl)
    this.socket = socket
    try {
      await new Promise<void>((resolve, reject) => {
        socket.once('open', () => resolve())
        socket.once('error', (err) => reject(err as Error))
      })
    } catch (error) {
      if (this.socket === socket) this.socket = undefined
      throw error
    }
    socket.on('message', (data) => this.onMessage(data as Buffer | string))
    socket.on('error', (error) => this.rejectAll(new Error(`CDP WebSocket error: ${(error as Error).message}`)))
    socket.on('close', () => {
      if (this.socket === socket) this.socket = undefined
      this.rejectAll(new Error('CDP WebSocket disconnected'))
      this.emit('disconnect', new Error('CDP WebSocket disconnected'))
    })
  }

  private async createDefaultSocket(url: string): Promise<CdpWebSocketLike> {
    const ws = await import('ws')
    const WebSocketClass = ws.WebSocket ?? (ws.default as unknown as typeof ws.WebSocket)
    return new WebSocketClass(url) as unknown as CdpWebSocketLike
  }

  get isConnected(): boolean {
    return !!this.socket
  }

  /** 发送 CDP method。send 前硬拒绝禁止方法。返回 result。 */
  async send<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    sessionId?: string,
  ): Promise<T> {
    const startedAt = Date.now()
    try {
      assertMethodAllowed(method)
    } catch (error) {
      this.emit('forbidden', method)
      await this.audit(method, params, sessionId, startedAt, 'FORBIDDEN')
      throw error
    }
    if (!this.socket) throw new Error('CDP socket is not connected')

    const id = this.nextId++
    const result = new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`CDP request timed out: ${method}`))
      }, this.timeoutMs)
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer, sessionId })
      try {
        this.socket!.send(
          JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }),
        )
      } catch (error) {
        clearTimeout(timer)
        this.pending.delete(id)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
    try {
      const value = await result
      await this.audit(method, params, sessionId, startedAt, 'OK')
      return value
    } catch (error) {
      await this.audit(method, params, sessionId, startedAt, 'ERROR')
      throw error
    }
  }

  /** 订阅 CDP 事件（按 method 过滤）。返回取消订阅函数。 */
  onCdpEvent(method: string, handler: (event: CdpEvent) => void): () => void {
    const wrapped = (event: CdpEvent) => {
      if (event.method === method) handler(event)
    }
    this.on('event', wrapped)
    return () => this.off('event', wrapped)
  }

  async close(): Promise<void> {
    if (!this.socket) return
    const socket = this.socket
    this.socket = undefined
    socket.close()
    if (socket.readyState !== socket.CLOSED) {
      await new Promise<void>((resolve) => {
        const onClose = () => {
          clearTimeout(timer)
          resolve()
        }
        const timer = setTimeout(() => {
          socket.off('close', onClose)
          socket.terminate?.()
          resolve()
        }, this.timeoutMs)
        socket.once('close', onClose)
      })
    }
    this.rejectAll(new Error('CDP socket closed'))
  }

  private onMessage(data: Buffer | string): void {
    let message: CdpResponse & Partial<CdpEvent>
    try {
      message = JSON.parse(data.toString())
    } catch {
      return
    }
    // 响应（有 id）
    if (typeof message.id === 'number') {
      const pending = this.pending.get(message.id)
      if (!pending) return
      if (pending.sessionId !== message.sessionId) {
        clearTimeout(pending.timer)
        this.pending.delete(message.id)
        pending.reject(new Error(`CDP response session mismatch for request ${message.id}`))
        return
      }
      clearTimeout(pending.timer)
      this.pending.delete(message.id)
      if (message.error) {
        pending.reject(new Error(`CDP error ${message.error.code}: ${message.error.message}`))
      } else {
        pending.resolve(message.result)
      }
      return
    }
    // 事件（有 method 无 id）
    if (typeof message.method === 'string') {
      this.emit('event', {
        method: message.method,
        params: message.params ?? {},
        sessionId: message.sessionId,
      } satisfies CdpEvent)
    }
  }

  private rejectAll(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.pending.clear()
  }

  private async audit(
    method: string,
    params: Record<string, unknown>,
    sessionId: string | undefined,
    startedAt: number,
    status: 'OK' | 'ERROR' | 'FORBIDDEN',
  ): Promise<void> {
    if (!this.auditWriter) return
    try {
      await this.auditWriter.write({ method, sessionId, params, durationMs: Date.now() - startedAt, status })
    } catch {
      // 审计失败不应中断主流程，但应记录
    }
  }
}

export { ForbiddenCdpMethodError }
