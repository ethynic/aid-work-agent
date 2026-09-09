/**
 * v2 结果 result outbox（§5.3 / R14 / R21）。
 *
 * - v2 invocation 终态先原子写 result-outbox/<invocation_id>.json（tmp + rename），
 *   再发送 operation-result；**仅收到 2xx ACK 才删除条目**（R21：回执删除只认持久 ACK）。
 * - 404/409/其它确定性 4xx → 保留条目并标记 gave_up（停止主动重试，ERROR 日志含回执
 *   摘要白名单字段；条目永不删除，留待人工对账——服务端语义为「绑定通过即接纳迟到
 *   证据」，确定性 4xx 意味着绑定本身不成立，重投大概率不变）。
 * - 5xx/网络失败保留条目并指数退避重试；设备撤销 → 放弃本次（条目保留，fail-loud）。
 * - Runtime 启动时重投未 gave_up 的 pending 条目（用条目内原 claim 身份；重启后设备
 *   token 不变，语义等价于"经原 invocation/claim 身份重复回传"）；gave_up 条目跳过
 *   重投并输出汇总日志（供对账），永不删除。
 * - 条目内容是完整回传 payload——v2 路径按契约持久化 claim_token/permit_token
 *   （区别于旧 invocation 的"claim_token 仅内存"策略，仅此目录、权限跟随 runtime home）。
 */
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, readdirSync, renameSync, rmSync, unlinkSync, writeSync } from 'node:fs'
import path from 'node:path'
import type { ApiClient, OperationResultPayload } from './apiClient.js'
import { ApiError, DeviceRevokedError, NetworkError } from './apiClient.js'
import { logError, logInfo } from './log.js'

export interface OutboxEntry {
  invocation_id: string
  endpoint: 'operation-result'
  payload: OperationResultPayload
  created_at: string
  /** 已尝试发送次数（每次失败 +1，供排障与测试断言） */
  attempts: number
  last_attempt_at?: string
  last_error?: string
  /** 确定性 4xx 后置 true（R21）：停止主动重试、启动重投跳过、条目永不删除（供对账） */
  gave_up?: boolean
  gave_up_at?: string
  /** gave_up 原因（HTTP 状态 + 服务端错误码摘要，非敏感） */
  gave_up_reason?: string
}

export type OutboxOutcome =
  | 'acked' // 2xx，条目已删除（唯一删除路径）
  | 'gave-up' // 确定性 4xx（已标记 gave_up 保留）或设备撤销（保留，fail-loud）
  | 'retry-exhausted' // 有界重试用尽（网络/5xx），条目保留
  | 'shutdown' // Runtime 关闭中止，条目保留

export function resultOutboxDir(runtimeHome: string): string {
  return path.join(runtimeHome, 'result-outbox')
}

/** 指数退避等待，shutdown 时立即返回 false（invocationRunner 同语义共用） */
export async function backoffSleep(ms: number, shutdownSignal?: AbortSignal): Promise<boolean> {
  if (shutdownSignal?.aborted) return false
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(true), ms)
    shutdownSignal?.addEventListener('abort', () => {
      clearTimeout(timer)
      resolve(false)
    }, { once: true })
  })
}

export class ResultOutbox {
  constructor(readonly dir: string) {}

  private fileFor(invocationId: string): string {
    return path.join(this.dir, `${invocationId}.json`)
  }

  /** 原子写入：先写 <id>.json.tmp 再 rename（崩溃时最多残留 tmp，不产生半写条目） */
  save(invocationId: string, payload: OperationResultPayload): OutboxEntry {
    const entry: OutboxEntry = {
      invocation_id: invocationId,
      endpoint: 'operation-result',
      payload,
      created_at: new Date().toISOString(),
      attempts: 0,
    }
    this.writeEntry(entry)
    return entry
  }

  /** 失败后回写尝试计数（tmp+rename 原子） */
  markAttempt(invocationId: string, error?: string): void {
    const entry = this.load(invocationId)
    if (!entry) return
    entry.attempts += 1
    entry.last_attempt_at = new Date().toISOString()
    entry.last_error = error?.slice(0, 300)
    this.writeEntry(entry)
  }

  /** 确定性 4xx 后标记 gave_up（R21：停止主动重试、条目永不删除；tmp+rename 原子） */
  markGaveUp(invocationId: string, reason: string): void {
    const entry = this.load(invocationId)
    if (!entry || entry.gave_up) return
    entry.gave_up = true
    entry.gave_up_at = new Date().toISOString()
    entry.gave_up_reason = reason.slice(0, 300)
    this.writeEntry(entry)
  }

  /** 删除条目（幂等，不存在时静默） */
  remove(invocationId: string): void {
    try {
      unlinkSync(this.fileFor(invocationId))
    } catch {
      // ENOENT 视为已删除
    }
    // 同名残留 tmp 一并清理
    try {
      rmSync(this.fileFor(invocationId) + '.tmp', { force: true })
    } catch {
      // 忽略
    }
  }

  load(invocationId: string): OutboxEntry | null {
    try {
      return JSON.parse(readFileSync(this.fileFor(invocationId), 'utf8')) as OutboxEntry
    } catch {
      return null
    }
  }

  loadAll(): OutboxEntry[] {
    if (!existsSync(this.dir)) return []
    const entries: OutboxEntry[] = []
    for (const name of readdirSync(this.dir)) {
      if (!name.endsWith('.json')) continue
      try {
        entries.push(JSON.parse(readFileSync(path.join(this.dir, name), 'utf8')) as OutboxEntry)
      } catch (err) {
        logError(`result outbox 条目解析失败（保留待人工排查）: ${name}: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
    return entries
  }

  private writeEntry(entry: OutboxEntry): void {
    mkdirSync(this.dir, { recursive: true })
    const file = this.fileFor(entry.invocation_id)
    const tmp = file + '.tmp'
    // tmp 写入 + fsync 再 rename（掉电安全，与 journal 同标准；rename 后目录元数据
    // fsync 在 Windows 上无通用 API，接受进程崩溃安全、极端掉电窗口由 rename 原子性兜底）
    const fd = openSync(tmp, 'w')
    try {
      writeSync(fd, JSON.stringify(entry, null, 2))
      fsyncSync(fd)
    } finally {
      closeSync(fd)
    }
    renameSync(tmp, file)
  }
}

/** 回执摘要（ERROR 级终态收敛日志用）：只含非敏感字段，绝不含 claim_token/permit_token */
function receiptSummary(payload: OperationResultPayload): string {
  const p = payload as unknown as Record<string, unknown>
  return [
    `request_id=${String(p['request_id'] ?? '-')}`,
    `effect=${String(p['effect'] ?? '-')}`,
    `phase=${String(p['phase'] ?? '-')}`,
    `safe_to_retry=${String(p['safe_to_retry'] ?? '-')}`,
    `permit_id=${String(p['permit_id'] ?? '-')}`,
    `code=${String(p['code'] ?? '-')}`,
  ].join(' ')
}

export interface DeliverOptions {
  retryBaseMs?: number
  retryMaxMs?: number
  /** 最多尝试次数（默认无限制：终态回传重试到成功；启动重投用有限值避免拖死启动） */
  maxAttempts?: number
  shutdownSignal?: AbortSignal
  emit?: (message: string) => void
}

/**
 * 发送单个 outbox 条目（终态回传与启动重投共用，R21 语义）：
 * - 2xx → 删除条目（唯一删除路径）；
 * - 404/409/其它确定性 4xx → 保留条目并标记 gave_up（停止主动重试，条目永不删除），
 *   ERROR 日志含回执摘要白名单字段，供人工对账；
 * - 设备撤销 → 放弃保留（fail-loud，不标 gave_up——非服务端裁决）；
 * - 5xx/网络失败 → 指数退避重试（条目保留）。
 */
export async function deliverOutboxEntry(
  api: ApiClient,
  outbox: ResultOutbox,
  entry: OutboxEntry,
  opts: DeliverOptions = {},
): Promise<OutboxOutcome> {
  const base = opts.retryBaseMs ?? 1_000
  const max = opts.retryMaxMs ?? 30_000
  const maxAttempts = opts.maxAttempts ?? Number.POSITIVE_INFINITY
  const emit = opts.emit ?? logInfo
  let backoff = base
  let attempt = 0
  for (;;) {
    attempt += 1
    try {
      await api.operationResult(entry.invocation_id, entry.payload)
      outbox.remove(entry.invocation_id)
      emit(`invocation ${entry.invocation_id} v2 终态已 ACK（operation-result），outbox 条目已删除`)
      return 'acked'
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        // 确定性 4xx（403/404/409/422 等）：绑定/payload 不被接受，重试大概率不变——
        // 保留条目并标记 gave_up（R21：永不删除，停止主动重试，留待人工对账）
        outbox.markAttempt(entry.invocation_id, err.message)
        outbox.markGaveUp(entry.invocation_id, `HTTP ${err.status}${err.serverMessage ? ` ${err.serverMessage}` : ''}`)
        logError(
          `invocation ${entry.invocation_id} v2 终态被云端确定性拒绝（HTTP ${err.status}${err.serverMessage ? ` ${err.serverMessage}` : ''}），条目保留并标记 gave_up（停止主动重试、永不删除，待人工对账）；回执摘要: ${receiptSummary(entry.payload)}；原文: ${err.message}`,
        )
        return 'gave-up'
      }
      if (err instanceof DeviceRevokedError) {
        outbox.markAttempt(entry.invocation_id, err.message)
        logError(`invocation ${entry.invocation_id} v2 终态未能回传：设备已撤销（outbox 条目保留，fail-loud）`)
        return 'gave-up'
      }
      // 网络失败 / 5xx：保留条目退避重试
      outbox.markAttempt(entry.invocation_id, err instanceof Error ? err.message : String(err))
      if (attempt >= maxAttempts) {
        logError(`invocation ${entry.invocation_id} v2 终态重投 ${attempt} 次未成功（outbox 条目保留，稍后再投）`)
        return 'retry-exhausted'
      }
      emit(`v2 终态回传失败（网络/服务暂不可用），${backoff}ms 后重试`)
      const proceed = await backoffSleep(backoff, opts.shutdownSignal)
      if (!proceed) {
        logError(`invocation ${entry.invocation_id} v2 终态未能回传（Runtime 关闭中，outbox 条目保留）`)
        return 'shutdown'
      }
      backoff = Math.min(backoff * 2, max)
    }
  }
}

/** 启动重投结果汇总（gave_up 条目跳过重投，仅计入对账日志） */
export interface ReplaySummary {
  /** 2xx ACK 且已删除 */
  acked: number
  /** 本次重投中被确定性 4xx 拒绝、新标记 gave_up 的条目（保留在磁盘） */
  rejected: number
  /** 瞬态失败仍待重投（网络/5xx/关闭中止/设备撤销）的条目（保留在磁盘） */
  kept: number
  /** 启动时已处于 gave_up 状态、被跳过重投的条目（保留在磁盘，供对账） */
  skippedGaveUp: number
}

/**
 * 启动时重投未 gave_up 的 pending 条目（用原 claim 身份）。gave_up 条目跳过重投并输出
 * 汇总日志（供对账），永不删除。单条失败不影响其余条目；有界尝试后仍失败的条目保留在
 * 磁盘（下次启动/人工介入再收敛），不阻塞主循环启动。
 */
export async function replayPendingOutbox(
  api: ApiClient,
  outbox: ResultOutbox,
  opts: DeliverOptions = {},
): Promise<ReplaySummary> {
  const pending = outbox.loadAll()
  if (pending.length === 0) return { acked: 0, rejected: 0, kept: 0, skippedGaveUp: 0 }
  const replayable = pending.filter((entry) => !entry.gave_up)
  const skipped = pending.filter((entry) => entry.gave_up)
  if (skipped.length > 0) {
    // 汇总日志（供对账）：逐条列出跳过条目与回执摘要白名单字段（不含 claim/permit token）
    logInfo(`result outbox 启动重投：${pending.length} 条 pending，其中 ${skipped.length} 条已 gave_up（跳过重投、条目保留待对账）`)
    for (const entry of skipped) {
      logInfo(
        `result outbox gave_up 条目（跳过）invocation=${entry.invocation_id} attempts=${entry.attempts}` +
          ` reason=${entry.gave_up_reason ?? '-'} 回执摘要: ${receiptSummary(entry.payload)}`,
      )
    }
  } else {
    logInfo(`result outbox 启动重投：${pending.length} 条 pending`)
  }
  if (replayable.length === 0) return { acked: 0, rejected: 0, kept: 0, skippedGaveUp: skipped.length }
  let acked = 0
  let rejected = 0
  let kept = 0
  for (const entry of replayable) {
    try {
      const outcome = await deliverOutboxEntry(api, outbox, entry, opts)
      // gave-up 二义（确定性拒绝已标记 gave_up / 设备撤销未标记）：以条目磁盘状态归类
      if (outcome === 'acked') acked += 1
      else if (outbox.load(entry.invocation_id)?.gave_up === true) rejected += 1
      else kept += 1
    } catch (err) {
      // deliverOutboxEntry 内部已分类网络/HTTP 错误；此处防御未知异常不让单条拖垮启动
      kept += 1
      logError(`result outbox 重投异常（条目保留）invocation=${entry.invocation_id}: ${err instanceof Error ? err.message : String(err)}`)
    }
  }
  return { acked, rejected, kept, skippedGaveUp: skipped.length }
}
