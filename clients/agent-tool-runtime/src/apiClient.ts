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

export interface WriteAuthorizePayload {
  claim_token: string
  request_id: string
  target_version?: string
  payload_hash?: string
}

export interface WriteAuthorizeResponse {
  permit_id: string
  permit_token: string
  deadline_at: string
}

/** v2 operation-result 回传 payload（服务端 effect/phase 优先于 success/code 解释，R10） */
export interface OperationResultPayload {
  claim_token: string
  request_id: string
  effect: string
  phase?: string
  safe_to_retry?: boolean
  evidence_ref?: string
  permit_id?: string
  permit_token?: string
  success?: boolean
  code?: string
  message?: string
  data?: Record<string, unknown>
  /** 与 safe_to_retry 同值的兼容字段（服务端 OperationResultRequest 未定义，extra 默认忽略） */
  retryable?: boolean
}

export interface OperationResultAck {
  state: string
  effect: string
  run_state?: string | null
  late?: boolean
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

/** 端侧会话任务设备协议类型（C2，设计 §9） */
export interface SessionTaskControl {
  status: string
  control_epoch: number
  server_control_seq: number
  completion_reason: string | null
  blocked_reason: string | null
}

export interface SessionTaskClaim {
  assignment_id: string
  task_id: string
  spec: Record<string, unknown>
  spec_revision: number
  fence: number
  control_epoch: number
  server_control_seq: number
  lease_seconds: number
  conversation_binding_id: string
  binding_version: number
  account_identity_version: number
}

export interface SessionTaskControlAck {
  lease_seconds: number
  control: SessionTaskControl
}

export interface SessionTaskDecision {
  decision_id: string
  status: string
  decision_kind: string
  batch_id: string
  input_version: number
}

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

  /** GET 请求（决策状态查询等；错误分类与 post 一致） */
  private async get(path: string, opts: { signal?: AbortSignal } = {}): Promise<unknown> {
    const headers: Record<string, string> = { ...JSON_HEADERS }
    if (!this.token) throw new Error('缺少设备 token')
    headers['Authorization'] = `Bearer ${this.token}`
    const timeoutSignal = AbortSignal.timeout(15_000)
    const signal = opts.signal ? AbortSignal.any([timeoutSignal, opts.signal]) : timeoutSignal
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path}`, { method: 'GET', headers, signal })
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
    if (response.status === 401) throw new DeviceRevokedError('设备 token 无效或已撤销，请重新 pair')
    if (!response.ok) throw new ApiError(response.status, `云端返回 HTTP ${response.status}`)
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

  /** v2 写动作许可申请（Runtime 内部 API，不暴露给 LLM；§5.2） */
  async writeAuthorize(invocationId: string, payload: WriteAuthorizePayload): Promise<WriteAuthorizeResponse> {
    const res = (await this.post(`/api/local-tools/runtime/invocations/${invocationId}/write-authorize`, payload)) as Record<string, unknown>
    const permitId = res['permit_id']
    const permitToken = res['permit_token']
    const deadlineAt = res['deadline_at']
    if (typeof permitId !== 'string' || !permitId || typeof permitToken !== 'string' || !permitToken) {
      throw new ApiError(500, '许可响应缺少 permit_id/permit_token')
    }
    return {
      permit_id: permitId,
      permit_token: permitToken,
      deadline_at: typeof deadlineAt === 'string' ? deadlineAt : '',
    }
  }

  /** v2 操作结果回传（持久 ACK 幂等；迟到只对账不改判） */
  async operationResult(invocationId: string, payload: OperationResultPayload): Promise<OperationResultAck> {
    const res = (await this.post(`/api/local-tools/runtime/invocations/${invocationId}/operation-result`, payload)) as Record<string, unknown>
    return {
      state: String(res['state'] ?? ''),
      effect: String(res['effect'] ?? ''),
      run_state: (res['run_state'] as string | null | undefined) ?? null,
      late: Boolean(res['late']),
    }
  }

  // ----- 端侧会话任务设备端点（C2，设计 §9；envelope {success,data} 剥壳） -----

  private sessionData(res: unknown): Record<string, unknown> {
    const env = res as Record<string, unknown>
    const data = env && typeof env['success'] === 'boolean' ? env['data'] : env
    return (data && typeof data === 'object' ? data : {}) as Record<string, unknown>
  }

  /** 领取会话任务（无任务返回 null；短轮询，engine 自控节奏） */
  async sessionTaskClaim(runtimeInstanceId: string, signal?: AbortSignal): Promise<SessionTaskClaim | null> {
    const res = (await this.post('/api/local-tools/runtime/session-tasks/claim', {
      runtime_instance_id: runtimeInstanceId,
    }, { signal, timeoutMs: 20_000 })) as Record<string, unknown>
    if (Object.keys(res).length === 0) return null // 204 无任务
    const data = this.sessionData(res)
    const assignmentId = data['assignment_id']
    if (typeof assignmentId !== 'string' || !assignmentId) return null
    return {
      assignment_id: assignmentId,
      task_id: String(data['task_id'] ?? ''),
      spec: (data['spec'] as Record<string, unknown>) ?? {},
      spec_revision: Number(data['spec_revision'] ?? 0),
      fence: Number(data['fence'] ?? 0),
      control_epoch: Number(data['control_epoch'] ?? 0),
      server_control_seq: Number(data['server_control_seq'] ?? 0),
      lease_seconds: Number(data['lease_seconds'] ?? 60),
      conversation_binding_id: String(data['conversation_binding_id'] ?? ''),
      binding_version: Number(data['binding_version'] ?? 0),
      account_identity_version: Number(data['account_identity_version'] ?? 0),
    }
  }

  /** 续租并取回最新控制（暂停/终态如实返回，Runtime 停止新副作用） */
  async sessionTaskRenew(assignmentId: string, payload: { fence: number; control_epoch: number }, signal?: AbortSignal): Promise<SessionTaskControlAck> {
    const res = (await this.post(`/api/local-tools/runtime/session-tasks/${assignmentId}/renew`, payload, { signal })) as Record<string, unknown>
    const data = this.sessionData(res)
    const control = (data['control'] as Record<string, unknown>) ?? {}
    return {
      lease_seconds: Number(data['lease_seconds'] ?? 0),
      control: {
        status: String(control['status'] ?? ''),
        control_epoch: Number(control['control_epoch'] ?? 0),
        server_control_seq: Number(control['server_control_seq'] ?? 0),
        completion_reason: (control['completion_reason'] as string | null | undefined) ?? null,
        blocked_reason: (control['blocked_reason'] as string | null | undefined) ?? null,
      },
    }
  }

  /** events 连续前缀上报（≤100 条/批；返回 ack_seq 与控制） */
  async sessionTaskEvents(assignmentId: string, payload: {
    fence: number
    records: Array<{ local_seq: number; event_id: string; type: string; payload: unknown }>
  }, signal?: AbortSignal): Promise<{ ack_seq: number; historical?: boolean; control: SessionTaskControl }> {
    const res = (await this.post(`/api/local-tools/runtime/session-tasks/${assignmentId}/events`, payload, { signal, timeoutMs: 30_000 })) as Record<string, unknown>
    const data = this.sessionData(res)
    const control = (data['control'] as Record<string, unknown>) ?? {}
    return {
      ack_seq: Number(data['ack_seq'] ?? 0),
      historical: Boolean(data['historical']),
      control: {
        status: String(control['status'] ?? ''),
        control_epoch: Number(control['control_epoch'] ?? 0),
        server_control_seq: Number(control['server_control_seq'] ?? 0),
        completion_reason: (control['completion_reason'] as string | null | undefined) ?? null,
        blocked_reason: (control['blocked_reason'] as string | null | undefined) ?? null,
      },
    }
  }

  /** 创建决策（202；携带冻结版本/控制代，旧请求被拒 STALE） */
  async sessionTaskCreateDecision(assignmentId: string, payload: {
    fence: number
    batch_id: string
    decision_kind: string
    input_version: number
    control_epoch: number
    spec_revision: number
  }, signal?: AbortSignal): Promise<{ decision_id: string; status: string }> {
    const res = (await this.post(`/api/local-tools/runtime/session-tasks/${assignmentId}/decisions`, payload, { signal })) as Record<string, unknown>
    const data = this.sessionData(res)
    return { decision_id: String(data['decision_id'] ?? ''), status: String(data['status'] ?? '') }
  }

  /** 决策状态查询（普通网络轮询，不是 LLM 调用） */
  async sessionTaskGetDecision(assignmentId: string, decisionId: string, signal?: AbortSignal): Promise<SessionTaskDecision> {
    const res = (await this.get(`/api/local-tools/runtime/session-tasks/${assignmentId}/decisions/${decisionId}`, { signal })) as Record<string, unknown>
    const data = this.sessionData(res)
    return {
      decision_id: String(data['decision_id'] ?? decisionId),
      status: String(data['status'] ?? ''),
      decision_kind: String(data['decision_kind'] ?? ''),
      batch_id: String(data['batch_id'] ?? ''),
      input_version: Number(data['input_version'] ?? 0),
    }
  }
}
