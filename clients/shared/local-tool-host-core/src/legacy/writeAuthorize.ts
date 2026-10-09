/**
 * v2 写动作许可客户端（§5.2 / R14）。
 *
 * - 在最后一次目标复验后、输入开始前申请短期一次性许可：POST write-authorize
 *   {claim_token, request_id, target_version, payload_hash} → {permit_id, permit_token, deadline_at}。
 * - 本地以单调时钟（process.hrtime.bigint，不受系统时间回拨影响）限制许可有效期：
 *   min(服务端 deadline 剩余, 本地上限 LOCAL_PERMIT_CAP_MS)。进程重启后 hrtime 清零且
 *   permit 仅存在于执行中 invocation 的内存，旧许可一律不可复用。
 * - 拿不到许可（4xx 明确拒绝/网络失败/到达即过期）→ 抛 PermitAcquireError，调用方终止
 *   执行（终态 PERMIT_DENIED/PERMIT_UNAVAILABLE，effect none，绝不开始输入）。
 * - permit_token 与 claim_token 同级敏感：仅内存持有，不落盘（journal 只记 permit_id）、不打印。
 */
import type { ApiClient, WriteAuthorizePayload, WriteAuthorizeResponse } from './apiClient.js'
import { ApiError, DeviceRevokedError, NetworkError } from './apiClient.js'

/** 本地许可有效期上限（宪章 P1-C：min(服务端 deadline, 90s)） */
export const LOCAL_PERMIT_CAP_MS = 90_000

/** 服务端拒绝码中可安全重试的（另起新 attempt 可能成功）；其余按不可重试处理 */
const RETRYABLE_SERVER_CODES = new Set([
  'QUOTA_EXCEEDED', // 窗口滚动后可再试
  'LEASE_EXPIRED', // 重新 claim 后可再试
])

export type PermitFailureCode = 'PERMIT_DENIED' | 'PERMIT_UNAVAILABLE'

export class PermitAcquireError extends Error {
  constructor(
    readonly code: PermitFailureCode,
    message: string,
    /** 服务端拒绝码（网络失败/本地过期时为 null） */
    readonly serverCode: string | null,
    /** 是否可安全重试（true → 上层可另建 attempt） */
    readonly retryable: boolean,
  ) {
    super(message)
    this.name = 'PermitAcquireError'
  }
}

export interface WritePermit {
  readonly permitId: string
  /** 一次性许可 token（明文，仅签发方与本进程内存；绝不落盘/打印） */
  readonly permitToken: string
  /** 本地单调时钟下是否仍有效（hrtime 单调，进程重启后不可比） */
  isValid(): boolean
  /** 剩余有效毫秒（<=0 即过期） */
  msRemaining(): number
}

class WritePermitImpl implements WritePermit {
  constructor(
    readonly permitId: string,
    readonly permitToken: string,
    /** hrtime 纳秒单调截止点 */
    private readonly deadlineNs: bigint,
  ) {}

  isValid(): boolean {
    return process.hrtime.bigint() < this.deadlineNs
  }

  msRemaining(): number {
    return Number(this.deadlineNs - process.hrtime.bigint()) / 1_000_000
  }
}

function nowNs(): bigint {
  return process.hrtime.bigint()
}

/**
 * 申请 v2 写动作许可。任何失败路径都抛 PermitAcquireError——调用方必须终止执行
 * （effect none），不得降级为无许可执行。
 */
export async function acquireWritePermit(
  api: ApiClient,
  invocationId: string,
  req: WriteAuthorizePayload,
  opts: { localCapMs?: number } = {},
): Promise<WritePermit> {
  const capMs = opts.localCapMs ?? LOCAL_PERMIT_CAP_MS
  let resp: WriteAuthorizeResponse
  try {
    resp = await api.writeAuthorize(invocationId, req)
  } catch (err) {
    if (err instanceof NetworkError) {
      throw new PermitAcquireError('PERMIT_UNAVAILABLE', `许可申请网络失败: ${err.message}`, null, true)
    }
    if (err instanceof DeviceRevokedError) {
      throw new PermitAcquireError('PERMIT_UNAVAILABLE', '设备 token 已失效或被撤销，无法申请许可', null, false)
    }
    if (err instanceof ApiError) {
      if (err.status >= 500) {
        throw new PermitAcquireError('PERMIT_UNAVAILABLE', `许可服务暂不可用（HTTP ${err.status}）: ${err.message}`, err.serverMessage ?? null, true)
      }
      // 4xx：服务端在同一事务中明确拒绝（claim/取消/epoch/额度/场景授权等）
      const serverCode = err.serverMessage ?? null
      const retryable = serverCode !== null && RETRYABLE_SERVER_CODES.has(serverCode)
      throw new PermitAcquireError('PERMIT_DENIED', `写动作许可被拒绝: ${err.message}`, serverCode, retryable)
    }
    throw err
  }

  // 服务端 deadline（墙钟 ISO）→ 剩余毫秒，再与本地单调上限取 min
  const parsed = Date.parse(resp.deadline_at)
  const remainingMs = Number.isFinite(parsed) ? parsed - Date.now() : 0
  if (remainingMs <= 0) {
    throw new PermitAcquireError('PERMIT_UNAVAILABLE', '许可到达时已过期（服务端 deadline 已过）', null, true)
  }
  const ttlMs = Math.min(remainingMs, capMs)
  return new WritePermitImpl(resp.permit_id, resp.permit_token, nowNs() + BigInt(Math.max(0, Math.round(ttlMs))) * 1_000_000n)
}
