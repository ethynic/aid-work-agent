/**
 * FakeCloud：node:http 内存版 M0.3 Runtime API（6 个端点），供 node:test 使用。
 *
 * - 配对码 / 设备 token / invocation 队列 / 取消标志 / 终态幂等，全内存
 * - 故障注入：offline（立即断连）、failStatus（统一返回指定 HTTP 状态）
 * - callLog 记录 started/progress/result 顺序供断言
 */
import http from 'node:http'
import { randomUUID } from 'node:crypto'
import type { AddressInfo } from 'node:net'

interface FakeDevice {
  device_id: string
  token: string
  heartbeats: number[]
}

export interface FakeInvocation {
  invocation_id: string
  tool_name: string
  arguments: Record<string, unknown>
  state: 'queued' | 'claimed' | 'running' | 'succeeded' | 'failed' | 'unknown'
  claim_token: string | null
  seq: number
  cancel_requested: boolean
  result: Record<string, unknown> | null
}

export interface CallLogEntry {
  type: 'started' | 'progress' | 'result'
  invocation_id: string
  payload: Record<string, unknown>
  at: number
}

const TERMINAL = new Set(['succeeded', 'failed', 'unknown', 'cancelled', 'expired'])

export class FakeCloud {
  private server: http.Server | null = null
  private codes = new Set<string>()
  private devices = new Map<string, FakeDevice>() // token -> device
  private invocations = new Map<string, FakeInvocation>()
  readonly callLog: CallLogEntry[] = []
  offline = false
  failStatus: number | null = null
  /** 最近一次 HTTP pair 签发的 token（pair 测试断言用） */
  lastPairToken: string | null = null
  /** claim 长轮询内部 tick（默认 25ms） */
  claimTickMs = 25

  get baseUrl(): string {
    const addr = this.server!.address() as AddressInfo
    return `http://127.0.0.1:${addr.port}`
  }

  async start(): Promise<string> {
    this.server = http.createServer((req, res) => {
      void this.handle(req, res).catch((err) => {
        res.writeHead(500, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ detail: { error: String(err) } }))
      })
    })
    await new Promise<void>((resolve) => this.server!.listen(0, '127.0.0.1', resolve))
    return this.baseUrl
  }

  async stop(): Promise<void> {
    if (!this.server) return
    this.server.closeAllConnections?.()
    await new Promise<void>((resolve) => this.server!.close(() => resolve()))
    this.server = null
  }

  // ---------- 测试注入 API ----------

  issueCode(code: string): void {
    this.codes.add(code.toUpperCase())
  }

  /** 直接造一个已配对设备（跳过 HTTP pair，非 pair 测试用） */
  createDeviceDirectly(): { device_id: string; token: string } {
    const device: FakeDevice = { device_id: randomUUID(), token: randomUUID().replace(/-/g, ''), heartbeats: [] }
    this.devices.set(device.token, device)
    return { device_id: device.device_id, token: device.token }
  }

  enqueueInvocation(toolName: string, args: Record<string, unknown> = {}): string {
    const inv: FakeInvocation = {
      invocation_id: randomUUID(),
      tool_name: toolName,
      arguments: args,
      state: 'queued',
      claim_token: null,
      seq: 0,
      cancel_requested: false,
      result: null,
    }
    this.invocations.set(inv.invocation_id, inv)
    return inv.invocation_id
  }

  cancelInvocation(id: string): void {
    const inv = this.invocations.get(id)
    if (inv && !TERMINAL.has(inv.state)) inv.cancel_requested = true
  }

  getInvocation(id: string): FakeInvocation | undefined {
    return this.invocations.get(id)
  }

  callsFor(id: string): CallLogEntry[] {
    return this.callLog.filter((c) => c.invocation_id === id)
  }

  heartbeatCount(token: string): number {
    return this.devices.get(token)?.heartbeats.length ?? 0
  }

  /** 等待条件满足（轮询），超时抛错 */
  async waitFor(predicate: () => boolean, timeoutMs = 10_000, label = 'condition'): Promise<void> {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      if (predicate()) return
      await new Promise((r) => setTimeout(r, 20))
    }
    throw new Error(`waitFor 超时: ${label}`)
  }

  // ---------- HTTP 处理 ----------

  private async readBody(req: http.IncomingMessage): Promise<Record<string, unknown>> {
    const chunks: Buffer[] = []
    for await (const chunk of req) chunks.push(chunk as Buffer)
    if (chunks.length === 0) return {}
    try {
      return JSON.parse(Buffer.concat(chunks).toString('utf8')) as Record<string, unknown>
    } catch {
      return {}
    }
  }

  private sendJson(res: http.ServerResponse, status: number, body: unknown): void {
    res.writeHead(status, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(body))
  }

  private authDevice(req: http.IncomingMessage): FakeDevice | null {
    const header = req.headers['authorization'] ?? ''
    if (!header.startsWith('Bearer ')) return null
    return this.devices.get(header.slice(7)) ?? null
  }

  private async handle(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
    if (this.offline) {
      req.socket.destroy()
      return
    }
    if (this.failStatus !== null) {
      this.sendJson(res, this.failStatus, { detail: { error: 'injected failure' } })
      return
    }
    const url = new URL(req.url ?? '/', 'http://localhost')
    const path = url.pathname

    if (path === '/api/local-tools/runtime/pair' && req.method === 'POST') {
      const body = await this.readBody(req)
      const code = String(body['code'] ?? '').toUpperCase()
      if (!this.codes.has(code)) {
        this.sendJson(res, 400, { detail: { error: '配对码无效、已过期或已被使用' } })
        return
      }
      this.codes.delete(code)
      const device = this.createDeviceDirectly()
      this.lastPairToken = device.token
      this.sendJson(res, 200, { success: true, device_id: device.device_id, device_token: device.token })
      return
    }

    const device = this.authDevice(req)
    if (!device) {
      this.sendJson(res, 401, { detail: { error: '设备 token 无效或设备已撤销' } })
      return
    }

    if (path === '/api/local-tools/runtime/heartbeat' && req.method === 'POST') {
      device.heartbeats.push(Date.now())
      this.sendJson(res, 200, { success: true, selected: true, server_time: new Date().toISOString() })
      return
    }

    if (path === '/api/local-tools/runtime/claim' && req.method === 'POST') {
      const wait = Math.min(Number(url.searchParams.get('wait') ?? '20'), 30)
      const deadline = Date.now() + wait * 1000
      for (;;) {
        for (const inv of this.invocations.values()) {
          if (inv.state === 'queued') {
            inv.state = 'claimed'
            inv.claim_token = randomUUID().replace(/-/g, '')
            this.sendJson(res, 200, {
              success: true,
              invocation_id: inv.invocation_id,
              tool_name: inv.tool_name,
              arguments: inv.arguments,
              claim_token: inv.claim_token,
              lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
              provider: 'boss-recruiting',
            })
            return
          }
        }
        if (Date.now() >= deadline) {
          this.sendJson(res, 200, { success: true, invocation: null })
          return
        }
        await new Promise((r) => setTimeout(r, this.claimTickMs))
      }
    }

    const invMatch = path.match(/^\/api\/local-tools\/runtime\/invocations\/([^/]+)\/(started|progress|result)$/)
    if (invMatch && req.method === 'POST') {
      const inv = this.invocations.get(invMatch[1]!)
      const action = invMatch[2]!
      const body = await this.readBody(req)
      if (!inv || inv.claim_token !== body['claim_token']) {
        this.sendJson(res, 404, { detail: { error: 'invocation 不存在或 claim token 不匹配' } })
        return
      }
      if (action === 'started') {
        if (inv.state === 'claimed') inv.state = 'running'
        this.callLog.push({ type: 'started', invocation_id: inv.invocation_id, payload: body, at: Date.now() })
        this.sendJson(res, 200, { success: true, state: inv.state })
        return
      }
      if (action === 'progress') {
        if (!['claimed', 'running'].includes(inv.state) && !inv.cancel_requested) {
          this.sendJson(res, 404, { detail: { error: '状态不允许上报进度' } })
          return
        }
        inv.seq += 1
        this.callLog.push({ type: 'progress', invocation_id: inv.invocation_id, payload: { ...body, seq: inv.seq }, at: Date.now() })
        this.sendJson(res, 200, { success: true, seq: inv.seq, cancel: inv.cancel_requested })
        return
      }
      // result：幂等——已终态重复写返回原终态
      if (TERMINAL.has(inv.state)) {
        this.sendJson(res, 200, { success: true, state: inv.state, effect: (inv.result?.['effect'] as string) ?? 'none' })
        return
      }
      const success = Boolean(body['success'])
      const code = typeof body['code'] === 'string' ? body['code'] : undefined
      inv.state = success ? 'succeeded' : code === 'EXECUTION_UNKNOWN' ? 'unknown' : 'failed'
      inv.result = body
      this.callLog.push({ type: 'result', invocation_id: inv.invocation_id, payload: body, at: Date.now() })
      this.sendJson(res, 200, { success: true, state: inv.state, effect: (body['effect'] as string) ?? 'none' })
      return
    }

    this.sendJson(res, 404, { detail: { error: 'not found' } })
  }
}
