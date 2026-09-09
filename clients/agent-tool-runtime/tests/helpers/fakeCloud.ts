/**
 * FakeCloud：node:http 内存版 M0.3 + v2 写路径 Runtime API（8 个端点），供 node:test 使用。
 *
 * - 配对码 / 设备 token / invocation 队列 / 取消标志 / 终态幂等，全内存
 * - 故障注入：offline（立即断连）、failStatus（统一返回指定 HTTP 状态）
 * - v2 写路径注入：writeAuthorizeMode（allow/deny/unavailable/network）、
 *   writeAuthorizeDeadlineAt（过期 deadline）、operationResultInterceptor（逐次拦截回执）
 * - callLog 记录 started/progress/result/write_authorize/operation_result 顺序供断言
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
  /** invocation 级 provider_key：undefined → claim 回包带 'boss-recruiting'（现状）；null → 回包省略该字段（旧服务端） */
  provider?: string | null
  /** v2 write-authorize 已签发的许可（同 invocation 重复申请 → 409，对齐服务端一次性语义） */
  permit?: { permit_id: string; permit_token: string; deadline_at: string; state?: 'issued' | 'expired' }
  /** 迟到回执接纳时许可已过期（R21 服务端 audit permit_expired_late 的镜像标记，断言用） */
  permit_expired_late?: boolean
}

export interface CallLogEntry {
  type: 'started' | 'progress' | 'result' | 'write_authorize' | 'operation_result'
  invocation_id: string
  payload: Record<string, unknown>
  at: number
}

const TERMINAL = new Set(['succeeded', 'failed', 'unknown', 'cancelled', 'expired'])

export type WriteAuthorizeMode = 'allow' | 'deny' | 'unavailable' | 'network'

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
  /** v2 write-authorize 注入模式（默认放行） */
  writeAuthorizeMode: WriteAuthorizeMode = 'allow'
  /** deny 模式的 HTTP 状态与 detail.error 码 */
  writeAuthorizeDenyStatus = 409
  writeAuthorizeDenyCode = 'ADAPTER_DENIED'
  /** 许可 deadline 覆盖（默认 now+120s；注入过去时间即「到达即过期」） */
  writeAuthorizeDeadlineAt: string | null = null
  /** v2 operation-result 逐次拦截：'destroy'=断连、数字=返回该状态、undefined=正常处理 */
  operationResultInterceptor?: (body: Record<string, unknown>) => 'destroy' | number | undefined

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

  enqueueInvocation(toolName: string, args: Record<string, unknown> = {}, opts: { provider?: string | null } = {}): string {
    const inv: FakeInvocation = {
      invocation_id: randomUUID(),
      tool_name: toolName,
      arguments: args,
      state: 'queued',
      claim_token: null,
      seq: 0,
      cancel_requested: false,
      result: null,
      provider: opts.provider,
    }
    this.invocations.set(inv.invocation_id, inv)
    return inv.invocation_id
  }

  cancelInvocation(id: string): void {
    const inv = this.invocations.get(id)
    if (inv && !TERMINAL.has(inv.state)) inv.cancel_requested = true
  }

  /** 模拟服务端清扫把已签发许可置 expired（R21/R22：过期不释放预留；回执绑定通过仍接纳） */
  expirePermit(id: string): void {
    const inv = this.invocations.get(id)
    if (inv?.permit) {
      inv.permit.state = 'expired'
      inv.permit.deadline_at = new Date(Date.now() - 1_000).toISOString()
    }
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
            const response: Record<string, unknown> = {
              success: true,
              invocation_id: inv.invocation_id,
              tool_name: inv.tool_name,
              arguments: inv.arguments,
              claim_token: inv.claim_token,
              lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
            }
            if (inv.provider !== null) response['provider'] = inv.provider ?? 'boss-recruiting'
            this.sendJson(res, 200, response)
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

    const invMatch = path.match(/^\/api\/local-tools\/runtime\/invocations\/([^/]+)\/(started|progress|result|write-authorize|operation-result)$/)
    if (invMatch && req.method === 'POST') {
      const inv = this.invocations.get(invMatch[1]!)
      const action = invMatch[2]!
      const body = await this.readBody(req)

      // v2 写动作许可（§5.2）：可控假实现——放行/拒绝/服务不可用/断连/过期 deadline
      if (action === 'write-authorize') {
        if (!inv || inv.claim_token !== body['claim_token']) {
          this.sendJson(res, 404, { detail: { error: 'CLAIM_MISMATCH', message: 'invocation 不存在或 claim token 不匹配' } })
          return
        }
        if (this.writeAuthorizeMode === 'network') {
          req.socket.destroy()
          return
        }
        if (this.writeAuthorizeMode === 'unavailable') {
          this.sendJson(res, 500, { detail: { error: 'PERMIT_SERVICE_UNAVAILABLE', message: '注入：许可服务暂不可用' } })
          return
        }
        if (this.writeAuthorizeMode === 'deny') {
          this.sendJson(res, this.writeAuthorizeDenyStatus, {
            detail: { error: this.writeAuthorizeDenyCode, message: '注入：写动作许可被拒绝' },
          })
          return
        }
        const invRequestId = inv.arguments['request_id']
        if (typeof invRequestId === 'string' && invRequestId !== body['request_id']) {
          this.sendJson(res, 409, { detail: { error: 'REQUEST_ID_MISMATCH', message: 'request_id 与 invocation 不匹配' } })
          return
        }
        // 许可绑定 target_version/payload_hash（对齐 permits.py：请求携带且与 invocation 不一致 → 409）
        const invTargetVersion = inv.arguments['target_version']
        if (body['target_version'] !== undefined && invTargetVersion !== body['target_version']) {
          this.sendJson(res, 409, { detail: { error: 'TARGET_VERSION_MISMATCH', message: 'target_version 与 invocation 不匹配' } })
          return
        }
        const invPayloadHash = inv.arguments['payload_hash']
        if (body['payload_hash'] !== undefined && invPayloadHash !== body['payload_hash']) {
          this.sendJson(res, 409, { detail: { error: 'PAYLOAD_HASH_MISMATCH', message: 'payload_hash 与 invocation 不匹配' } })
          return
        }
        if (inv.permit) {
          // 服务端一次性语义：同 delivery 已持有 issued/consumed 许可 → 409
          this.sendJson(res, 409, { detail: { error: 'PERMIT_ALREADY_ISSUED', message: '该 delivery 已持有许可（一次性）' } })
          return
        }
        const permit = {
          permit_id: randomUUID(),
          permit_token: randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, ''),
          deadline_at: this.writeAuthorizeDeadlineAt ?? new Date(Date.now() + 120_000).toISOString(),
        }
        inv.permit = permit
        this.callLog.push({ type: 'write_authorize', invocation_id: inv.invocation_id, payload: { ...body, permit_id: permit.permit_id }, at: Date.now() })
        this.sendJson(res, 200, { success: true, ...permit })
        return
      }

      // v2 操作结果回传（幂等 ACK；迟到只对账不改判——映射对齐服务端 _map_delivery_outcome）
      if (action === 'operation-result') {
        const intercepted = this.operationResultInterceptor?.(body)
        if (intercepted === 'destroy') {
          req.socket.destroy()
          return
        }
        if (typeof intercepted === 'number') {
          this.sendJson(res, intercepted, { detail: { error: 'INJECTED_RESULT_STATUS', message: `注入 HTTP ${intercepted}` } })
          return
        }
        if (!inv || inv.claim_token !== body['claim_token']) {
          this.sendJson(res, 404, { detail: { error: 'CLAIM_MISMATCH', message: 'invocation 不存在或 claim token 不匹配' } })
          return
        }
        const effect = typeof body['effect'] === 'string' ? body['effect'] : ''
        if (!['none', 'applied', 'unknown'].includes(effect)) {
          this.sendJson(res, 422, { detail: { error: 'INVALID_EFFECT', message: `非法 effect: ${effect}` } })
          return
        }
        // request_id 绑定（对齐 operation_result.py：与 invocation arguments 不一致 → 409；
        // 合成 invocation（无 request_id 字段，如 v2Gate 门禁样例）不强制——真实服务端在
        // enqueue 即要求 v2 arguments 携带 request_id，这里只防 Runtime 回显回归）
        const invRequestId = inv.arguments['request_id']
        if (typeof invRequestId === 'string' && invRequestId !== body['request_id']) {
          this.sendJson(res, 409, { detail: { error: 'REQUEST_ID_MISMATCH', message: 'request_id 与 invocation 不匹配' } })
          return
        }
        const phase = typeof body['phase'] === 'string' ? body['phase'] : undefined
        if (inv.result !== null) {
          // 幂等 ACK：迟到重复回执 2xx + late=true，不改判
          this.callLog.push({ type: 'operation_result', invocation_id: inv.invocation_id, payload: body, at: Date.now() })
          this.sendJson(res, 200, { success: true, state: inv.state, effect: (inv.result['effect'] as string) ?? 'none', run_state: null, late: true })
          return
        }
        // permit 绑定（对齐 operation_result.py：applied+verified 必须携带有效 permit；
        // 携带 permit_id 时校验归属与 token——防止 Runtime 回执字段回归被宽松 fake 掩盖）
        const permitId = typeof body['permit_id'] === 'string' ? body['permit_id'] : undefined
        const permitToken = typeof body['permit_token'] === 'string' ? body['permit_token'] : undefined
        if (permitId === undefined) {
          if (effect === 'applied' && phase === 'verified') {
            this.sendJson(res, 409, { detail: { error: 'PERMIT_REQUIRED', message: 'applied 结果必须携带有效 permit' } })
            return
          }
        } else {
          const bindingInvalid = !inv.permit
            || inv.permit.permit_id !== permitId
            || (permitToken !== undefined && inv.permit.permit_token !== permitToken)
          if (bindingInvalid) {
            this.sendJson(res, 403, { detail: { error: 'PERMIT_BINDING_INVALID', message: '许可绑定校验失败' } })
            return
          }
          // R21 服务端语义镜像：绑定通过即接纳迟到证据，不看 permit 状态（issued/expired/
          // consumed 均可）；过期许可的迟到回执照常落账并镜像 audit permit_expired_late
          if (inv.permit?.state === 'expired') inv.permit_expired_late = true
        }
        // effect/phase → invocation 终态（对齐服务端：unknown 优先；applied 须有 verified 证据）
        const mapped = effect === 'unknown' || phase === 'unknown'
          ? 'unknown'
          : effect === 'applied' && phase === 'verified'
            ? 'succeeded'
            : effect === 'applied'
              ? 'unknown'
              : 'failed'
        inv.state = mapped as FakeInvocation['state']
        inv.result = body
        this.callLog.push({ type: 'operation_result', invocation_id: inv.invocation_id, payload: body, at: Date.now() })
        this.sendJson(res, 200, { success: true, state: inv.state, effect, run_state: null, late: false })
        return
      }

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
