/**
 * 云端 Runtime API 客户端（M0.3 契约，前缀 /api/local-tools）。
 *
 * - 网络错误抛 NetworkError（可重试）；HTTP 非 2xx 抛 ApiError（带 status）。
 * - 401 抛 DeviceRevokedError（设备 token 无效/已撤销，主循环停止）。
 * - 日志绝不输出 token / claim_token。
 */

export class NetworkError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'NetworkError'
  }
}

export class ApiError extends Error {
  readonly status: number
  readonly serverMessage?: string
  constructor(status: number, message: string, serverMessage?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.serverMessage = serverMessage
  }
}

export class DeviceRevokedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'DeviceRevokedError'
  }
}

/** 主循环主动中止（shutdown），非网络错误，不重试 */
export class AbortLoopError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AbortLoopError'
  }
}

export interface PairResponse {
  device_id: string
  device_token: string
}

export interface HeartbeatResponse {
  selected: boolean
  server_time?: string
}

export interface ClaimedInvocation {
  invocation_id: string
  tool_name: string
  arguments: Record<string, unknown>
  claim_token: string
  lease_expires_at?: string
  provider?: string
}

export interface ProgressAck {
  seq: number
  cancel: boolean
}

export interface InvocationResultPayload {
  claim_token: string
  success: boolean
  code?: string
  message?: string
  effect?: string
  data?: Record<string, unknown>
  retryable?: boolean
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export class ApiClient {
  private readonly baseUrl: string
  private readonly token: string | null

  constructor(baseUrl: string, token?: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
    this.token = token ?? null
  }

  private async post(path: string, body: unknown, opts: { auth?: boolean; timeoutMs?: number; signal?: AbortSignal } = {}): Promise<unknown> {
    const headers: Record<string, string> = { ...JSON_HEADERS }
    if (opts.auth !== false) {
      if (!this.token) throw new Error('缺少设备 token')
      headers['Authorization'] = `Bearer ${this.token}`
    }
    const timeoutSignal = AbortSignal.timeout(opts.timeoutMs ?? 15_000)
    const signal = opts.signal ? AbortSignal.any([timeoutSignal, opts.signal]) : timeoutSignal
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body ?? {}),
        signal,
      })
    } catch (err) {
      if (opts.signal?.aborted) throw new AbortLoopError('请求被中止（Runtime 关闭）')
      const detail = err instanceof Error ? err.message : String(err)
      throw new NetworkError(`网络请求失败: ${detail}`)
    }
    let parsed: Record<string, unknown> = {}
    try {
      parsed = (await response.json()) as Record<string, unknown>
    } catch {
      // 非 JSON 响应按无 body 处理
    }
    if (response.status === 401 && opts.auth !== false) {
      throw new DeviceRevokedError('设备 token 无效或已撤销，请重新 pair')
    }
    if (!response.ok) {
      const detail = parsed && typeof parsed['detail'] === 'object' && parsed['detail'] !== null
        ? String((parsed['detail'] as Record<string, unknown>)['error'] ?? '')
        : typeof parsed['detail'] === 'string'
          ? parsed['detail']
          : ''
      throw new ApiError(response.status, `云端返回 HTTP ${response.status}${detail ? `: ${detail}` : ''}`, detail || undefined)
    }
    return parsed
  }

  /** 配对码换设备 token（无 token 调用） */
  async pair(req: {
    code: string
    name?: string
    platform: string
    runtime_version: string
    capabilities: Record<string, unknown>
    machine_fingerprint: string
  }): Promise<PairResponse> {
    const res = (await this.post('/api/local-tools/runtime/pair', req, { auth: false })) as Record<string, unknown>
    const deviceId = res['device_id']
    const deviceToken = res['device_token']
    if (typeof deviceId !== 'string' || typeof deviceToken !== 'string' || !deviceToken) {
      throw new ApiError(500, '配对响应缺少 device_id/device_token')
    }
    return { device_id: deviceId, device_token: deviceToken }
  }

  async heartbeat(req: { runtime_version: string; capabilities: Record<string, unknown>; manifest_digest: string }, signal?: AbortSignal): Promise<HeartbeatResponse> {
    const res = (await this.post('/api/local-tools/runtime/heartbeat', req, { signal })) as Record<string, unknown>
    return { selected: Boolean(res['selected']), server_time: res['server_time'] as string | undefined }
  }

  /** 长轮询领取 invocation（服务端最长 wait 秒；超时返回 null） */
  async claim(waitSeconds: number, signal?: AbortSignal): Promise<ClaimedInvocation | null> {
    const res = (await this.post(`/api/local-tools/runtime/claim?wait=${waitSeconds}`, {}, {
      timeoutMs: (waitSeconds + 15) * 1000,
      signal,
    })) as Record<string, unknown>
    if (res['invocation'] === null) return null
    if (typeof res['invocation_id'] !== 'string') return null
    return {
      invocation_id: res['invocation_id'],
      tool_name: String(res['tool_name'] ?? ''),
      arguments: (res['arguments'] as Record<string, unknown>) ?? {},
      claim_token: String(res['claim_token'] ?? ''),
      lease_expires_at: res['lease_expires_at'] as string | undefined,
      provider: res['provider'] as string | undefined,
    }
  }

  async started(invocationId: string, claimToken: string): Promise<void> {
    await this.post(`/api/local-tools/runtime/invocations/${invocationId}/started`, { claim_token: claimToken })
  }

  async progress(invocationId: string, payload: {
    claim_token: string
    stage?: string
    current?: number
    total?: number
    message?: string
  }): Promise<ProgressAck> {
    const res = (await this.post(`/api/local-tools/runtime/invocations/${invocationId}/progress`, payload)) as Record<string, unknown>
    return { seq: Number(res['seq'] ?? 0), cancel: Boolean(res['cancel']) }
  }

  async result(invocationId: string, payload: InvocationResultPayload): Promise<void> {
    await this.post(`/api/local-tools/runtime/invocations/${invocationId}/result`, payload)
  }
}
