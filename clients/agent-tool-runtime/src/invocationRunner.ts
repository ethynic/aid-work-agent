/**
 * 单 invocation 执行器：Provider 路由/门禁 → started → desktopCheck → 桌面锁仲裁内
 * （v2 写动作：取消复验 → write-authorize 许可 → 许可防御检查 → journal 落盘 → 防御复验
 * → MCP call，R20：许可/journal 全部在锁内，锁等待期不得申请许可）（进度转发 + 取消轮询）
 * → result（v2 走 operation-result + result outbox）。
 *
 * 关键策略（实施规格 §3/§4/§5/§6 + R20/R21）：
 * - invocation 级 provider 路由：无 provider 字段 → boss（旧行为不变）；未安装 → PROVIDER_NOT_AVAILABLE
 * - v2 invocation 能力门禁：manifest 未协商 v2 → PROTOCOL_NOT_SUPPORTED，不降级旧 tool
 * - v2 写动作（manifest 判写）：provider 调用前申请短期许可（拿不到/过期禁止执行，
 *   effect none）；许可到手先 journal + fsync 再执行（fsync 失败禁止执行）
 * - v2 终态先落 result outbox 再发 operation-result；仅 2xx ACK 删条目，确定性 4xx
 *   保留并标记 gave_up（R21：停止主动重试、永不删除），断网退避重试
 * - 旧 invocation 完全走 /result：payload 逐键显式构造，与历史字节一致（含计费语义）
 * - 全部 Provider tool call 持桌面资源锁执行（含旧 boss 链路）；等待超时 → DESKTOP_RESOURCE_BUSY
 * - 执行中每 2s 或每个业务进度回传 progress；ack.cancel=true → 协作式中止（AbortController）
 * - 写动作断线：本地继续完成原子动作，恢复后回传终态；无法证明效果 → EXECUTION_UNKNOWN
 * - 旧链路 claim_token 仅内存持有；v2 按契约将 claim/permit 身份持久于 result outbox（仅该目录）
 */
import type { ApiClient, ClaimedInvocation, InvocationResultPayload, OperationResultPayload } from './apiClient.js'
import { ApiError, DeviceRevokedError, NetworkError } from './apiClient.js'
import { getProviderManifest, isToolAllowedFor, isWriteToolFor, type ProviderManifest } from './providers.js'
import type { ProviderSet } from './providerManager.js'
import { ProviderCrashError } from './providerManager.js'
import { DesktopLockTimeoutError, desktopLockName, withDesktopLock } from './desktopLock.js'
import { appendJournalEntry, JournalWriteError } from './journal.js'
import { backoffSleep, deliverOutboxEntry, type OutboxEntry, ResultOutbox, resultOutboxDir } from './resultOutbox.js'
import { runtimeHomeDir } from './config.js'
import { logError, logInfo } from './log.js'
import { acquireWritePermit, PermitAcquireError, type WritePermit } from './writeAuthorize.js'

export interface RunnerDeps {
  api: ApiClient
  providers: ProviderSet
  /** 桌面可交互检测（默认由调用方注入真实实现；测试注入 mock） */
  desktopCheck: () => Promise<boolean>
  /** 桌面资源锁 resource_key（必填，机器作用域派生（R24：hostname|username|session）；缺失/为空即接线错误，启动即抛） */
  desktopResourceKey: string
  /** 仲裁等待超时（默认 desktopLock DESKTOP_LOCK_DEFAULT_TIMEOUT_MS） */
  desktopLockTimeoutMs?: number
  /** 进度续租间隔（默认 2000ms，规格 §3） */
  progressIntervalMs?: number
  /** 终态/started 网络重试基数（默认 1000ms） */
  retryBaseMs?: number
  retryMaxMs?: number
  /** shutdown 信号：触发协作式中止 + 终态回传限时 */
  shutdownSignal?: AbortSignal
  /** v2 数据根目录（journal/ 与 result-outbox/ 所在；默认 runtimeHomeDir()） */
  runtimeDataDir?: string
  /** v2 结果 outbox 实例（PollLoop 启动重投与 runner 共用；缺省按 runtimeDataDir 派生） */
  resultOutbox?: ResultOutbox
  /** 本地许可有效期上限 ms（默认 writeAuthorize LOCAL_PERMIT_CAP_MS=90s；测试注入短值） */
  permitLocalCapMs?: number
  /** 测试注入的 Provider manifest 覆盖（v2 契约样例专用；生产不传，走 TRUSTED_MANIFESTS） */
  manifests?: Record<string, ProviderManifest>
  onEvent?: (message: string) => void
}

interface FinalResult {
  success: boolean
  code?: string
  message?: string
  effect?: string
  data?: Record<string, unknown>
  retryable?: boolean
  /** 以下为 v2 operation-result 专用字段（旧 /result 路径不携带——payload 显式构造） */
  phase?: string
  safe_to_retry?: boolean
  evidence_ref?: string
  permit_id?: string
  permit_token?: string
}

/** 进度转发器：业务进度立即发 + 每 progressIntervalMs 续租；串行化发送，cancel 透传 */
class ProgressForwarder {
  private last: { stage?: string; current?: number; total?: number; message?: string } = { stage: 'running' }
  private lastSentAt = 0
  private chain: Promise<void> = Promise.resolve()
  private timer: ReturnType<typeof setInterval> | null = null
  private cloudOffline = false
  cancelled = false

  constructor(
    private readonly api: ApiClient,
    private readonly invocationId: string,
    private readonly claimToken: string,
    private readonly intervalMs: number,
    private readonly onCancel: () => void,
    private readonly emit: (msg: string) => void,
  ) {}

  start(): void {
    this.timer = setInterval(() => {
      if (Date.now() - this.lastSentAt >= this.intervalMs) {
        this.push({})
      }
    }, this.intervalMs)
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
  }

  /** 更新最近进度并入队一次发送（串行链保证不并发、不乱序） */
  push(update: { stage?: string; current?: number; total?: number; message?: string }): void {
    this.last = { ...this.last, ...update }
    this.chain = this.chain.then(() => this.doSend())
  }

  /** 等待所有已入队进度发送完成（终态前 flush） */
  async flush(): Promise<void> {
    await this.chain
  }

  private async doSend(): Promise<void> {
    try {
      const ack = await this.api.progress(this.invocationId, {
        claim_token: this.claimToken,
        stage: this.last.stage,
        current: this.last.current,
        total: this.last.total,
        message: this.last.message,
      })
      this.lastSentAt = Date.now()
      if (this.cloudOffline) {
        this.cloudOffline = false
        this.emit('云端连接已恢复（进度回传）')
      }
      if (ack.cancel && !this.cancelled) {
        this.cancelled = true
        this.emit('收到云端取消请求，协作式中止')
        this.onCancel()
      }
    } catch (err) {
      if (err instanceof NetworkError || err instanceof DeviceRevokedError) {
        // 断线策略：进度丢失可容忍，继续本地执行，终态会重试回传
        if (!this.cloudOffline) {
          this.cloudOffline = true
          this.emit('云端连接中断（进度暂无法回传，本地继续执行）')
        }
        return
      }
      if (err instanceof ApiError && err.status === 404) {
        // 租约过期/状态不允许：云端已不再接受进度，停止续租（终态大概率也被拒，但仍尝试）
        this.emit('云端拒绝进度回传（租约可能过期），停止续租')
        this.stop()
        return
      }
      logError(`进度回传失败: ${err instanceof Error ? err.message : String(err)}`)
    }
  }
}

export async function runInvocation(inv: ClaimedInvocation, deps: RunnerDeps): Promise<void> {
  const emit = deps.onEvent ?? logInfo
  const retryBase = deps.retryBaseMs ?? 1_000
  const retryMax = deps.retryMaxMs ?? 30_000

  // 桌面锁 resource_key 必填（缺失即接线错误，启动即抛——不静默落到共享默认值）
  if (!deps.desktopResourceKey) {
    throw new Error('RunnerDeps.desktopResourceKey 缺失：桌面资源锁 resource_key 必须由调用方（机器作用域派生）显式传入')
  }

  const protocolVersion = inv.arguments['protocol_version']
  const isV2Invocation = (typeof protocolVersion === 'number' && protocolVersion === 2) || inv.tool_name.endsWith('_v2')
  const requestId = typeof inv.arguments['request_id'] === 'string' ? (inv.arguments['request_id'] as string) : ''
  const v2StrArg = (key: string): string | undefined => {
    const v = inv.arguments[key]
    return typeof v === 'string' ? v : undefined
  }

  // v2 结果 outbox：与 PollLoop 启动重投共用实例（deps 注入），缺省按数据目录派生
  const dataDir = deps.runtimeDataDir ?? runtimeHomeDir()
  const outbox = isV2Invocation ? (deps.resultOutbox ?? new ResultOutbox(resultOutboxDir(dataDir))) : null

  /** 旧链路终态回传（/result）：payload 逐键显式构造——键集与顺序和改造前一致，
   *  FinalResult 的 v2 专用字段绝不进入旧路径（逐字节兼容，含计费语义） */
  const sendFinalResultLegacy = async (final: FinalResult): Promise<void> => {
    const payload: InvocationResultPayload = {
      claim_token: inv.claim_token,
      success: final.success,
      code: final.code,
      message: final.message,
      effect: final.effect,
      data: final.data,
      retryable: final.retryable,
    }
    let backoff = retryBase
    for (;;) {
      try {
        await deps.api.result(inv.invocation_id, payload)
        emit(`invocation ${inv.invocation_id} 终态已回传 success=${final.success} code=${final.code ?? '-'} effect=${final.effect ?? '-'}`)
        return
      } catch (err) {
        if (err instanceof ApiError) {
          logError(`invocation ${inv.invocation_id} 终态被云端拒绝（HTTP ${err.status}），放弃重试: ${err.message}`)
          return
        }
        if (err instanceof DeviceRevokedError) {
          logError(`invocation ${inv.invocation_id} 终态回传失败：设备已撤销，结果丢失（fail-loud）`)
          return
        }
        emit(`终态回传失败（网络），${backoff}ms 后重试`)
        const proceed = await backoffSleep(backoff, deps.shutdownSignal)
        if (!proceed) {
          logError(`invocation ${inv.invocation_id} 终态未能回传（Runtime 关闭中），结果丢失（fail-loud）`)
          return
        }
        backoff = Math.min(backoff * 2, retryMax)
      }
    }
  }

  /** v2 终态回传（operation-result）：先原子落 result outbox 再发送；仅 2xx ACK 删条目（R21），
   *  确定性 4xx 保留条目并标记 gave_up（停止主动重试、永不删除），5xx/断网退避重试
   *  （详见 resultOutbox.deliverOutboxEntry） */
  const sendFinalResultV2 = async (final: FinalResult): Promise<void> => {
    const payload: OperationResultPayload = {
      claim_token: inv.claim_token,
      request_id: requestId,
      permit_id: final.permit_id,
      permit_token: final.permit_token,
      effect: final.effect ?? 'none',
      phase: final.phase ?? 'prepared',
      safe_to_retry: final.safe_to_retry ?? final.retryable ?? false,
      evidence_ref: final.evidence_ref,
      success: final.success,
      code: final.code,
      message: final.message,
      data: final.data,
      retryable: final.safe_to_retry ?? final.retryable ?? false,
    }
    let entry: OutboxEntry | null = null
    try {
      outbox!.save(inv.invocation_id, payload)
      entry = outbox!.load(inv.invocation_id)
    } catch (err) {
      logError(`invocation ${inv.invocation_id} v2 outbox 落盘失败: ${err instanceof Error ? err.message : String(err)}`)
    }
    if (!entry) {
      // outbox 不可写（磁盘/目录异常）：fail-loud 直发一次，结果可能丢失，但不静默吞终态
      logError(`invocation ${inv.invocation_id} v2 终态无本地 outbox 保障，直接发送`)
      try {
        await deps.api.operationResult(inv.invocation_id, payload)
        emit(`invocation ${inv.invocation_id} v2 终态已直发成功（无 outbox 保障）`)
      } catch (err) {
        logError(`invocation ${inv.invocation_id} v2 终态直发失败: ${err instanceof Error ? err.message : String(err)}`)
      }
      return
    }
    await deliverOutboxEntry(deps.api, outbox!, entry, {
      retryBaseMs: retryBase,
      retryMaxMs: retryMax,
      shutdownSignal: deps.shutdownSignal,
      emit,
    })
  }

  const sendFinalResult = isV2Invocation ? sendFinalResultV2 : sendFinalResultLegacy

  // 0. Provider 路由：invocation 级 provider_key（服务端旧数据无该字段 → 设备默认 boss，现状不变）
  const providerKey = inv.provider ?? 'boss-recruiting'
  const manifest = deps.manifests?.[providerKey] ?? getProviderManifest(providerKey)
  if (!manifest || !deps.providers.has(providerKey)) {
    emit(`invocation ${inv.invocation_id} provider=${providerKey} 未安装或未配置入口，拒绝执行`)
    await sendFinalResult({
      success: false,
      code: 'PROVIDER_NOT_AVAILABLE',
      message: `Provider ${providerKey} 未安装或未配置入口`,
      effect: 'none',
      retryable: true,
      phase: 'prepared',
      safe_to_retry: true,
    })
    return
  }
  const writeTool = isWriteToolFor(manifest, inv.tool_name)

  // 0.5 v2 能力门禁：v2 invocation 只派给协商通过的 Provider；能力缺失直接拒绝，不降级旧 tool
  if (isV2Invocation && !(manifest.protocol_version >= 2 && manifest.shared_lock_capable)) {
    emit(
      `invocation ${inv.invocation_id} provider=${providerKey} 不支持 v2 操作协议` +
        `（protocol_version=${manifest.protocol_version} shared_lock_capable=${manifest.shared_lock_capable}），拒绝执行`,
    )
    await sendFinalResult({
      success: false,
      code: 'PROTOCOL_NOT_SUPPORTED',
      message: `Provider ${providerKey} 不支持 v2 操作协议（manifest protocol_version=${manifest.protocol_version}, shared_lock_capable=${manifest.shared_lock_capable}）`,
      effect: 'none',
      retryable: false,
      phase: 'prepared',
      safe_to_retry: false,
    })
    return
  }

  // 1. manifest 校验：tool_name 不在该 Provider 受信清单 → 直接回 TOOL_NOT_ALLOWED（规格 §4 决策）
  if (!isToolAllowedFor(manifest, inv.tool_name)) {
    emit(`invocation ${inv.invocation_id} tool=${inv.tool_name} 不在受信 manifest，拒绝执行`)
    await sendFinalResult({
      success: false,
      code: 'TOOL_NOT_ALLOWED',
      message: `工具 ${inv.tool_name} 不在本地受信 manifest 清单内`,
      effect: 'none',
      retryable: false,
      phase: 'prepared',
      safe_to_retry: false,
    })
    return
  }

  // 2. started（幂等，网络重试；失败则放弃执行——写动作未开始无副作用，租约过期云端回收）
  {
    let backoff = retryBase
    for (;;) {
      try {
        await deps.api.started(inv.invocation_id, inv.claim_token)
        break
      } catch (err) {
        if (err instanceof ApiError) {
          logError(`invocation ${inv.invocation_id} started 被云端拒绝（HTTP ${err.status}），放弃执行: ${err.message}`)
          return
        }
        if (err instanceof DeviceRevokedError) {
          logError(`invocation ${inv.invocation_id} started 失败：设备已撤销`)
          return
        }
        emit(`started 失败（网络），${backoff}ms 后重试`)
        const proceed = await backoffSleep(backoff, deps.shutdownSignal)
        if (!proceed) return
        backoff = Math.min(backoff * 2, retryMax)
      }
    }
  }
  emit(`invocation ${inv.invocation_id} 已开始执行 tool=${inv.tool_name}`)

  // 3. 锁屏检测（写动作前置；规格 §6）
  if (writeTool) {
    const interactive = await deps.desktopCheck()
    if (!interactive) {
      await sendFinalResult({
        success: false,
        code: 'DESKTOP_NOT_INTERACTIVE',
        message: 'Windows 桌面锁屏或不可交互，请解锁后重试',
        effect: 'none',
        retryable: true,
        phase: 'prepared',
        safe_to_retry: true,
      })
      return
    }
  }

  // 4. MCP 调用（进度转发 + 取消 + shutdown 中止 + 桌面资源仲裁）
  const provider = deps.providers.get(providerKey)
  const lockName = desktopLockName(deps.desktopResourceKey)
  const abortController = new AbortController()
  const onShutdownAbort = () => abortController.abort()
  deps.shutdownSignal?.addEventListener('abort', onShutdownAbort, { once: true })

  const forwarder = new ProgressForwarder(
    deps.api,
    inv.invocation_id,
    inv.claim_token,
    deps.progressIntervalMs ?? 2_000,
    () => abortController.abort(),
    emit,
  )
  // forwarder 先于锁启动：锁等待期间持续续租租约并感知取消（R20 取消状态复验的数据来源）
  forwarder.start()

  let permit: WritePermit | null = null
  /** v2 写动作锁内前置失败终态（未开始输入，effect none）；终态回传在锁释放后进行 */
  let lockPreabort: FinalResult | null = null
  /** 前置失败终态构造：未执行到输入一律 none/prepared；已签发许可附身份（服务端按
   *  R22 ①「effect=none 且 phase=prepared → release」释放额度，避免只剩清扫一条路） */
  const preabortFinal = (final: FinalResult): FinalResult => ({
    effect: 'none',
    phase: 'prepared',
    ...final,
    ...(permit ? { permit_id: permit.permitId, permit_token: permit.permitToken } : {}),
  })

  let settled: Record<string, unknown> | null = null
  let callError: Error | null = null
  let lockBusy = false
  try {
    // 桌面资源仲裁入口：全部 Provider（含旧 boss 链路）的 tool call 都持锁执行，不改 MCP 契约；
    // 等待超时按未提交处理回 DESKTOP_RESOURCE_BUSY（绝不复用旧 'BUSY' 码，避免误触发云端旧弹层自愈）。
    // v2 写动作的许可/journal 全部移入锁内（R20）：锁等待最长 120s 会耗尽 90s 本地许可有效期，
    // 等待期间不得发起 writeAuthorize；锁内顺序固定为 取消复验 → writeAuthorize → 许可防御检查
    //（单调时钟）→ journal fsync → 防御复验 → provider 调用，任一失败禁止执行（effect none）
    settled = await withDesktopLock(
      lockName,
      async () => {
        if (isV2Invocation && writeTool) {
          // 取消状态复验（R20）：flush 让等待期最后一批进度 ACK 先落地，再读取消标志；
          // 已被取消/关闭则不申请许可、不执行（effect none，可重试语义与取消分支一致收敛）
          await forwarder.flush()
          if (forwarder.cancelled || deps.shutdownSignal?.aborted) {
            lockPreabort = preabortFinal({
              success: false,
              code: 'CANCELLED',
              message: '云端请求取消（许可申请前复验），未开始执行',
              retryable: true,
              safe_to_retry: true,
            })
            return null
          }
          try {
            permit = await acquireWritePermit(
              deps.api,
              inv.invocation_id,
              {
                claim_token: inv.claim_token,
                request_id: requestId,
                target_version: v2StrArg('target_version'),
                payload_hash: v2StrArg('payload_hash'),
              },
              { localCapMs: deps.permitLocalCapMs },
            )
          } catch (err) {
            if (err instanceof PermitAcquireError) {
              emit(
                `invocation ${inv.invocation_id} 写动作许可未获得（${err.code}${err.serverCode ? `/${err.serverCode}` : ''}），不执行`,
              )
              lockPreabort = preabortFinal({
                success: false,
                code: err.code,
                message: err.message,
                retryable: err.retryable,
                safe_to_retry: err.retryable,
              })
              return null
            }
            throw err
          }
          emit(`invocation ${inv.invocation_id} 已获得写动作许可 permit_id=${permit.permitId}（本地有效期 ${Math.round(permit.msRemaining())}ms）`)
          // 许可到手：先做单调时钟防御检查，再把 permit_id + payload_hash 持久进 journal
          // （含 fsync）再执行；fsync 失败 → 禁止执行（无证据链宁可不发送）
          if (!permit.isValid()) {
            emit(`invocation ${inv.invocation_id} 写动作许可在输入开始前已过期（本地单调时钟），不执行`)
            lockPreabort = preabortFinal({
              success: false,
              code: 'PERMIT_UNAVAILABLE',
              message: '写动作许可在输入开始前已过期（本地单调时钟上限），不执行',
              retryable: true,
              safe_to_retry: true,
            })
            return null
          }
          try {
            appendJournalEntry(dataDir, {
              ts: new Date().toISOString(),
              invocation_id: inv.invocation_id,
              request_id: requestId,
              permit_id: permit.permitId,
              payload_hash: v2StrArg('payload_hash'),
              operation: v2StrArg('operation'),
              phase: 'may_have_started',
            })
          } catch (err) {
            if (err instanceof JournalWriteError) {
              logError(`invocation ${inv.invocation_id} ${err.message}`)
              lockPreabort = preabortFinal({
                success: false,
                code: 'JOURNAL_WRITE_FAILED',
                message: err.message,
                retryable: false,
                safe_to_retry: false,
              })
              return null
            }
            throw err
          }
          // journal fsync 后、provider 调用前的最后一次防御复验（单调时钟；过期即中止，
          // provider 零调用）
          if (!permit.isValid()) {
            emit(`invocation ${inv.invocation_id} 写动作许可在 journal 落盘后已过期（本地单调时钟），不执行`)
            lockPreabort = preabortFinal({
              success: false,
              code: 'PERMIT_UNAVAILABLE',
              message: '写动作许可在 journal 落盘后已过期（本地单调时钟上限），不执行',
              retryable: true,
              safe_to_retry: true,
            })
            return null
          }
        }
        // v2 写动作把 permit handle 注入 Provider 调用参数（v2 契约：受控操作接收本地已校验许可）
        const callArgs: Record<string, unknown> = permit
          ? { ...inv.arguments, permit_id: permit.permitId, permit_token: permit.permitToken }
          : inv.arguments
        return await provider.callTool(inv.tool_name, callArgs, {
          signal: abortController.signal,
          onProgress: (p) => {
            const stage = extractStage(p.message)
            forwarder.push({
              stage,
              current: typeof p.progress === 'number' ? Math.round(p.progress) : undefined,
              total: typeof p.total === 'number' ? Math.round(p.total) : undefined,
              message: p.message,
            })
          },
        })
      },
      { timeoutMs: deps.desktopLockTimeoutMs, signal: abortController.signal },
    )
  } catch (err) {
    if (err instanceof DesktopLockTimeoutError) {
      lockBusy = true
      logError(`invocation ${inv.invocation_id} 桌面资源锁等待超时: ${err.message}`)
    } else {
      callError = err instanceof Error ? err : new Error(String(err))
    }
  } finally {
    forwarder.stop()
    deps.shutdownSignal?.removeEventListener('abort', onShutdownAbort)
  }
  await forwarder.flush()

  // 锁内前置失败（取消复验/许可/防御检查/journal）优先于执行结果映射：未开始输入，effect none
  if (lockPreabort) {
    await sendFinalResult(lockPreabort)
    return
  }

  // 5. 终态映射（v2 写动作：拿不到可信结果一律 unknown，不可漏为 retryable——§5.3）
  // 执行后 v2 失败回执附 permit 身份（服务端 consume 结算额度）；未执行到输入的失败不附。
  // （permit 在锁内闭包赋值，重读一次恢复联合类型——闭包赋值不参与流分析）
  const finalPermit = permit as WritePermit | null
  const permitFields = finalPermit
    ? { permit_id: finalPermit.permitId, permit_token: finalPermit.permitToken }
    : {}
  const strField = (v: unknown): string | undefined => (typeof v === 'string' ? v : undefined)

  if (forwarder.cancelled) {
    // 云端取消优先：协作式中止后回 cancelled 终态（保留已产生的局部 effect）
    await sendFinalResult({
      success: false,
      code: 'CANCELLED',
      message: '云端请求取消，已协作式中止',
      effect: strField(settled?.['effect']) ?? (isV2Invocation && writeTool ? 'unknown' : 'none'),
      retryable: false,
      phase: strField(settled?.['phase']) ?? (isV2Invocation && writeTool ? 'unknown' : 'prepared'),
      safe_to_retry: false,
      ...permitFields,
    })
    return
  }
  if (deps.shutdownSignal?.aborted) {
    // v1 保持 retryable:true（与 HEAD 一致）；v2 写动作未拿到可信结果一律 unknown 且
    // 不可自动重试（§5.3「取消/shutdown 分支未拿到可信结果时写工具一律 unknown」）
    const shutdownRetryable = !(isV2Invocation && writeTool)
    await sendFinalResult({
      success: false,
      code: 'CANCELLED',
      message: 'Runtime 关闭，任务已中止',
      effect: strField(settled?.['effect']) ?? (isV2Invocation && writeTool ? 'unknown' : 'none'),
      retryable: shutdownRetryable,
      phase: strField(settled?.['phase']) ?? (isV2Invocation && writeTool ? 'unknown' : 'prepared'),
      safe_to_retry: shutdownRetryable,
      ...permitFields,
    })
    return
  }
  if (lockBusy) {
    // 仲裁等待超时：动作未开始、无副作用 → effect=none，可重试（R20：许可申请在锁内，
    // 等待超时意味着 writeAuthorize 从未发起，无许可需要收敛）
    await sendFinalResult({
      success: false,
      code: 'DESKTOP_RESOURCE_BUSY',
      message: '等待桌面资源锁超时（其它任务正在使用本机桌面），稍后重试',
      effect: 'none',
      retryable: true,
      phase: 'prepared',
      safe_to_retry: true,
    })
    return
  }
  if (callError) {
    if (callError instanceof ProviderCrashError) {
      // Provider 崩溃：写动作无法证明效果 → EXECUTION_UNKNOWN；只读 → INTERNAL_ERROR（规格 §4）
      if (writeTool) {
        await sendFinalResult({
          success: false,
          code: 'EXECUTION_UNKNOWN',
          message: `Provider 执行中崩溃，无法确认动作效果: ${callError.message}`,
          effect: 'unknown',
          retryable: false,
          phase: 'unknown',
          safe_to_retry: false,
          ...permitFields,
        })
      } else {
        await sendFinalResult({
          success: false,
          code: 'INTERNAL_ERROR',
          message: `Provider 执行中崩溃: ${callError.message}`,
          effect: 'none',
          retryable: true,
          phase: 'prepared',
          safe_to_retry: true,
        })
      }
      return
    }
    await sendFinalResult({
      success: false,
      code: 'INTERNAL_ERROR',
      message: `Provider 调用失败: ${callError.message}`,
      effect: writeTool ? 'unknown' : 'none',
      retryable: !writeTool,
      phase: writeTool ? 'unknown' : 'prepared',
      safe_to_retry: !writeTool,
      ...permitFields,
    })
    return
  }

  const r = settled ?? {}
  const data: Record<string, unknown> = { ...(r['data'] as Record<string, unknown> | undefined) }
  if (typeof r['run_id'] === 'string') data['run_id'] = r['run_id']
  // v1（旧 /result）：Provider 结果缺 effect 时键缺省——与 HEAD 逐字节一致，不注入默认值
  const rawEffect = strField(r['effect'])
  await sendFinalResult({
    success: Boolean(r['success']),
    code: strField(r['code']),
    message: strField(r['message']),
    effect: isV2Invocation ? sanitizeEffect(rawEffect, writeTool) : rawEffect,
    data: Object.keys(data).length > 0 ? data : undefined,
    retryable: typeof r['retryable'] === 'boolean' ? r['retryable'] : undefined,
    // v2 Provider 未按契约回报 phase 时保守收敛（phase 仅 v2 路径携带）
    phase: sanitizePhase(strField(r['phase']), writeTool),
    safe_to_retry: typeof r['safe_to_retry'] === 'boolean' ? r['safe_to_retry'] : undefined,
    evidence_ref: strField(r['evidence_ref']),
    ...permitFields,
  })
}

/** 服务端 effect 枚举（R10）；v2 回执非法/缺失 effect 一律收敛 unknown（防 422 永久拒绝） */
const DELIVERY_EFFECTS = new Set(['none', 'applied', 'unknown'])
/** 服务端 phase 枚举（R10）；v2 回执非法/缺失 phase 按写动作保守收敛 */
const OPERATION_PHASES = new Set(['prepared', 'may_have_started', 'verified', 'unknown'])

function sanitizeEffect(effect: string | undefined, writeTool: boolean): string {
  if (effect !== undefined && DELIVERY_EFFECTS.has(effect)) return effect
  // 缺失按写动作未知处理；非法值（如 v1 残留 'partial'）一律 unknown——不可判 none
  return writeTool || effect !== undefined ? 'unknown' : 'none'
}

function sanitizePhase(phase: string | undefined, writeTool: boolean): string {
  if (phase !== undefined && OPERATION_PHASES.has(phase)) return phase
  return writeTool ? 'unknown' : 'prepared'
}

/** 从 Provider 进度文案提取 stage（boss 约定 "stage message" 首词为 stage） */
function extractStage(message?: string): string | undefined {
  if (!message) return undefined
  const first = message.trim().split(/\s+/)[0]
  return first || undefined
}
