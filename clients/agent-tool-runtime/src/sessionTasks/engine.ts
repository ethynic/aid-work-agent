/**
 * 会话任务引擎（C2，设计 §3/§6/§7/§8）。
 *
 * Runtime 常驻任务执行器：claim/renew 与既有 pollLoop 共存（不替代），等待中
 * 的会话不占桌面锁、不占模型调用；一个任务等待不阻塞其他任务（ReadyQueue
 * 公平调度，单动作单元让位）。
 *
 * 不变量：
 * - 每个本地状态迁移先落 SessionStore（fsync 成功）再推进内存；重启由日志
 *   确定性回放恢复（回放失败 → blocked，禁止空状态启动）。
 * - 观察动作在共享桌面锁内执行（C2 未验证只读并发前不放开）；等待模型/客户
 *   时不持锁（决策请求为纯网络调用）。
 * - 决策在飞上限默认 2（客户端侧限制，不冒充服务端保障）。
 * - 未 ACK 事件退避重投（1s→30s）；不因云端终态删除本地事实。
 * - 普通日志不得含消息正文/密钥（emit 只带 ID/相位/结果码）。
 * - 控制更新（renew / 事件 ACK / 恢复接续）统一经任务级串行入口
 *   applyControlUpdate：旧响应按 control_epoch 拒绝，不回退已应用的控制状态、
 *   不重开门禁（审计九轮 P1）；控制元数据的失败重试同样在链内执行——不允许
 *   链外写盘（审计十轮：链外旧重试会把磁盘覆盖回旧控制代）。
 */
import { randomUUID } from 'node:crypto'
import type { ApiClient, ClaimedInvocation, SessionTaskControlAck, SessionTaskPrepareSendResult, SessionTaskTargetedClaim } from '../apiClient.js'
import { ApiError, NetworkError } from '../apiClient.js'
import { ReadyQueue, type QueueKind } from './readyQueue.js'
import { SessionStore, SessionStoreCorruptError, sessionTaskDir, type AssignmentMeta, type SessionCrypto, type ReplayedEvent } from './sessionStore.js'
import { enforceRetention } from './retention.js'

export type TaskPhase =
  | 'ready' // 已领取，待观察
  | 'observing' // 有活跃批次在聚合（静默窗口计时）
  | 'decision_pending' // 决策已提交，等待云端（纯网络轮询，不占锁/模型）
  | 'send_ready' // 决策 ready 且 action=reply：待 prepare-send 物化底座执行单元
  | 'executing' // C3：底座 invocation 已物化，定向 claim 后经既有 v2 执行链发送
  | 'waiting_peer' // 已回复，等对方新消息（让出资源的持久状态）
  | 'sync_pending' // 有未 ACK 事件待同步（可与任意相位叠加，由独立通道冲刷）
  | 'blocked'

/** 观察请求（session_observer_v1 冻结契约） */
export interface ObserverWatermark {
  last_local_message_id: string | null
  window_fingerprint: string
}

export interface ObserverMessage {
  sender: 'peer' | 'self' | 'system'
  text: string
  local_message_id: string
  source_evidence_ref: string
}

export interface ObserverResult {
  observation_id: string
  account_identity_version: number
  conversation_binding_id: string
  binding_version: number
  observed_at: string
  coverage: 'complete_window' | 'gap' | 'unavailable'
  ordered_messages: ObserverMessage[]
  window_fingerprint: string | null
  gap_reason: string | null
}

export type ObserverFn = (task: { taskId: string; conversationBindingId: string; expectedBindingVersion: number; expectedAccountIdentityVersion: number; targetName?: string }, request: { watermark: ObserverWatermark | null }) => Promise<ObserverResult>

export interface EngineOptions {
  api: ApiClient
  runtimeHome: string
  crypto: SessionCrypto
  observer: ObserverFn
  runtimeInstanceId: string
  /** 共享桌面锁包装（默认直通——生产接线 withDesktopLock；测试注入直通/伪锁） */
  withLock?: <T>(fn: () => Promise<T>) => Promise<T>
  now?: () => number
  emit?: (message: string) => void
  claimIntervalMs?: number
  renewIntervalMs?: number
  maxInFlightDecisions?: number
  /** C3：既有 v2 单动作执行器（invocationRunner）注入；缺失时执行相位保守 blocked。
   * sessionPrecheck 由引擎按任务构造（§7 顺序 4 锁内会话复核），cli 接线时传给
   * runner 的 deps.sessionPrecheck——在桌面锁内、write-authorize 之前执行 */
  runInvocation?: (inv: ClaimedInvocation, sessionPrecheck: () => Promise<void>) => Promise<void>
  batchSilenceMs?: number
  batchMaxWaitMs?: number
  syncRetryBaseMs?: number
  syncRetryMaxMs?: number
}

interface PendingDecision {
  decisionId: string
  batchId: string
  inputVersion: number
  pollAt: number
}

/** 旧任务的未完成工作（按 task_id 汇总，恢复扫描产出，adoptTask 消费后删除） */
interface OldTaskWork {
  oldAssignmentId: string
  watermark: ObserverWatermark | null
  /** 已冻结批次 batchId → inputVersion（含已决策与待决策） */
  decidedBatches: Map<string, number>
  /** 待提交批次（有 batch 事件但无 decision 事件）：batchId → 版本 + 批次事件 local_seq */
  pendingBatches: Map<string, { inputVersion: number; batchSeq: number }>
  /** 在飞决策（最后一条 decision 非终态） */
  inFlightDecision: { decisionId: string; batchId: string; status: string } | null
  /** send_ready 待执行决策（C3） */
  sendReadyDecision: { decisionId: string; batchId: string; inputVersion: number } | null
  /** executing 中的 invocation（C3，含冻结 input_version） */
  executionInvocation: { invocationId: string; decisionId: string; inputVersion: number } | null
  lastPhase: TaskPhase
  /** 日志/meta 损坏等：有旧工作但无法安全提取（新领取 blocked） */
  unrecoverable: boolean
}

interface TaskRuntime {
  inputVersionBase: number
  freshBaseline: boolean
  taskId: string
  assignmentId: string
  conversationBindingId: string
  fence: number
  controlEpoch: number
  /** 服务端控制序号（最新见值；调试/审计记录，当前不做拒绝判断） */
  serverControlSeq: number
  specRevision: number
  /** 领取时任务规格（meta.json 持久化用；决策请求仅用 spec_revision） */
  spec: Record<string, unknown>
  store: SessionStore
  phase: TaskPhase
  watermark: ObserverWatermark | null
  inputVersion: number
  pendingBatch: { batchId: string; messages: ObserverMessage[]; firstNewAt: number } | null
  decidedBatchIds: Set<string>
  inFlight: PendingDecision | null
  lastObservationAt: number
  observeDueAt: number
  observeBackoffIndex: number
  renewDueAt: number
  /** 未 ACK 事件（seq → 明文记录，供同步通道冲刷） */
  pendingEvents: Map<number, { event_id: string; type: string; payload: unknown }>
  syncNextAttemptAt: number
  syncBackoff: number
  /** 动作单元在飞互斥（各 task 最多一个观察/决策流程在飞，设计 §7） */
  busy: boolean
  /** 决策提交网络请求中（占并发名额，评审三轮 P1-3：请求前原子占用） */
  submitting: boolean
  /** 副作用门禁（评审 P1-6）：open=可观察/决策；paused_control/lease_stale=仅同步 */
  gate: 'open' | 'paused_control' | 'lease_stale'
  /** 本地租约截止（ms epoch）：断网时保守判定失效，停止新副作用 */
  leaseDeadline: number
  /** 待提交决策批次（评审 P1-7：并发满/断网时不丢；batchSeq=批次事件 local_seq，须 ACK 后才可提交；
   * kind=opening 为开场白（合成批次，batchSeq=0 免 ACK 门禁） */
  decisionQueue: Array<{ batchId: string; inputVersion: number; batchSeq: number; kind?: 'opening' | 'reply' }>
  /** 冻结批次 → 冻结时的 inputVersion（入队/提交取绑定值，不用最新 task.inputVersion） */
  batchVersionById: Map<string, number>
  /** 已有 decision 事件的批次（含 ready/superseded/failed/pending）——不再重新入队决策 */
  decidedByDecisionIds: Set<string>
  /** C3：send_ready 待执行决策（prepare-send → invocation） */
  sendReady: { decisionId: string; batchId: string; inputVersion: number } | null
  /** C3：executing 中的底座 invocation（含决策冻结 input_version——锁内复核比对） */
  execution: { invocationId: string; decisionId: string; inputVersion: number } | null
  /** 期望身份（评审 P1-9：观察结果三字段校验） */
  expectedBindingVersion: number
  expectedAccountIdentityVersion: number
  /** 连续 gap 计数（达阈值 → blocked，评审 P1-9） */
  gapStreak: number
  /** 控制 epoch 的 meta 持久化未完成（renew/ACK 后 persistMeta 失败）：门禁保持
   * 关闭，下轮 renew 在串行链内重试（审计 P1：不吞错、持久化失败不回开门禁） */
  metaPersistPending: boolean
  /** 最近一次接受的控制状态（'active'/'paused'/…）：持久化成功后据此决定是否
   * 重新开放门禁——不依赖 await 期间可能过时的响应（审计十轮） */
  lastKnownControlStatus: string
}

const OBSERVE_BACKOFF = [2_000, 5_000, 10_000, 30_000] // 设计 §6 工程初值
const SYNC_MAX_RECORDS = 100 // C1 events 接口条数上限
const SYNC_MAX_BYTES = 256 * 1024 // C1 events 接口字节上限

export class SessionTaskEngine {
  private readonly tasks = new Map<string, TaskRuntime>()
  private readonly queue = new ReadyQueue()
  private readonly opts: Required<Pick<EngineOptions, 'claimIntervalMs' | 'renewIntervalMs' | 'maxInFlightDecisions' | 'batchSilenceMs' | 'batchMaxWaitMs' | 'syncRetryBaseMs' | 'syncRetryMaxMs'>> & EngineOptions
  private stopped = false
  private lastClaimAt = 0
  private lastServedTaskId: string | null = null
  /** 磁盘水位（P2-8）：stopNew=true 时拒绝新观察持久化/新发送 */
  private diskStopNew = false
  private lastRetentionAt = 0

  constructor(options: EngineOptions) {
    this.opts = {
      claimIntervalMs: 2_000,
      renewIntervalMs: 20_000,
      maxInFlightDecisions: 2,
      batchSilenceMs: 2_000,
      batchMaxWaitMs: 10_000,
      syncRetryBaseMs: 1_000,
      syncRetryMaxMs: 30_000,
      ...options,
    }
  }

  get runningTaskCount(): number {
    return this.tasks.size
  }

  phaseOf(taskId: string): TaskPhase | undefined {
    return this.tasks.get(taskId)?.phase
  }

  /** 测试/观测辅助：任务当前控制状态（乱序/并发控制场景断言用，审计九轮 P1） */
  controlStateOf(
    taskId: string,
  ): { assignmentId: string; controlEpoch: number; serverControlSeq: number; gate: 'open' | 'paused_control' | 'lease_stale'; metaPersistPending: boolean } | undefined {
    const t = this.tasks.get(taskId)
    if (!t) return undefined
    return {
      assignmentId: t.assignmentId,
      controlEpoch: t.controlEpoch,
      serverControlSeq: t.serverControlSeq,
      gate: t.gate,
      metaPersistPending: t.metaPersistPending,
    }
  }

  shutdown(): void {
    this.stopped = true
  }

  private emit(message: string): void {
    this.opts.emit?.(message)
  }

  private now(): number {
    return this.opts.now ? this.opts.now() : Date.now()
  }

  private async withLock<T>(fn: () => Promise<T>): Promise<T> {
    return this.opts.withLock ? this.opts.withLock(fn) : fn()
  }

  /**
   * 启动恢复（评审 P1-5 + 续租接续）：两阶段。
   *
   * 阶段 1：扫描 session-tasks/ 下所有 assignment 目录，回放并把未 ACK 事件
   * 入 recoveryPending（对旧/已换代 assignment 走 C1 历史补交通道），同时提取
   * 每个旧任务的全部未完成工作（水位/批次/在飞决策）存入 oldTaskWork。
   *
   * 阶段 2：对有 meta.json 的旧 assignment 尝试续租接续——C1 的 renew 不校验
   * runtime_instance_id（只查 device/fence/control_epoch/租约），重启后的实例
   * 可对旧 assignment 直接 renew；租约仍有效则接续旧 assignment（恢复水位/
   * 已决策批次，不重新建基线）并撤销 oldTaskWork 条目；失效/被拒则事件走补交，
   * 任务等正常 claim——新 assignment 领取时按 oldTaskWork 持久化保守阻断
   * （C2 无自动换代接续协议）；旧日志损坏则新领取 blocked（需人工处理）。
   * 失败退避，不阻塞主循环启动。
   */
  private recovered = false

  /** 恢复补交待办（P1-5：失败进常驻重试，主循环每轮冲刷） */
  private readonly recoveryPending = new Map<string, { store: SessionStore; events: ReplayedEvent[]; retryAt: number; backoff: number }>()

  /**
   * 恢复扫描记录的旧任务未完成工作（审计：租约过期换代后新 assignment 领取时
   * 消费）。C2 无自动换代接续协议——存在任一未完成工作（水位/冻结批次/在飞
   * 决策）即对新 assignment 持久化保守阻断（recovery_blocked），不迁移水位、
   * 不静默重建基线（丢旧消息决策语义）；unrecoverable=true 表示旧日志损坏，
   * 同样阻断（原因=old_log_corrupt）。
   */
  private readonly oldTaskWork = new Map<string, OldTaskWork>()

  /**
   * 不可归属旧 assignment（审计 P1）：旧日志存在但 meta 缺失/损坏/缺 task_id，
   * 无法确定旧工作属于哪个任务 → 保守阻断本设备所有新会话任务激活（adoptTask
   * 持久化 recovery_blocked，不静默建基线）。key=assignment_id，reason=meta 失败
   * 原因；每次启动的恢复扫描重建本列表（不依赖一次性内存标记），人工清除不可
   * 归属目录并重启后解除。
   */
  private readonly unattributableAssignments = new Map<string, { reason: string }>()

  private async recoverOrphanAssignments(signal?: AbortSignal): Promise<void> {
    const { readdirSync, existsSync } = await import('node:fs')
    const root = (await import('node:path')).join(this.opts.runtimeHome, 'session-tasks')
    if (!existsSync(root)) return
    // 阶段 2 候选：回放成功且有 meta 的旧 assignment（含无事件的空日志目录）
    const candidates: Array<{
      assignmentId: string
      meta: AssignmentMeta
      store: SessionStore
      replayed: { events: ReplayedEvent[]; localSeq: number; droppedTailDiagnostic?: string }
    }> = []
    for (const name of readdirSync(root, { withFileTypes: true })) {
      if (!name.isDirectory()) continue
      const assignmentId = name.name
      const store = new SessionStore({ runtimeHome: this.opts.runtimeHome, assignmentId, crypto: this.opts.crypto })
      const metaResult = await store.readMeta()
      if (metaResult.status !== 'ok' && store.exists()) {
        // 审计 P1：旧日志存在但 meta 不可用——按失败原因分派：
        // decrypt_failed 可从明文 task_id 定位任务 → oldTaskWork 标记不可恢复
        // （新领取 blocked，不静默重建基线）；missing/corrupt/no_task_id 无法
        // 归属任务 → unattributableAssignments（保守阻断本设备所有新任务激活）
        if (metaResult.status === 'decrypt_failed') {
          const taskIdResult = store.readMetaTaskId()
          if (taskIdResult.status === 'ok') {
            this.recordOldTaskWork(taskIdResult.taskId, {
              oldAssignmentId: assignmentId,
              watermark: null,
              decidedBatches: new Map(),
              pendingBatches: new Map(),
              inFlightDecision: null,
              sendReadyDecision: null,
              executionInvocation: null,
              lastPhase: 'ready',
              unrecoverable: true,
            })
          } else {
            this.unattributableAssignments.set(assignmentId, { reason: taskIdResult.status })
            this.emit(`assignment=${assignmentId} 旧日志存在但 meta ${taskIdResult.status}，无法归属任务`)
          }
        } else {
          this.unattributableAssignments.set(assignmentId, { reason: metaResult.status })
          this.emit(`assignment=${assignmentId} 旧日志存在但 meta ${metaResult.status}，无法归属任务`)
        }
      }
      if (!store.exists()) {
        // 领取后、首条事件前崩溃：无日志无水位，接续等价于重新建基线前的 ready
        if (metaResult.status === 'ok') candidates.push({ assignmentId, meta: metaResult.meta, store, replayed: { events: [], localSeq: 0 } })
        continue
      }
      try {
        const replayed = await store.replay()
        if (metaResult.status === 'ok') {
          candidates.push({ assignmentId, meta: metaResult.meta, store, replayed })
          // 提取旧任务全部未完成工作——无论后续 renew 成败。renew 成功走
          // adoptFromRecovery 接续并删除本条目；失败（租约过期换代）则等新
          // assignment claim 时按此记录保守阻断
          this.recordOldTaskWork(metaResult.meta.task_id, extractOldTaskWork(assignmentId, replayed.events))
        }
        if (replayed.events.length === 0) continue
        const unacked = replayed.events.filter((e) => e.record.local_seq > store.ackedLocalSeq)
        if (unacked.length === 0) continue
        this.emit(`恢复扫描 assignment=${assignmentId}：待补交 ${unacked.length} 条事件`)
        this.recoveryPending.set(assignmentId, { store, events: unacked, retryAt: 0, backoff: this.opts.syncRetryBaseMs })
      } catch (err) {
        if (err instanceof SessionStoreCorruptError) {
          this.emit(`assignment=${assignmentId} 本地日志损坏，跳过补交（blocked 语义）: ${err.code}`)
          // 有旧工作但日志损坏 → 标记不可恢复（新领取 blocked）；meta 不可用的
          // 情形已在扫描前段按原因处理（不可归属 / decrypt_failed 不可恢复）
          if (metaResult.status === 'ok') {
            this.recordOldTaskWork(metaResult.meta.task_id, {
              oldAssignmentId: assignmentId,
              watermark: null,
              decidedBatches: new Map(),
              pendingBatches: new Map(),
              inFlightDecision: null,
              sendReadyDecision: null,
              executionInvocation: null,
              lastPhase: 'ready',
              unrecoverable: true,
            })
          }
        }
      }
    }
    await this.flushRecovery(signal)
    // 阶段 2：续租接续（不调 claim，不消耗 claim 配额）
    for (const c of candidates) {
      if ([...this.tasks.values()].some((t) => t.assignmentId === c.assignmentId)) continue // 防御：已在本实例运行
      try {
        const renewAck = await this.opts.api.sessionTaskRenew(
          c.assignmentId,
          { fence: c.meta.fence, control_epoch: c.meta.control_epoch },
          signal,
        )
        await this.adoptFromRecovery(c.assignmentId, c.meta, c.store, c.replayed, renewAck)
        // 接续成功：未 ACK 事件改由任务正常 syncPending 冲刷，撤销补交待办；
        // 旧工作已由日志回放恢复到本 assignment，撤销换代阻断待办（防止下次
        // claim 重复检查）
        this.recoveryPending.delete(c.assignmentId)
        this.oldTaskWork.delete(c.meta.task_id)
        this.emit(`assignment=${c.assignmentId} 续租接续成功（task ${c.meta.task_id}，不重新建基线）`)
      } catch (err) {
        // STALE/租约过期/网络失败 → 保持现有行为：事件走 recoveryPending 补交，
        // 任务等正常 claim（新 assignment 按 oldTaskWork 保守阻断）
        this.emit(`assignment=${c.assignmentId} 续租接续失败（走补交/重新领取路径）: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
  }

  /** 记录旧任务工作：同 task_id 多代旧 assignment 并存时保守合并——已有条目
   * 含未完成工作/不可恢复则保留（任一有工作即须阻断，引用保留首个发现），
   * 否则用新扫描结果覆盖（可能携带工作或不可恢复标记）。 */
  private recordOldTaskWork(taskId: string, work: OldTaskWork): void {
    const prev = this.oldTaskWork.get(taskId)
    if (!prev) {
      this.oldTaskWork.set(taskId, work)
      return
    }
    const prevHasWork =
      prev.unrecoverable || prev.watermark !== null || prev.decidedBatches.size > 0 || prev.inFlightDecision !== null
      || prev.sendReadyDecision !== null || prev.executionInvocation !== null
    if (!prevHasWork) this.oldTaskWork.set(taskId, work)
  }

  /** 恢复补交冲刷：按 100 条/批分批发送（P1-5），成功/终态移除，失败退避重试 */
  private async flushRecovery(signal?: AbortSignal): Promise<void> {
    const now = this.now()
    for (const [assignmentId, entry] of [...this.recoveryPending.entries()]) {
      if (now < entry.retryAt) continue
      const fence = entry.events[entry.events.length - 1]!.record.fence
      const allPending = entry.events
        .filter((e) => e.record.local_seq > entry.store.ackedLocalSeq)
        .map((e) => ({ local_seq: e.record.local_seq, event_id: e.record.event_id, type: e.record.type, payload: e.payload }))
      if (allPending.length === 0) {
        this.recoveryPending.delete(assignmentId)
        writeAckedMarker(this.opts.runtimeHome, assignmentId, entry.store.ackedLocalSeq)
        continue
      }
      // 恢复通道必须保持连续前缀（云端按连续前缀校验，跳号整批拒绝）：在首个
      // 超限事件处截断，只发送其之前的连续事件；首个事件即超限（前缀为空）→
      // 该 assignment 停止重试，本地保留待人工处理。
      const firstOversizedIdx = allPending.findIndex((ev) => syncRecordBytes(ev) > SYNC_MAX_BYTES)
      if (firstOversizedIdx === 0) {
        this.recoveryPending.delete(assignmentId)
        this.emit(`assignment=${assignmentId} 恢复事件 local_seq=${allPending[0]!.local_seq} 超 256KiB 上限（前缀为空），停止补交（本地保留，需人工处理）`)
        continue
      }
      const sendable = firstOversizedIdx === -1 ? allPending : allPending.slice(0, firstOversizedIdx)
      const { batches: rbatches } = batchEventsForSync(sendable)
      const batch = rbatches[0] ?? []
      if (batch.length === 0) continue // 防御：sendable 非空且无超限，不应到达
      try {
        const ack = await this.opts.api.sessionTaskEvents(assignmentId, { fence, records: batch }, signal)
        entry.store.ackUpTo(ack.ack_seq)
        entry.backoff = this.opts.syncRetryBaseMs
        const remaining = entry.events.filter((e) => e.record.local_seq > ack.ack_seq)
        if (remaining.length === 0) {
          this.recoveryPending.delete(assignmentId)
          // 持久 ACK 标记：retention 据此判定可清理（重启后不误删）
          writeAckedMarker(this.opts.runtimeHome, assignmentId, ack.ack_seq)
          this.emit(`assignment=${assignmentId} 恢复补交完成（marker=${ack.ack_seq}）`)
        } else {
          entry.events = remaining
          entry.retryAt = now + 50 // 下一批
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        if (err instanceof NetworkError) {
          entry.retryAt = now + entry.backoff
          entry.backoff = Math.min(entry.backoff * 2, this.opts.syncRetryMaxMs)
        } else {
          // STALE 等 4xx：云端换代/已终结——本地保留（保留期清理），不再重试
          this.emit(`assignment=${assignmentId} 补交被拒（停止重试，保留本地）: ${msg}`)
          this.recoveryPending.delete(assignmentId)
        }
      }
    }
  }

  /** 主循环：恢复扫描 → claim → renew → sync → 调度一个动作单元 → 小步睡眠 */
  async run(signal?: AbortSignal): Promise<void> {
    if (!this.recovered) {
      this.recovered = true
      try {
        await this.recoverOrphanAssignments(signal)
      } catch (err) {
        this.emit(`启动恢复扫描异常（不阻塞主循环）: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
    while (!this.stopped && !(signal && signal.aborted)) {
      const now = this.now()
      try {
        if (now - this.lastClaimAt >= this.opts.claimIntervalMs) {
          this.lastClaimAt = now
          await this.claimOnce(signal)
        }
        this.renewDue(now, signal)
        await this.syncPending(now, signal)
        await this.flushRecovery(signal)
        await this.checkRetention(now)
        this.dispatchOne(now)
      } catch (err) {
        // 网络类错误安全等待；其他异常记录并继续（单任务错误不拖垮引擎）
        this.emit(`engine 循环异常（安全等待）: ${err instanceof Error ? err.message : String(err)}`)
      }
      await this.sleepTick(signal)
    }
  }

  private sleepTick(signal?: AbortSignal): Promise<void> {
    return new Promise((resolve) => {
      const t = setTimeout(resolve, 25)
      if (signal) {
        signal.addEventListener(
          'abort',
          () => {
            clearTimeout(t)
            resolve()
          },
          { once: true },
        )
      }
    })
  }

  // ---------------- claim / renew ----------------

  private async claimOnce(signal?: AbortSignal): Promise<void> {
    const claimed = await this.opts.api.sessionTaskClaim(this.opts.runtimeInstanceId, signal)
    if (claimed === null) return
    const existing = this.tasks.get(claimed.task_id)
    if (existing && existing.assignmentId === claimed.assignment_id) return
    if (existing) {
      existing.gate = 'lease_stale'
      // 保留换代前的未 ACK 事实，独立历史补交通道继续处理。
      const replayed = await existing.store.replay()
      this.recordOldTaskWork(existing.taskId, extractOldTaskWork(existing.assignmentId, replayed.events))
      const events = replayed.events.filter((e) => e.record.local_seq > existing.store.ackedLocalSeq)
      if (events.length) this.recoveryPending.set(existing.assignmentId, {
        store: existing.store, events, retryAt: 0, backoff: this.opts.syncRetryBaseMs,
      })
    }
    await this.adoptTask(claimed)
  }

  private async adoptTask(claimed: {
    input_version_base?: number
    fresh_baseline?: boolean
    assignment_id: string
    task_id: string
    spec: Record<string, unknown>
    spec_revision: number
    fence: number
    control_epoch: number
    lease_seconds: number
    conversation_binding_id?: string
    binding_version?: number
    account_identity_version?: number
  }): Promise<void> {
    const store = new SessionStore({
      runtimeHome: this.opts.runtimeHome,
      assignmentId: claimed.assignment_id,
      crypto: this.opts.crypto,
    })
    const task: TaskRuntime = {
      taskId: claimed.task_id,
      assignmentId: claimed.assignment_id,
      conversationBindingId: claimed.conversation_binding_id ?? '',
      fence: claimed.fence,
      controlEpoch: claimed.control_epoch,
      serverControlSeq: 0,
      specRevision: claimed.spec_revision,
      spec: claimed.spec,
      store,
      phase: 'ready',
      watermark: null,
      inputVersion: claimed.input_version_base ?? 0,
      inputVersionBase: claimed.input_version_base ?? 0,
      freshBaseline: claimed.fresh_baseline === true,
      pendingBatch: null,
      decidedBatchIds: new Set(),
      inFlight: null,
      sendReady: null,
      execution: null,
      lastObservationAt: 0,
      observeDueAt: this.now(),
      observeBackoffIndex: 0,
      renewDueAt: this.now() + Math.min(this.opts.renewIntervalMs, claimed.lease_seconds * 300),
      pendingEvents: new Map(),
      syncNextAttemptAt: 0,
      syncBackoff: this.opts.syncRetryBaseMs,
      busy: false,
      submitting: false,
      gate: 'open',
      leaseDeadline: this.now() + (claimed.lease_seconds ?? 60) * 1000,
      decisionQueue: [],
      batchVersionById: new Map(),
      decidedByDecisionIds: new Set(),
      expectedBindingVersion: claimed.binding_version ?? 0,
      expectedAccountIdentityVersion: claimed.account_identity_version ?? 0,
      gapStreak: 0,
      metaPersistPending: false,
      // 领取即激活（gate=open），隐含控制状态 active；后续控制经串行链更新
      lastKnownControlStatus: 'active',
    }
    // 持久化成功是激活任务的前提（审计三）：meta.json 持有重启后续租接续的
    // 身份（fence/control_epoch）——写入失败则不激活（不加入 tasks/ReadyQueue），
    // 本轮领取放弃，等下一轮 claim 重试；spec 经 crypto 加密落盘
    try {
      await store.writeMeta({
        input_version_base: task.inputVersionBase,
        fresh_baseline: task.freshBaseline,
        task_id: task.taskId,
        conversation_binding_id: task.conversationBindingId,
        binding_version: task.expectedBindingVersion,
        account_identity_version: task.expectedAccountIdentityVersion,
        spec_revision: task.specRevision,
        spec: task.spec,
        fence: task.fence,
        control_epoch: task.controlEpoch,
      })
    } catch (err) {
      this.emit(`task ${task.taskId} meta.json 写入失败，任务不激活: ${err instanceof Error ? err.message : String(err)}`)
      return
    }
    if (store.exists()) {
      // 重启恢复：回放既有日志重建水位/未同步事件（损坏 → blocked 照常上报）
      try {
        await this.restoreTaskFromLog(task, await store.replay())
        // P2 自愈：baseline 已建立但 opening 未入队的崩溃窗口（服务端幂等/唯一
        // 索引兜底，重复提交无害）
        if (task.watermark && task.phase !== 'blocked') this.maybeEnqueueOpening(task, this.now())
      } catch (err) {
        if (err instanceof SessionStoreCorruptError) {
          task.phase = 'blocked'
          this.emit(`task ${task.taskId} 本地日志损坏，进入 blocked: ${err.code}`)
        } else {
          throw err
        }
      }
    } else {
      // 首次领取：建基线观察由首个动作单元执行
    }
    // fresh_baseline 仅由服务端人工恢复安全检查授权；允许重建基线，旧证据仍保留。
    // 普通换代恢复保守阻断：恢复扫描记录了旧 assignment 的未完成工作 → 新
    // assignment 持久化 blocked（C2 无自动换代接续协议，不迁移水位、不重建
    // 基线——那会丢旧消息决策语义）；旧日志损坏同样阻断。阻断经 recovery_blocked
    // 事件持久化（重启回放仍 blocked）。persistRecoveryBlocked 写盘失败时异常
    // 向上传播（任务不激活，条目保留待下轮 claim 重试）。
    const oldWork = this.oldTaskWork.get(task.taskId)
    if (oldWork && !task.freshBaseline) {
      if (oldWork.unrecoverable) {
        await this.persistRecoveryBlocked(task, 'old_log_corrupt', oldWork.oldAssignmentId)
      } else if (
        oldWork.watermark !== null ||
        oldWork.decidedBatches.size > 0 ||
        oldWork.inFlightDecision !== null ||
        oldWork.sendReadyDecision !== null ||
        oldWork.executionInvocation !== null
      ) {
        await this.persistRecoveryBlocked(task, 'generation_change_requires_manual_review', oldWork.oldAssignmentId)
      }
      // oldWork 为 undefined 或全空（无任何旧工作）→ 首次领取语义，正常建基线
      this.oldTaskWork.delete(task.taskId)
    }
    if (task.freshBaseline) this.oldTaskWork.delete(task.taskId)
    // 不可归属旧 assignment（审计 P1）：旧日志存在但 meta 缺失/损坏/缺 task_id，
    // 无法确定旧 assignment 属于哪个 task → 保守阻断本设备的所有新会话任务激
    // 活（不建基线、不观察、不决策）。阻断经 recovery_blocked 事件持久化（重
    // 启回放仍 blocked），事件补交（syncPending）与上面阻断路径一致不受影响。
    if (this.unattributableAssignments.size > 0) {
      const reasons = [...this.unattributableAssignments.entries()].map(([id, v]) => `${id}:${v.reason}`).join(', ')
      await this.persistRecoveryBlocked(task, `unattributable_old_assignment_exists(${reasons})`, 'unknown')
      this.emit(`task ${task.taskId} 设备存在不可归属旧 assignment，保守阻断新任务激活`)
    }
    this.tasks.set(task.taskId, task)
    this.queue.set(task.taskId, 'observe', task.observeDueAt)
    this.emit(`task ${task.taskId} 领取 assignment=${task.assignmentId} fence=${task.fence}`)
  }

  /**
   * 持久化换代恢复阻断：先写一条加密事务记录（recovery_blocked，含旧
   * assignment 引用与阻断原因）到新 assignment 日志，写盘成功后才设内存
   * blocked + 门禁关闭——重启回放该事件仍为 blocked（持久）；事件补交
   * （syncPending）不受 blocked 影响。
   */
  private async persistRecoveryBlocked(task: TaskRuntime, reason: string, oldAssignmentId: string): Promise<void> {
    await this.logEvent(
      task,
      'recovery_blocked',
      {
        reason,
        old_assignment_id: oldAssignmentId,
        task_id: task.taskId,
      },
      this.now(),
    )
    // 写盘成功后才设内存 blocked（写盘失败时异常已向上传播，内存不动）
    task.phase = 'blocked'
    task.gate = 'paused_control' // 阻断一切副作用（观察/新决策/执行）
    this.emit(`task ${task.taskId} 换代恢复保守阻断（原因=${reason}，旧 assignment=${oldAssignmentId}）`)
  }

  /**
   * 回放恢复（claim 重启与续租接续共用同一套逻辑，避免两份代码漂移）：从日志
   * 重建水位/批次/决策/相位/未同步事件。仅回放失败的 SessionStoreCorruptError
   * 向上抛（调用方置 blocked）；本方法自身不产生 IO。
   */
  private async restoreTaskFromLog(
    task: TaskRuntime,
    replayed: { events: ReplayedEvent[]; localSeq: number; droppedTailDiagnostic?: string },
  ): Promise<void> {
    const batchSeqById = new Map<string, number>() // batchId → 事件 local_seq（ACK 门禁恢复）
    // batchVersionById / decidedByDecisionIds 为 task 属性：syncPending ACK
    // 入队时沿用同一口径（版本取冻结时绑定值；已决策批次不再重新激活）
    let lastDecision: { decisionId: string; batchId: string; status: string } | null = null
    // C3 执行链恢复：send_ready 的待执行决策与 executing 的 invocation
    let restoredSendReady: { decisionId: string; batchId: string; inputVersion: number } | null = null
    let restoredExecution: { invocationId: string; decisionId: string; inputVersion: number } | null = null
    for (const ev of replayed.events) {
      task.pendingEvents.set(ev.record.local_seq, { event_id: ev.record.event_id, type: ev.record.type, payload: ev.payload })
      const p = ev.payload as {
        watermark?: ObserverWatermark
        watermark_after?: ObserverWatermark
        batch_id?: string
        input_version?: number
        to?: TaskPhase
        phase_to?: TaskPhase
        decision_id?: string
        invocation_id?: string
        status?: string
      }
      if (ev.record.type === 'baseline' || ev.record.type === 'observation') {
        if (p && p.watermark) task.watermark = p.watermark
      }
      if (ev.record.type === 'batch') {
        if (p && p.watermark_after) task.watermark = p.watermark_after
        if (p && typeof p.batch_id === 'string') {
          task.decidedBatchIds.add(p.batch_id)
          batchSeqById.set(p.batch_id, ev.record.local_seq)
          task.batchVersionById.set(p.batch_id, typeof p.input_version === 'number' ? p.input_version : task.inputVersion)
        }
        if (p && typeof p.input_version === 'number') task.inputVersion = Math.max(task.inputVersion, p.input_version)
      }
      if (ev.record.type === 'phase' && p && p.to) task.phase = p.to
      // decision_phase：决策状态+相位单条事务记录（新日志）；旧日志的
      // decision + phase 两类事件仍按序兼容回放
      if (ev.record.type === 'decision_phase' && p && p.phase_to) {
        task.phase = p.phase_to
        if (p.phase_to === 'send_ready' && typeof p.decision_id === 'string' && p.decision_id) {
          restoredSendReady = {
            decisionId: p.decision_id,
            batchId: String(p.batch_id ?? ''),
            inputVersion: typeof p.input_version === 'number' ? p.input_version : task.inputVersion,
          }
        }
        if (p.phase_to === 'waiting_peer') {
          restoredSendReady = null
          restoredExecution = null
        }
      }
      // execution_phase（C3）：send_ready→executing→waiting_peer 单条事务记录——
      // 相位与配对数据在同一事件内完整恢复（executing 恢复后按原 invocation 接续）
      if (ev.record.type === 'execution_phase' && p) {
        if (p.phase_to === 'executing' && typeof p.invocation_id === 'string' && typeof p.decision_id === 'string') {
          restoredExecution = {
            invocationId: p.invocation_id,
            decisionId: p.decision_id,
            inputVersion: typeof p.input_version === 'number' ? p.input_version : Number.MAX_SAFE_INTEGER,
          }
          // MAX_SAFE_INTEGER 语义：旧日志无冻结版本 → 恒小于当前，触发版本门禁
          //（保守阻断而非放行；Runtime 重试 prepare-send 走服务端权威校验）
          restoredSendReady = null
          task.phase = 'executing'
        }
        if (p.phase_to === 'waiting_peer') {
          task.phase = 'waiting_peer'
          restoredSendReady = null
          restoredExecution = null
        }
        if (p.phase_to === 'waiting_peer') {
          restoredSendReady = null
          restoredExecution = null
        }
        if (p.phase_to === 'blocked') {
          task.phase = 'blocked'
          restoredSendReady = null
          restoredExecution = null
        }
      }
      if (ev.record.type === 'decision' || ev.record.type === 'decision_phase') {
        // 恢复在飞决策与队列（评审三轮 P1-2）：最后一条未终态 decision 恢复为
        // inFlight（decision_pending 轮询）；已 superseded/failed 的只记批次
        if (p && typeof p.batch_id === 'string') task.decidedByDecisionIds.add(p.batch_id)
        lastDecision = {
          decisionId: String(p?.decision_id ?? ''),
          batchId: String(p?.batch_id ?? ''),
          status: String(p?.status ?? ''),
        }
      }
      if (ev.record.type === 'recovery_blocked') {
        // 换代恢复阻断持久回放：重启后仍 blocked（相位 + 门禁），事件补交
        // （syncPending）不受影响
        task.phase = 'blocked'
        task.gate = 'paused_control'
      }
    }
    // 决策恢复：最后 decision 非 ready/superseded/failed → 恢复为在飞
    if (lastDecision && lastDecision.decisionId && !['ready', 'superseded', 'failed'].includes(lastDecision.status)) {
      task.inFlight = {
        decisionId: lastDecision.decisionId,
        batchId: lastDecision.batchId,
        inputVersion: task.batchVersionById.get(lastDecision.batchId) ?? task.inputVersion,
        pollAt: this.now() + 1_000,
      }
      this.emit(`task ${task.taskId} 恢复在飞决策 ${lastDecision.decisionId}`)
    } else {
      // 无在飞决策：恢复所有未被 decision 事件覆盖的冻结批次——C1 校验各
      // 批次自己的 input_version，不存在“旧批次被覆盖”；每个批次独立携带
      // inputVersion/batchSeq 入队（Map 按日志顺序迭代）
      for (const [batchId, seq] of batchSeqById) {
        if (task.decidedByDecisionIds.has(batchId)) continue
        const iv = task.batchVersionById.get(batchId) ?? task.inputVersion
        if (!task.decisionQueue.some((q) => q.batchId === batchId)) {
          task.decisionQueue.push({ batchId, inputVersion: iv, batchSeq: seq })
          this.emit(`task ${task.taskId} 恢复待决策批次 ${batchId}`)
        }
      }
    }
    // C3 执行链回放落位：相位与决策/invocation 记录原子恢复；防御崩溃窗口
    // （相位已迁移但配对记录缺失 → 回 waiting_peer，prepare-send 幂等可重入）
    task.sendReady = restoredSendReady
    task.execution = restoredExecution
    if (task.phase === 'send_ready' && task.sendReady === null) task.phase = 'waiting_peer'
    if (task.phase === 'executing' && task.execution === null) task.phase = 'waiting_peer'
    // 崩溃窗口对齐（仅旧格式日志）：decision 已落盘、phase 未落盘——恢复
    // 设置了 inFlight 但 phase 仍是旧值（如 ready），调度会走观察而非轮
    // 询；强制对齐 decision_pending。新 decision_phase 单条事务记录不存
    // 在此窗口（决策状态与相位原子恢复）。
    if (task.inFlight !== null && task.phase !== 'decision_pending' && task.phase !== 'send_ready') {
      const stalePhase = task.phase
      task.phase = 'decision_pending'
      this.emit(`task ${task.taskId} 在飞决策与相位 ${stalePhase} 不一致，对齐为 decision_pending`)
    }
    if (replayed.droppedTailDiagnostic) {
      this.emit(`task ${task.taskId} 丢弃未完整尾记录（诊断保留）: ${replayed.droppedTailDiagnostic}`)
    }
    this.emit(`task ${task.taskId} 从本地日志恢复 localSeq=${replayed.localSeq}`)
  }

  /**
   * 续租接续（不调 claim，不消耗 claim 配额）：renew 成功后由恢复扫描调用。
   * 用 meta 身份 + 本地日志回放恢复 TaskRuntime（复用 restoreTaskFromLog），
   * control_epoch 取 renew 返回值，租约截止按 ack 重置；纳入 tasks + ReadyQueue。
   * renew 返回非 active 控制（暂停/终态）→ 恢复任务但不推进（gate=paused_control）。
   */
  private async adoptFromRecovery(
    assignmentId: string,
    meta: AssignmentMeta,
    store: SessionStore,
    replayed: { events: ReplayedEvent[]; localSeq: number; droppedTailDiagnostic?: string },
    renewAck: SessionTaskControlAck,
  ): Promise<void> {
    const task: TaskRuntime = {
      taskId: meta.task_id,
      assignmentId,
      conversationBindingId: meta.conversation_binding_id,
      fence: meta.fence,
      // 先以 meta 记录的 epoch 建运行时，renew 返回的控制经 applyControlUpdate
      // 统一应用（审计九轮 P1）：epoch 前进才持久化 + 按状态决定门禁
      controlEpoch: meta.control_epoch,
      serverControlSeq: 0,
      specRevision: meta.spec_revision,
      spec: meta.spec,
      store,
      phase: 'ready',
      watermark: null,
      inputVersion: meta.input_version_base ?? 0,
      inputVersionBase: meta.input_version_base ?? 0,
      freshBaseline: meta.fresh_baseline === true,
      pendingBatch: null,
      decidedBatchIds: new Set(),
      inFlight: null,
      sendReady: null,
      execution: null,
      lastObservationAt: 0,
      observeDueAt: this.now(),
      observeBackoffIndex: 0,
      renewDueAt: this.now() + Math.min(this.opts.renewIntervalMs, Math.max(renewAck.lease_seconds, 1) * 300),
      pendingEvents: new Map(),
      syncNextAttemptAt: 0,
      syncBackoff: this.opts.syncRetryBaseMs,
      busy: false,
      submitting: false,
      gate: 'open',
      leaseDeadline: this.now() + renewAck.lease_seconds * 1000,
      decisionQueue: [],
      batchVersionById: new Map(),
      decidedByDecisionIds: new Set(),
      expectedBindingVersion: meta.binding_version,
      expectedAccountIdentityVersion: meta.account_identity_version,
      gapStreak: 0,
      metaPersistPending: false,
      // 接续先以 gate=open 起步，renew 返回的控制经 applyControlUpdate 统一应用
      lastKnownControlStatus: 'active',
    }
    await this.restoreTaskFromLog(task, replayed)
    this.tasks.set(task.taskId, task)
    this.queue.set(task.taskId, 'observe', task.observeDueAt)
    // renew 返回的控制同样走串行入口（审计九轮 P1）：暂停/终态只保留同步通道
    // （禁止观察与新决策，旧决策作废）；迟到旧响应不会覆盖恢复语义
    await this.applyControlUpdate(task, 'recovery', {
      assignmentId,
      control_epoch: renewAck.control.control_epoch,
      server_control_seq: renewAck.control.server_control_seq,
      status: renewAck.control.status,
    })
  }

  /** 刷新 meta.json（领取时写、control_epoch 变化后重写）。失败向上传播——
   * 调用方负责门禁回退与重试，不吞错（审计 P1：控制更新持久化失败不得照常
   * 推进）；spec 字段经 crypto 加密落盘——业务正文不绕过 DPAPI 保护 */
  private async persistMeta(task: TaskRuntime): Promise<void> {
    await task.store.writeMeta({
      input_version_base: task.inputVersionBase,
      fresh_baseline: task.freshBaseline,
      task_id: task.taskId,
      conversation_binding_id: task.conversationBindingId,
      binding_version: task.expectedBindingVersion,
      account_identity_version: task.expectedAccountIdentityVersion,
      spec_revision: task.specRevision,
      spec: task.spec,
      fence: task.fence,
      control_epoch: task.controlEpoch,
    })
  }

  /** 任务级串行控制更新链（审计九轮 P1）：同一任务的控制更新依次应用，检查与
   * 应用之间不被其他控制响应交错（key=task_id；链尾自清理不留悬挂条目）。 */
  private readonly controlChains = new Map<string, Promise<void>>()

  /**
   * 任务级串行控制更新（唯一入口，审计九轮 P1）。renew / 事件 ACK / 启动恢复
   * 接续的控制处理统一走此方法——同一任务的控制更新在串行链上按到达顺序应用。
   *
   * 不变量：
   * - 旧响应（control_epoch < task.controlEpoch）不得回退控制状态、覆盖元数据、
   *   重新打开门禁
   * - 同版本同状态响应幂等（不重复持久化；有待持久化元数据时除外——须重试）
   * - 应用有效新控制前先关闭动作门禁；持久化成功后才依据最新控制决定是否开放
   * - 不依据异步等待期间可能过时的响应开放门禁
   * - 持久化失败保留最新待写控制（metaPersistPending），门禁持续关闭；重试同样
   *   在串行链内执行，成功只持久化最新版本（审计十轮：不允许链外元数据写入）
   * - 本地阻断（recovery_blocked / 日志损坏）不被普通 active 控制清除
   * - 任务换 assignment 后，旧回调不得影响新任务（assignment 比对 + 当前运行时比对）
   *
   * persistMeta 失败不抛出（内部消化），调用方无需 try/catch。
   */
  private async applyControlUpdate(
    task: TaskRuntime,
    source: 'renew' | 'event_ack' | 'recovery',
    incoming: { assignmentId: string; control_epoch: number; server_control_seq: number; status: string },
  ): Promise<void> {
    const step = () => this.runControlUpdate(task, source, incoming)
    const prev = this.controlChains.get(task.taskId) ?? Promise.resolve()
    const run = prev.then(step, step) // 防御：链上异常不阻断后续控制更新
    this.controlChains.set(task.taskId, run)
    // 链尾自清理：空闲/已移出调度的任务不在内存留条目
    void run.then(
      () => {
        if (this.controlChains.get(task.taskId) === run) this.controlChains.delete(task.taskId)
      },
      () => {
        if (this.controlChains.get(task.taskId) === run) this.controlChains.delete(task.taskId)
      },
    )
    await run
  }

  /** 串行链上的控制应用（仅经 applyControlUpdate 调用，勿直接调用） */
  private async runControlUpdate(
    task: TaskRuntime,
    source: 'renew' | 'event_ack' | 'recovery',
    incoming: { assignmentId: string; control_epoch: number; server_control_seq: number; status: string },
  ): Promise<void> {
    // 1. assignment 归属检查：回调目标已不是当前运行时（任务换代/移出调度）或
    // 来源 assignment 与任务当前 assignment 不符 → 丢弃，不影响新任务
    const live = this.tasks.get(task.taskId)
    if (live !== task) {
      this.emit(
        live
          ? `task ${task.taskId} 丢弃旧 assignment 控制响应（回调目标非当前运行时，来源=${source}）`
          : `task ${task.taskId} 丢弃迟到控制响应（任务已移出调度，来源=${source}）`,
      )
      return
    }
    if (incoming.assignmentId !== task.assignmentId) {
      this.emit(`task ${task.taskId} 丢弃旧 assignment 控制响应（${incoming.assignmentId} ≠ 当前 ${task.assignmentId}，来源=${source}）`)
      return
    }
    // 2. 本地阻断（recovery_blocked / 日志损坏，phase=blocked 且门禁为控制关闭）：
    // 普通 active 控制不得解除；仅前进 controlEpoch/serverControlSeq（供 meta 记录）
    if (task.phase === 'blocked' && task.gate === 'paused_control') {
      if (incoming.control_epoch > task.controlEpoch) {
        task.controlEpoch = incoming.control_epoch
        task.serverControlSeq = Math.max(task.serverControlSeq, incoming.server_control_seq)
        try {
          await this.persistMeta(task) // 尽力持久化（失败不影响阻断状态）
        } catch {
          /* best effort */
        }
      }
      return
    }
    // 3. 旧版本拒绝：epoch 小于当前 → 丢弃（不回退状态、不覆盖元数据、不开门禁）
    if (incoming.control_epoch < task.controlEpoch) {
      this.emit(`task ${task.taskId} 丢弃旧控制响应（epoch ${incoming.control_epoch} < ${task.controlEpoch}，来源=${source}）`)
      return
    }
    // 租约本地兜底恢复：门禁因断网判 lease_stale，但续租已刷新本地租约且控制未
    // 变（同代）→ 直接按本响应恢复/保持门禁，无需持久化（control_epoch 未变化）
    if (task.gate === 'lease_stale' && this.now() <= task.leaseDeadline && incoming.control_epoch === task.controlEpoch) {
      task.serverControlSeq = Math.max(task.serverControlSeq, incoming.server_control_seq)
      task.lastKnownControlStatus = incoming.status
      if (incoming.status !== 'active') {
        task.gate = 'paused_control'
        task.inFlight = null
        task.sendReady = null
        task.decisionQueue = []
      } else {
        task.gate = 'open'
      }
      return
    }
    // 4. 同版本幂等：epoch 相等且状态一致且无待持久化元数据 → 无操作（不重复
    //    持久化）；有 metaPersistPending 时落到第 5 步在链内重试写入
    const currentStatus = task.gate === 'open' ? 'active' : 'paused'
    if (incoming.control_epoch === task.controlEpoch && incoming.status === currentStatus && !task.metaPersistPending) {
      task.serverControlSeq = Math.max(task.serverControlSeq, incoming.server_control_seq)
      return
    }
    // 5. 应用有效新控制：先保守关门禁 → 前进控制元数据 → 持久化成功后依据
    //    lastKnownControlStatus 决定是否开放（不依据等待期间可能过时的响应开门）
    const epochIncreased = incoming.control_epoch > task.controlEpoch
    const statusChanged = incoming.status !== task.lastKnownControlStatus
    const wasLeaseStale = task.gate === 'lease_stale' && this.now() > task.leaseDeadline
    task.gate = 'paused_control' // 保守关闭（覆盖 lease_stale：恢复开放须经持久化成功）
    task.controlEpoch = Math.max(task.controlEpoch, incoming.control_epoch)
    task.serverControlSeq = Math.max(task.serverControlSeq, incoming.server_control_seq)
    task.lastKnownControlStatus = incoming.status
    if (epochIncreased || incoming.status !== 'active') {
      // 控制代变化或暂停/终态：旧决策作废、待提交批次清空（对齐原 renew/ACK 语义）
      task.inFlight = null
      task.sendReady = null
      task.decisionQueue = []
    }
    // 有待持久化的元数据（上次失败）或控制状态变化 → 需要写入（审计十轮：元数据
    // 重试纳入串行链——链外旧重试会与链内新控制并发写盘，可能把磁盘覆盖回旧代）
    const needPersist = task.metaPersistPending || epochIncreased || statusChanged
    if (needPersist) {
      task.metaPersistPending = true
      const expectedAssignmentId = task.assignmentId
      try {
        // 执行时读取当前最新待持久化控制（persistMeta 从 task 当前状态构造 meta，
        // 不捕获旧值）；串行链保证 await 期间无其他控制更新修改 task
        await this.persistMeta(task)
        // 持久化完成后检查任务/assignment 是否仍有效（await 期间任务可能被移出
        // 调度或被新 assignment 替换——不应用旧状态到已换代对象）
        const stillValid = this.tasks.get(task.taskId) === task && task.assignmentId === expectedAssignmentId
        if (stillValid) {
          // 写入成功且任务未换代：清除待持久化标记
          task.metaPersistPending = false
          // 依据最近一次接受的控制状态决定是否开放（串行链保证该状态在 await
          // 期间未被其他控制更新改写，即写入的就是最新版本）
          if (task.lastKnownControlStatus === 'active' && task.phase !== 'blocked' && !wasLeaseStale) {
            task.gate = 'open'
          }
          // 非 active / 本地租约仍失效 / 本地阻断：保持 paused_control
        } else {
          this.emit(`task ${task.taskId} 持久化完成后检测到任务已换代，不应用旧状态`)
        }
      } catch (err) {
        // 持久化失败：保留 pending，门禁持续关闭，下轮 renew 在串行链内重试
        this.emit(
          `task ${task.taskId} 控制更新持久化失败（门禁保持关闭，下轮续租重试，来源=${source}）: ${err instanceof Error ? err.message : String(err)}`,
        )
      }
    }
  }

  private renewDue(now: number, signal?: AbortSignal): void {
    for (const task of this.tasks.values()) {
      // 本地租约兜底（评审 P1-6）：断网/续租持续失败超过租约期 → 停止新副作用
      if (task.gate === 'open' && now > task.leaseDeadline) {
        task.gate = 'lease_stale'
        this.emit(`task ${task.taskId} 本地租约判定失效（断网兜底），停止新副作用`)
      }
      if (now < task.renewDueAt) continue
      task.renewDueAt = now + this.opts.renewIntervalMs
      void this.opts.api
        .sessionTaskRenew(task.assignmentId, { fence: task.fence, control_epoch: task.controlEpoch }, signal)
        .then(async (ack) => {
          // 租约管理（非控制状态）：刷新本地租约截止
          task.leaseDeadline = this.now() + ack.lease_seconds * 1000
          // 控制处理统一走任务级串行入口（审计九轮 P1）：旧响应按 epoch 拒绝；
          // 上次持久化失败的元数据重试也经链内 needPersist 分支执行（审计十轮：
          // 不允许链外写入——链外旧重试会与链内新控制并发写盘，覆盖回旧代）
          await this.applyControlUpdate(task, 'renew', {
            assignmentId: task.assignmentId,
            control_epoch: ack.control.control_epoch,
            server_control_seq: ack.control.server_control_seq,
            status: ack.control.status,
          })
        })
        .catch((err: unknown) => {
          if (err instanceof NetworkError) {
            this.emit(`task ${task.taskId} 续租网络失败（离线等待，租约截止 ${new Date(task.leaseDeadline).toISOString()}）`)
            return
          }
          // STALE：assignment 已换代 → 移出调度等重新 claim；未 ACK 事件走历史补交
          this.emit(`task ${task.taskId} 续租被拒（等待重新分配）: ${err instanceof Error ? err.message : String(err)}`)
          task.gate = 'lease_stale'
        })
    }
  }

  // ---------------- 同步通道（独立于动作调度） ----------------

  private async syncPending(now: number, signal?: AbortSignal): Promise<void> {
    for (const task of this.tasks.values()) {
      if (task.pendingEvents.size === 0 || now < task.syncNextAttemptAt) continue
      const acked = task.store.ackedLocalSeq
      const allPending: Array<{ local_seq: number; event_id: string; type: string; payload: unknown }> = []
      for (let seq = acked + 1; seq <= task.store.lastLocalSeq; seq++) {
        const ev = task.pendingEvents.get(seq)
        if (!ev) break
        allPending.push({ local_seq: seq, event_id: ev.event_id, type: ev.type, payload: ev.payload })
      }
      if (allPending.length === 0) continue
      const { batches, oversized } = batchEventsForSync(allPending)
      if (oversized.length > 0) {
        // 超限事件云端从未接收：不能删除或 ACK（会制造序号缺口，云端按连续
        // 前缀校验将拒绝后续全部事件）。assignment 整体转 blocked，未 ACK
        // 事件全部保留不冲刷，等待人工处理。
        if (task.phase !== 'blocked') {
          this.emit(`task ${task.taskId} ${oversized.length} 条事件超 256KiB 上限，assignment 转 blocked（未 ACK 事件全部保留，需人工处理）`)
          await this.setPhase(task, 'blocked', now)
        }
        continue
      }
      const records = batches[0] ?? []
      if (records.length === 0) continue
      try {
        const ack = await this.opts.api.sessionTaskEvents(
          task.assignmentId,
          { fence: task.fence, records },
          signal,
        )
        const ackedBatchIds: Array<{ batchId: string; seq: number }> = []
        for (let seq = acked + 1; seq <= ack.ack_seq; seq++) {
          const ev = task.pendingEvents.get(seq)
          task.pendingEvents.delete(seq)
          if (ev && ev.type === 'batch') {
            const bp = ev.payload as { batch_id?: string }
            if (typeof bp.batch_id === 'string') ackedBatchIds.push({ batchId: bp.batch_id, seq })
          }
        }
        task.store.ackUpTo(ack.ack_seq)
        if (task.store.pendingSyncCount === 0) writeAckedMarker(this.opts.runtimeHome, task.assignmentId, ack.ack_seq)
        task.syncBackoff = this.opts.syncRetryBaseMs
        task.syncNextAttemptAt = now + 50 // 还有剩余则快速继续
        // 消费 ACK 控制状态（评审 P1-6；审计九轮 P1：统一走任务级串行入口，
        // 旧响应按 epoch 拒绝——不回退 renew 路径已应用的控制）
        await this.applyControlUpdate(task, 'event_ack', {
          assignmentId: task.assignmentId,
          control_epoch: ack.control.control_epoch,
          server_control_seq: ack.control.server_control_seq,
          status: ack.control.status,
        })
        // 批次事件获 ACK 后才允许提交决策（评审 P1-2/P1-6）。只重新激活尚无
        // decision 事件的批次（ready/superseded 历史批次不重决策）；版本取
        // batchVersionById 冻结时绑定值（不用最新 task.inputVersion）
        for (const { batchId, seq } of ackedBatchIds) {
          if (
            task.decidedBatchIds.has(batchId) &&
            !task.decidedByDecisionIds.has(batchId) &&
            !task.decisionQueue.some((q) => q.batchId === batchId)
          ) {
            const inputVersion = task.batchVersionById.get(batchId) ?? task.inputVersion
            task.decisionQueue.push({ batchId, inputVersion, batchSeq: seq })
          }
        }
        if (task.decisionQueue.length > 0) await this.drainDecisionQueue(task, this.now())
      } catch (err) {
        if (err instanceof NetworkError) {
          task.syncNextAttemptAt = now + task.syncBackoff
          task.syncBackoff = Math.min(task.syncBackoff * 2, this.opts.syncRetryMaxMs)
          this.emit(`task ${task.taskId} 事件同步退避 ${task.syncBackoff}ms`)
        } else {
          // STALE 等 4xx：换代/过时——停止该 assignment 推进，等重新 claim
          this.emit(`task ${task.taskId} 事件同步被拒（等待重新分配）: ${err instanceof Error ? err.message : String(err)}`)
          this.dropTask(task)
          return
        }
      }
    }
  }

  // ---------------- 动作调度（每轮一个动作单元） ----------------

  private dispatchOne(now: number): void {
    const entry = this.queue.peek(now, { excludeTaskId: this.lastServedTaskId ?? undefined })
    if (entry === null) return
    const task = this.tasks.get(entry.taskId)
    if (!task) {
      this.queue.remove(entry.taskId)
      return
    }
    if (task.busy) return // 在飞互斥：本轮让给其他任务
    this.queue.markServed(task.taskId, now)
    this.lastServedTaskId = task.taskId
    task.busy = true
    void this.actionUnit(task, now)
      .catch((err: unknown) => {
        this.emit(`task ${task.taskId} 动作单元异常: ${err instanceof Error ? err.message : String(err)}`)
      })
      .finally(() => {
        task.busy = false
      })
  }

  private async actionUnit(task: TaskRuntime, now: number): Promise<void> {
    // 统一门禁检查（审计四）：所有动作入口——blocked 或门禁关闭（暂停/租约
    // 失效/恢复阻断）时不做任何观察/决策/执行；事件补交（syncPending）在主
    // 循环独立执行，不受此限制
    if (task.phase === 'blocked' || task.gate !== 'open') {
      this.queue.set(task.taskId, 'observe', now + 5_000)
      return
    }
    // A coverage gap is a retryable read problem. Keep the acknowledged watermark
    // and pending work; require a continuous read before starting more decisions
    // or sends. Already-created execution retains its existing result lifecycle.
    if (task.gapStreak > 0 && task.phase !== 'executing') {
      await this.observeOnce(task, now)
      return
    }
    // Recovery may find new input while an earlier decision is still in flight.
    // Freeze that input first, then keep polling the existing decision even if
    // observeOnce changed the phase to observing. Never orphan its concurrency slot.
    if (task.phase !== 'executing' && task.pendingBatch) {
      await this.observeOnce(task, now)
      return
    }
    if (task.phase !== 'executing' && task.inFlight) {
      await this.pollDecision(task, now)
      return
    }
    // 每个动作单元先尝试冲填决策队列（P1-6：定时重试入口，不依赖特定相位）
    if (task.decisionQueue.length > 0 && !task.inFlight) {
      await this.drainDecisionQueue(task, now)
      if (task.inFlight) return // 刚提交了决策，本轮结束
    }
    switch (task.phase) {
      case 'decision_pending':
        await this.pollDecision(task, now)
        return
      case 'send_ready': {
        // 尝试物化发送；未推进（重试等待期）继续观察——等待执行期间对方新消息
        // 必须能 supersede 待执行决策（设计 §9/§7，C2 语义保留）
        const progressed = await this.executeSend(task, now)
        if (!progressed) await this.observeOnce(task, now)
        return
      }
      case 'executing':
        await this.driveExecution(task, now)
        return
      case 'waiting_peer':
      case 'ready':
      case 'observing':
      case 'sync_pending':
        await this.observeOnce(task, now)
        return
    }
  }

  /** 一次观察动作（共享桌面锁内调用观察器；结果先落日志再推进内存） */
  private async observeOnce(task: TaskRuntime, now: number): Promise<void> {
    if (this.diskStopNew) {
      // P2-8：磁盘上限——停止新观察持久化（不删未 ACK 数据，仅等待/清理）
      this.queue.set(task.taskId, 'observe', now + 30_000)
      return
    }
    if (task.gate !== 'open') {
      // 门禁（评审 P1-6）：暂停/租约失效 → 不观察不决策；仅等同步通道与续租
      this.queue.set(task.taskId, 'observe', now + 2_000)
      return
    }
    if (now < task.observeDueAt) {
      this.queue.set(task.taskId, 'observe', task.observeDueAt)
      return
    }
    let result: ObserverResult
    try {
      result = await this.withLock(() =>
        this.opts.observer(
          {
            taskId: task.taskId,
            conversationBindingId: task.conversationBindingId,
            expectedBindingVersion: task.expectedBindingVersion,
            expectedAccountIdentityVersion: task.expectedAccountIdentityVersion,
            targetName: taskTargetName(task.spec),
          },
          { watermark: task.watermark },
        ),
      )
    } catch (err) {
      // 观察失败（引擎不可用/超时）：退避，不猜测
      task.observeBackoffIndex = Math.min(task.observeBackoffIndex + 1, OBSERVE_BACKOFF.length - 1)
      task.observeDueAt = now + OBSERVE_BACKOFF[task.observeBackoffIndex]!
      this.queue.set(task.taskId, 'observe', task.observeDueAt)
      this.emit(`task ${task.taskId} 观察失败退避: ${err instanceof Error ? err.message : String(err)}`)
      return
    }
    task.lastObservationAt = now
    // 身份三字段校验（评审 P1-9，session_observer_v1 契约）：任一不符 → blocked，
    // 在飞决策作废（不推进水位、不再观察）
    if (
      result.conversation_binding_id !== task.conversationBindingId ||
      result.binding_version !== task.expectedBindingVersion ||
      result.account_identity_version !== task.expectedAccountIdentityVersion
    ) {
      await this.logEvent(task, 'observation', { observation_id: result.observation_id, outcome: 'identity_mismatch' }, now)
      task.inFlight = null
      task.sendReady = null
      task.decisionQueue = []
      await this.setPhase(task, 'blocked', now)
      this.emit(`task ${task.taskId} 观察身份不符，blocked（决策已作废）`)
      return
    }
    if (result.coverage === 'gap') {
      // Bounded reads with capped backoff, not a permanent stop after three OCR
      // failures. Never reset the baseline or discard unsent messages to recover.
      task.gapStreak += 1
      await this.logEvent(task, 'observation', { observation_id: result.observation_id, outcome: 'gap', reason: result.gap_reason, streak: task.gapStreak }, now)
      if (task.gapStreak === 3) this.emit(`task ${task.taskId} 连续 3 次 coverage gap，保留水位并退避重读`)
      task.observeBackoffIndex = Math.min(task.observeBackoffIndex + 1, OBSERVE_BACKOFF.length - 1)
      task.observeDueAt = now + OBSERVE_BACKOFF[task.observeBackoffIndex]!
      this.queue.set(task.taskId, 'observe', task.observeDueAt)
      return
    }
    if (result.coverage === 'unavailable') {
      await this.logEvent(task, 'observation', { observation_id: result.observation_id, outcome: 'unavailable', reason: result.gap_reason }, now)
      task.observeBackoffIndex = Math.min(task.observeBackoffIndex + 1, OBSERVE_BACKOFF.length - 1)
      task.observeDueAt = now + OBSERVE_BACKOFF[task.observeBackoffIndex]!
      this.queue.set(task.taskId, 'observe', task.observeDueAt)
      return
    }
    if (task.gapStreak > 0) {
      await this.logEvent(task, 'observation', { observation_id: result.observation_id, outcome: 'recovered', streak: task.gapStreak }, now)
      this.emit(`task ${task.taskId} 消息连续性恢复，接续原水位`)
      task.observeBackoffIndex = 0
    }
    task.gapStreak = 0
    // 基线语义（设计 §6：新启用只建基线，不回复旧消息）：首次观察到的窗口是
    // 历史锚点——水位锚到最后一条，不形成批次、不触发决策
    if (!task.watermark) {
      const anchorMsg = result.ordered_messages[result.ordered_messages.length - 1]
      const newWatermark = {
        last_local_message_id: anchorMsg?.local_message_id ?? null,
        window_fingerprint: result.window_fingerprint ?? 'fp_unknown',
      }
      // P1-7：写盘先行——失败则不推水位（本动作单元抛错，下轮重新建基线）
      await this.logEvent(
        task,
        'baseline',
        { observation_id: result.observation_id, watermark: { ...newWatermark }, historical_count: result.ordered_messages.length },
        now,
      )
      task.watermark = newWatermark
      task.observeBackoffIndex = 0
      task.observeDueAt = now + OBSERVE_BACKOFF[0]!
      this.queue.set(task.taskId, 'observe', task.observeDueAt)
      this.maybeEnqueueOpening(task, now)
      return
    }
    // complete_window：新消息合批（静默窗口 + 最长聚合；工作时段判断属 spec，C2 先全时）
    const newMessages = result.ordered_messages
    // 最长聚合到期：即使仍有新到消息也立即冻结当前批次（设计 §6 最长 10s）
    if (task.pendingBatch && now - task.pendingBatch.firstNewAt >= this.opts.batchMaxWaitMs) {
      const unseen = newMessages.filter((m) => !task.pendingBatch!.messages.some((p) => p.local_message_id === m.local_message_id))
      task.pendingBatch.messages.push(...unseen)
      await this.formBatch(task, now)
      return
    }
    if (newMessages.length > 0) {
      if (!task.pendingBatch) {
        task.pendingBatch = { batchId: randomUUID(), messages: [], firstNewAt: now }
        task.inputVersion += 1
      }
      // 聚合期水位未推进，观察器会重复返回同批消息：按 local_message_id 去重
      const unseen = newMessages.filter((m) => !task.pendingBatch!.messages.some((p) => p.local_message_id === m.local_message_id))
      task.pendingBatch.messages.push(...unseen)
      task.observeBackoffIndex = 0
      task.observeDueAt = now + Math.min(500, this.opts.batchSilenceMs) // 聚合期高频复查
      this.queue.set(task.taskId, 'observe', task.observeDueAt)
      await this.setPhase(task, 'observing', now, { skipLog: true })
      return
    }
    // 无新消息：水位指纹刷新（锚点不变）
    if (result.window_fingerprint && task.watermark) task.watermark.window_fingerprint = result.window_fingerprint
    // 批次到期？
    if (task.pendingBatch && (now - task.pendingBatch.firstNewAt >= this.opts.batchSilenceMs || now - task.pendingBatch.firstNewAt >= this.opts.batchMaxWaitMs)) {
      await this.formBatch(task, now)
      return
    }
    if (task.phase === 'observing') {
      // 静默未满：等待窗口到期
      const due = (task.pendingBatch?.firstNewAt ?? now) + this.opts.batchSilenceMs
      task.observeDueAt = Math.min(task.observeDueAt, due)
    } else {
      task.observeBackoffIndex = Math.min(task.observeBackoffIndex + (task.phase === 'waiting_peer' ? 1 : 0), OBSERVE_BACKOFF.length - 1)
      task.observeDueAt = now + OBSERVE_BACKOFF[task.observeBackoffIndex]!
    }
    this.queue.set(task.taskId, 'observe', task.observeDueAt)
  }

  /** 基线建立后提交开场白决策（§13.2：不调模型、仅一次；合成批次免 ACK 门禁）。
   * 已有新入站在聚合时不提交——服务端也按"已有接纳批次"拒绝/取消。 */
  private maybeEnqueueOpening(task: TaskRuntime, now: number): void {
    if (task.freshBaseline) return // 人工恢复重建基线不再发起开场白
    const opening = task.spec['opening_text']
    if (typeof opening !== 'string' || !opening) return
    if (task.decidedByDecisionIds.has('opening')) return
    if (task.decisionQueue.some((q) => q.batchId === 'opening')) return
    if (task.pendingBatch) return
    task.decisionQueue.push({ batchId: 'opening', inputVersion: 0, batchSeq: 0, kind: 'opening' })
    this.queue.set(task.taskId, 'send', now + 50)
    this.emit(`task ${task.taskId} 基线已建立，入队开场白决策`)
  }

  /** 批次冻结：先落日志（成功后才清 pendingBatch/推水位——评审 P1-7 防丢批次），
   * payload 顶层 batch_id/input_version 与 C1 事件协议一致（评审 P1-2）；
   * 决策提交延后到该事件获云端 ACK（drainDecisionQueue）。 */
  private async formBatch(task: TaskRuntime, now: number): Promise<void> {
    const batch = task.pendingBatch
    if (!batch || batch.messages.length === 0) {
      task.pendingBatch = null
      return
    }
    // 新批次冻结：未提交的开场白让位（§13.2 已有新入站 → 取消 opening）；
    // 已提交的 opening 由云端在批次接纳时 superseded，prepare-send 会放弃
    if (task.decisionQueue.some((q) => q.batchId === 'opening')) {
      task.decisionQueue = task.decisionQueue.filter((q) => q.batchId !== 'opening')
      this.emit(`task ${task.taskId} 新入站消息到达，取消未提交的开场白`)
    }
    const lastMsg = batch.messages[batch.messages.length - 1]
    const watermarkAfter = {
      last_local_message_id: lastMsg?.local_message_id ?? task.watermark?.last_local_message_id ?? null,
      window_fingerprint: task.watermark?.window_fingerprint ?? 'fp_unknown',
    }
    // 1) 落盘先行：失败则保留 pendingBatch（本轮动作单元抛错，下轮重试）
    await this.logEvent(
      task,
      'batch',
      {
        batch_id: batch.batchId,
        input_version: task.inputVersion,
        conversation_binding_id: task.conversationBindingId,
        messages: batch.messages,
        watermark_after: { ...watermarkAfter },
      },
      now,
    )
    // 2) 落盘成功才推进内存状态
    task.pendingBatch = null
    task.watermark = watermarkAfter
    task.decidedBatchIds.add(batch.batchId)
    task.batchVersionById.set(batch.batchId, task.inputVersion)
    task.decisionQueue.push({ batchId: batch.batchId, inputVersion: task.inputVersion, batchSeq: task.store.lastLocalSeq })
    // ACK 后由 drainDecisionQueue 提交（P1-6：batchSeq 门禁防止提前提交）
  }

  /** 在飞决策计数（评审 P1-3：含网络请求中的 submitting 占位） */
  private inFlightDecisionCount(): number {
    let n = 0
    for (const t of this.tasks.values()) if (t.inFlight || t.submitting) n++
    return n
  }

  /** 决策队列冲刷（评审 P1-6/P1-7）：批次事件 ACK 后才可提交；并发满/断网保留队列 */
  private async drainDecisionQueue(task: TaskRuntime, now: number): Promise<void> {
    if (task.gapStreak > 0) return // Event ACK may arrive while a read is recovering.
    if (task.submitting) return // 任务级提交锁：同一任务提交在途，先占任务再占全局
    while (task.decisionQueue.length > 0 && !task.inFlight) {
      const head = task.decisionQueue[0]!
      if (task.store.ackedLocalSeq < head.batchSeq) {
        // ACK 门禁：批次事件尚未获云端 ACK（C1 查不到批次），等同步完成再冲刷
        this.queue.set(task.taskId, 'send', now + 500)
        return
      }
      if (this.inFlightDecisionCount() >= this.opts.maxInFlightDecisions) {
        // 全局在飞满（含 submitting 占位）：保留队列；所有决策完成时统一冲刷
        for (const other of this.tasks.values()) this.queue.set(other.taskId, 'send', now + 500)
        return
      }
      if (task.gate !== 'open') return // 门禁关闭：不提交新决策（评审 P1-6）
      // 评审 P1-3：网络请求前原子占位（submitting=true 计入并发名额）
      task.submitting = true
      try {
        await this.submitDecision(task, head.batchId, head.inputVersion, now, head.kind ?? 'reply')
        task.decisionQueue.shift()
      } catch (err) {
        if (err instanceof NetworkError) {
          this.queue.set(task.taskId, 'send', now + 1_000)
          return
        }
        throw err
      } finally {
        task.submitting = false
      }
    }
  }

  private async submitDecision(
    task: TaskRuntime,
    batchId: string,
    inputVersion: number,
    now: number,
    kind: 'reply' | 'opening' = 'reply',
  ): Promise<void> {
    let resp: { decision_id: string; status: string }
    try {
      resp = await this.opts.api.sessionTaskCreateDecision(
        task.assignmentId,
        {
          fence: task.fence,
          batch_id: batchId,
          decision_kind: kind,
          input_version: inputVersion,
          control_epoch: task.controlEpoch,
          spec_revision: task.specRevision,
        },
      )
    } catch (err) {
      // opening 409：已存在（跨版本唯一）或已有新入站被取消（§13.2）——终态
      // 处理不重试；重新主动开场须关闭旧任务并新建授权任务
      if (kind === 'opening' && err instanceof ApiError && err.status === 409) {
        await this.logEvent(
          task,
          'decision_phase',
          {
            batch_id: batchId,
            decision_kind: kind,
            status: 'superseded',
            phase_from: task.phase,
            phase_to: 'waiting_peer',
            input_version: inputVersion,
          },
          now,
        )
        task.decidedByDecisionIds.add(batchId)
        task.phase = 'waiting_peer'
        this.queue.set(task.taskId, 'observe', now + OBSERVE_BACKOFF[0]!)
        return
      }
      throw err
    }
    // 单条事务记录：decision 状态与相位迁移合并为一条 decision_phase 落盘。
    // 写盘失败则内存全不推进（inFlight/decidedByDecisionIds/phase 都不设，
    // 批次保留在队列头部——shift 仅在成功后执行），下轮重提交——消除
    // decision+phase 两段写盘的中间态窗口（决策已标但相位未迁移会卡调度）。
    await this.logEvent(
      task,
      'decision_phase',
      {
        decision_id: resp.decision_id,
        batch_id: batchId,
        decision_kind: kind,
        status: resp.status,
        phase_from: task.phase,
        phase_to: 'decision_pending',
        input_version: inputVersion,
      },
      now,
    )
    // 落盘成功后一次性发布全部内存状态（不再二次 setPhase 写盘）
    task.decidedByDecisionIds.add(batchId)
    task.inFlight = { decisionId: resp.decision_id, batchId, inputVersion, pollAt: now + 1_000 }
    task.phase = 'decision_pending'
    this.queue.set(task.taskId, 'send', task.inFlight.pollAt)
  }

  /** 决策轮询（纯网络查询；不是 LLM 调用，不占桌面锁） */
  private async pollDecision(task: TaskRuntime, now: number): Promise<void> {
    const inflight = task.inFlight
    if (!inflight) {
      await this.setPhase(task, 'waiting_peer', now)
      return
    }
    if (now < inflight.pollAt) {
      this.queue.set(task.taskId, 'send', inflight.pollAt)
      return
    }
    const decision = await this.opts.api.sessionTaskGetDecision(task.assignmentId, inflight.decisionId)
    if (task.inFlight !== inflight || task.gate !== 'open' || this.tasks.get(task.taskId) !== task) return
    inflight.pollAt = now + 1_000 // 默认 1s 查询（契约 §9）
    if (decision.status === 'ready') {
      // 冻结动作分流（§9）：reply → send_ready 接执行链；wait/handoff/done →
      // 不发送，直接回 waiting_peer（handoff/done 的任务侧状态由云端推进）
      const action = decision.action ?? 'reply'
      const phaseTo = action === 'reply' ? 'send_ready' : 'waiting_peer'
      // 单条事务记录：决策终态与相位迁移合并落盘——写盘失败保留 inFlight
      // （下轮重新轮询本决策），成功才一次性清内存并迁移相位
      await this.logEvent(
        task,
        'decision_phase',
        {
          decision_id: decision.decision_id,
          batch_id: inflight.batchId,
          status: 'ready',
          action,
          phase_from: task.phase,
          phase_to: phaseTo,
          input_version: inflight.inputVersion,
        },
        now,
      )
      task.decidedByDecisionIds.add(inflight.batchId)
      task.inFlight = null
      if (phaseTo === 'send_ready') {
        task.sendReady = {
          decisionId: decision.decision_id,
          batchId: inflight.batchId,
          inputVersion: inflight.inputVersion,
        }
        task.phase = 'send_ready'
        this.queue.set(task.taskId, 'send', now + 50)
      } else {
        task.phase = 'waiting_peer'
        task.observeDueAt = now + OBSERVE_BACKOFF[0]!
        this.queue.set(task.taskId, 'observe', task.observeDueAt)
      }
      await this.drainDecisionQueue(task, now) // 并发释放：冲刷待提交批次
      return
    }
    if (decision.status === 'superseded' || decision.status === 'failed') {
      // 同 ready：单条事务记录，失败保留跟踪待下轮重查
      await this.logEvent(
        task,
        'decision_phase',
        {
          decision_id: decision.decision_id,
          batch_id: inflight.batchId,
          status: decision.status,
          phase_from: task.phase,
          phase_to: 'waiting_peer',
          input_version: inflight.inputVersion,
        },
        now,
      )
      task.decidedByDecisionIds.add(inflight.batchId)
      task.inFlight = null
      task.phase = 'waiting_peer'
      this.queue.set(task.taskId, 'observe', now + OBSERVE_BACKOFF[0]!)
      await this.drainDecisionQueue(task, now)
      return
    }
    await this.setPhase(task, 'decision_pending', now, { skipLog: true })
    this.queue.set(task.taskId, 'send', inflight.pollAt)
  }

  /** send_ready → prepare-send：服务端幂等物化单条底座执行单元（§9）。
   * 返回是否推进了相位（true=已进入 executing/已放弃回 waiting_peer；
   * false=重试等待期，保持 send_ready——调用方继续观察以便新消息 supersede）。
   * 决策 superseded/版本失配返回 invocation_id=null → 放弃发送相位回
   * waiting_peer；工作时段外/网络失败按间隔重试（不放弃决策）。 */
  private async executeSend(task: TaskRuntime, now: number): Promise<boolean> {
    const sr = task.sendReady
    if (!sr) {
      await this.setPhase(task, 'waiting_peer', now)
      return true
    }
    if (this.diskStopNew) {
      this.queue.set(task.taskId, 'send', now + 30_000)
      return false
    }
    let resp: SessionTaskPrepareSendResult
    try {
      resp = await this.opts.api.sessionTaskPrepareSend(task.assignmentId, sr.decisionId, task.fence)
    } catch (err) {
      if (err instanceof NetworkError) {
        this.queue.set(task.taskId, 'send', now + 1_000)
        return false
      }
      if (err instanceof ApiError && err.status === 409 && (err.code === 'WORK_WINDOW_CLOSED' || (err.serverMessage ?? err.message).includes('WORK_WINDOW_CLOSED'))) {
        this.queue.set(task.taskId, 'send', now + 30_000)
        return false
      }
      // 其他 4xx/5xx（预算停止等由 renew 控制传播）：稍后重试
      this.emit(`task ${task.taskId} prepare-send 失败（稍后重试）: ${err instanceof Error ? err.message : String(err)}`)
      this.queue.set(task.taskId, 'send', now + 5_000)
      return false
    }
    if (!resp.invocation_id) {
      await this.logEvent(
        task,
        'decision_phase',
        {
          decision_id: sr.decisionId,
          batch_id: sr.batchId,
          status: resp.decision_status ?? 'superseded',
          phase_from: 'send_ready',
          phase_to: 'waiting_peer',
          input_version: sr.inputVersion,
        },
        now,
      )
      task.sendReady = null
      task.phase = 'waiting_peer'
      this.queue.set(task.taskId, 'observe', now + OBSERVE_BACKOFF[0]!)
      await this.drainDecisionQueue(task, now)
      return true
    }
    await this.logEvent(
      task,
      'execution_phase',
      {
        decision_id: sr.decisionId,
        invocation_id: resp.invocation_id,
        phase_from: 'send_ready',
        phase_to: 'executing',
        input_version: sr.inputVersion,
      },
      now,
    )
    task.execution = { invocationId: resp.invocation_id, decisionId: sr.decisionId, inputVersion: sr.inputVersion }
    task.sendReady = null
    task.phase = 'executing'
    this.queue.set(task.taskId, 'send', now + 50)
    return true
  }

  /**
   * 锁内会话复核（§7 顺序 4，C3 门禁 #1）：在 runner 桌面锁内、许可申请之前
   * 名称场景复核任务门控和已接纳输入版本，标题由发送 Provider 的唯一截图确认。
   * 旧绑定场景重新观察目标会话并比对决策水位。任何新消息（peer/self/system——生成回复后
   * 客户追加、排队期间人工回复、opening 前新入站）、身份漂移、覆盖缺口、观察
   * 失败、assignment 门禁失效 → 抛 SessionPrecheckError，不申请许可不发送；
   * 新消息由后续常规观察周期合批上报（水位未推进，不丢事实），旧决策由云端
   * supersede 作废。
   */
  private async lockedSessionRecheck(task: TaskRuntime, frozenInputVersion?: number): Promise<void> {
    const { SessionPrecheckError } = await import('../invocationRunner.js')
    if (task.gate !== 'open' || this.now() >= task.leaseDeadline) {
      throw new SessionPrecheckError('ASSIGNMENT_STALE', '任务门禁关闭或本地租约失效')
    }
    // #2 输入版本硬门禁：决策生成后本地已形成更新输入版本（含已落盘未 ACK 的
    // 批次/聚合中批次）——旧发送绑定旧版本，一律不得执行（观察"水位后无新消息"
    // 不足以判定：批次可能尚未推进水位）
    if (frozenInputVersion !== undefined && (task.inputVersion > frozenInputVersion || task.pendingBatch !== null)) {
      this.emit(`task ${task.taskId} 发送前输入版本已前进（冻结 ${frozenInputVersion}，当前 ${task.inputVersion}${task.pendingBatch ? '+聚合中' : ''}），取消本次发送`)
      throw new SessionPrecheckError('INPUT_VERSION_STALE', '决策生成后输入版本已前进（新消息/人工回复待同步）')
    }
    // Name sends confirm the contact and locate the composer in one fresh capture
    // inside the same desktop lock as paste + Enter. Do not add a second full
    // observation here; accepted input versions above still invalidate stale work.
    if (taskTargetName(task.spec)) return
    let result: ObserverResult
    try {
      // 已在 runner 桌面锁内执行，不再重复取锁（观察与发送共用同一临界区）
      result = await this.opts.observer(
        {
          taskId: task.taskId,
          conversationBindingId: task.conversationBindingId,
          expectedBindingVersion: task.expectedBindingVersion,
          expectedAccountIdentityVersion: task.expectedAccountIdentityVersion,
            targetName: taskTargetName(task.spec),
        },
        { watermark: task.watermark },
      )
    } catch (err) {
      throw new SessionPrecheckError('OBSERVE_FAILED', `发送前观察失败: ${err instanceof Error ? err.message : String(err)}`)
    }
    if (
      result.conversation_binding_id !== task.conversationBindingId ||
      result.binding_version !== task.expectedBindingVersion ||
      result.account_identity_version !== task.expectedAccountIdentityVersion
    ) {
      throw new SessionPrecheckError('IDENTITY_MISMATCH', '发送前观察身份不符')
    }
    if (result.coverage !== 'complete_window') {
      throw new SessionPrecheckError('COVERAGE_GAP', `发送前观察覆盖缺口: ${result.coverage}`)
    }
    const watermarkId = task.watermark?.last_local_message_id ?? null
    const fresh = result.ordered_messages.filter((m) => m.local_message_id !== watermarkId)
    if (fresh.length > 0) {
      this.emit(`task ${task.taskId} 发送前复核发现 ${fresh.length} 条新消息，取消本次发送（旧决策待 supersede）`)
      throw new SessionPrecheckError('NEW_MESSAGES', '决策生成后有新消息/人工回复')
    }
  }

  /** executing → 定向 claim → 既有 v2 执行器（write-authorize/journal/outbox
   * 全在既有链内，不复制发送实现）→ 回 waiting_peer。不可领取（在途/终态）
   * 时按状态收敛：在途等待，终态直接结束（结果已由 outbox/operation-result 上报）。 */
  private async driveExecution(task: TaskRuntime, now: number): Promise<void> {
    const ex = task.execution
    if (!ex) {
      await this.setPhase(task, 'waiting_peer', now)
      return
    }
    const runner = this.opts.runInvocation
    if (runner === undefined) {
      this.emit(`task ${task.taskId} 未接入 v2 执行器（runInvocation 缺失），执行相位保守 blocked`)
      await this.logEvent(
        task,
        'execution_phase',
        {
          decision_id: ex.decisionId,
          invocation_id: ex.invocationId,
          outcome: 'runner_unavailable',
          phase_from: 'executing',
          phase_to: 'blocked',
        },
        now,
      )
      task.phase = 'blocked'
      return
    }
    if (this.diskStopNew) {
      // §8 磁盘 100%：停新发送（已上报结果的迟到回执不受影响）
      this.queue.set(task.taskId, 'send', now + 30_000)
      return
    }
    let claim: SessionTaskTargetedClaim
    try {
      claim = await this.opts.api.sessionTaskClaimInvocation(task.assignmentId, ex.invocationId, task.fence)
    } catch (err) {
      if (err instanceof NetworkError) {
        this.queue.set(task.taskId, 'send', now + 1_000)
        return
      }
      if (err instanceof ApiError && err.status === 404) {
        // invocation 不存在（数据异常/被清理）：终态收敛 blocked，不死循环重试
        this.emit(`task ${task.taskId} 定向 claim 404（invocation 缺失），保守 blocked`)
        await this.logEvent(
          task,
          'execution_phase',
          { decision_id: ex.decisionId, invocation_id: ex.invocationId, outcome: 'invocation_missing', phase_from: 'executing', phase_to: 'blocked' },
          now,
        )
        task.phase = 'blocked'
        return
      }
      this.emit(`task ${task.taskId} 定向 claim 失败（稍后重试）: ${err instanceof Error ? err.message : String(err)}`)
      this.queue.set(task.taskId, 'send', now + 5_000)
      return
    }
    const inv = claim.invocation
    if (inv === null) {
      if (claim.state === 'queued' || claim.state === 'claimed' || claim.state === 'running') {
        // 在途（本实例前次崩溃/旧进程）：等待终态——unknown 不重发（设计 §8）
        this.queue.set(task.taskId, 'send', now + 2_000)
        return
      }
      await this.finishExecution(task, ex, now, claim.state || 'terminal')
      return
    }
    const sessionPrecheck = async (): Promise<void> => {
      await this.lockedSessionRecheck(task, ex.inputVersion)
    }
    try {
      await runner(inv, sessionPrecheck)
    } catch (err) {
      // runner 内部终态走 result outbox 必达链；此处异常不改判、不重发
      this.emit(`task ${task.taskId} v2 执行异常（结果以上报链为准）: ${err instanceof Error ? err.message : String(err)}`)
    }
    await this.finishExecution(task, ex, now, 'reported')
  }

  /** 执行收尾：单条事务记录 + 相位迁移 + 冲刷后续决策队列 */
  private async finishExecution(
    task: TaskRuntime,
    ex: { invocationId: string; decisionId: string; inputVersion: number },
    now: number,
    outcome: string,
  ): Promise<void> {
    await this.logEvent(
      task,
      'execution_phase',
      {
        decision_id: ex.decisionId,
        invocation_id: ex.invocationId,
        outcome,
        phase_from: 'executing',
        phase_to: 'waiting_peer',
      },
      now,
    )
    task.execution = null
    task.phase = 'waiting_peer'
    task.observeDueAt = now + OBSERVE_BACKOFF[0]!
    this.queue.set(task.taskId, 'observe', task.observeDueAt)
    await this.drainDecisionQueue(task, now)
  }

  /** 周期 retention（P2-8：每 60s 评估；stopNew 拦截新观察写入） */
  private async checkRetention(now: number): Promise<void> {
    if (now - this.lastRetentionAt < 60_000) return
    this.lastRetentionAt = now
    try {
      // No assignment has trusted deletion eligibility yet. Disk limits still apply;
      // replaying retained history here would block renewals for no cleanup benefit.
      const result = enforceRetention(this.opts.runtimeHome, [], { now })
      const wasStop = this.diskStopNew
      this.diskStopNew = result.stopNew
      if (result.stopNew && !wasStop) this.emit('本地会话日志达 256MiB 上限：停止新观察持久化与新发送')
      if (!result.stopNew && wasStop) this.emit('磁盘水位恢复：重新开放观察')
      if (result.deletedAssignmentIds.length > 0) this.emit(`retention 清理 ${result.deletedAssignmentIds.length} 个终态 assignment`)
    } catch (err) {
      this.emit(`retention 评估异常: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  // ---------------- 日志与相位 ----------------

  private async logEvent(task: TaskRuntime, type: string, payload: unknown, _now: number): Promise<void> {
    const eventId = `${type}-${randomUUID()}`
    const record = await task.store.appendEncrypted(eventId, type, payload, task.fence)
    task.pendingEvents.set(record.local_seq, { event_id: record.event_id, type, payload })
    task.syncNextAttemptAt = 0 // 有新事实立即冲刷
  }

  /**
   * 相位迁移（评审三轮 P1-4：真写盘先行）。异步等待日志 fsync 成功后才更新
   * 内存 phase 并发布队列调度；失败不改 phase（调用方下轮重试同一动作）。
   * skipLog=true 的内部迁移不落盘（如 observing 期间的静默标记）。
   */
  private async setPhase(task: TaskRuntime, to: TaskPhase, now: number, opts?: { skipLog?: boolean }): Promise<void> {
    if (task.phase === to) return
    const from = task.phase
    if (opts?.skipLog) {
      task.phase = to
      this.queue.set(task.taskId, queueKindFor(to), now + 50)
      return
    }
    try {
      const record = await task.store.appendEncrypted(`phase-${randomUUID()}`, 'phase', { from, to }, task.fence)
      task.pendingEvents.set(record.local_seq, { event_id: record.event_id, type: 'phase', payload: { from, to } })
      task.syncNextAttemptAt = 0
      task.phase = to // 写盘成功后才发布
      this.queue.set(task.taskId, queueKindFor(to), now + 50)
    } catch (err) {
      this.emit(`task ${task.taskId} phase 日志写入失败（保持 ${from}，下轮重试）: ${err instanceof Error ? err.message : String(err)}`)
      // 不改 phase；调用方已在 await——异常传播给 actionUnit catch 处理
      throw err
    }
  }

  private dropTask(task: TaskRuntime): void {
    if (this.tasks.get(task.taskId) !== task) return
    this.tasks.delete(task.taskId)
    this.queue.remove(task.taskId)
    this.emit(`task ${task.taskId} 移出调度（assignment 过时，等待重新 claim）`)
  }

  /** 测试/观测辅助：某任务本地日志路径 */
  logPathFor(taskId: string): string | null {
    const t = this.tasks.get(taskId)
    return t ? sessionTaskDir(this.opts.runtimeHome, t.assignmentId) : null
  }
}

import { writeFileSync, mkdirSync } from 'node:fs'
import { join as pathJoin } from 'node:path'

function writeAckedMarker(runtimeHome: string, assignmentId: string, ackedSeq: number): void {
  try {
    const dir = pathJoin(runtimeHome, 'session-tasks', assignmentId)
    mkdirSync(dir, { recursive: true })
    writeFileSync(pathJoin(dir, '.acked'), String(ackedSeq), 'utf-8')
  } catch { /* best effort */ }
}

/** 单条线上记录字节数（含 local_seq，对齐 C1 json.dumps 整条 record 的紧凑口径）。
 * batchEventsForSync 与 flushRecovery 的超限判断共用同一口径。 */
function syncRecordBytes(ev: { local_seq: number; event_id: string; type: string; payload: unknown }): number {
  return Buffer.byteLength(JSON.stringify({ local_seq: ev.local_seq, event_id: ev.event_id, type: ev.type, payload: ev.payload }))
}

/** 按 C1 限制分批（评审 P1-5：条数 ≤100 且序列化 ≤256KiB；单条超限显式报错）。
 * 大小按完整线上记录口径计算（含 local_seq，对齐 C1 json.dumps 整条 record）。 */
function batchEventsForSync<T extends { local_seq: number; event_id: string; type: string; payload: unknown }>(events: T[]): { batches: T[][]; oversized: T[] } {
  const batches: T[][] = []
  const oversized: T[] = []
  let current: T[] = []
  let currentBytes = 0
  for (const ev of events) {
    const size = syncRecordBytes(ev)
    if (size > SYNC_MAX_BYTES) {
      oversized.push(ev)
      continue
    }
    if (current.length >= SYNC_MAX_RECORDS || currentBytes + size > SYNC_MAX_BYTES) {
      if (current.length > 0) batches.push(current)
      current = []
      currentBytes = 0
    }
    current.push(ev)
    currentBytes += size
  }
  if (current.length > 0) batches.push(current)
  return { batches, oversized }
}

function queueKindFor(phase: TaskPhase): QueueKind {
  if (phase === 'send_ready' || phase === 'executing' || phase === 'decision_pending') return 'send'
  if (phase === 'sync_pending') return 'control'
  return 'observe'
}

/** 从旧 assignment 回放事件提取全部未完成工作（与 restoreTaskFromLog 同一口径）：
 * 水位（最后一条 baseline/observation/batch）、全部冻结批次 ID + inputVersion、
 * 待提交批次（无 decision 事件的批次，含 ACK 门禁 local_seq）、在飞决策（最后
 * 一条 decision 非终态）、最后相位 */
function extractOldTaskWork(assignmentId: string, events: ReplayedEvent[]): OldTaskWork {
  const work: OldTaskWork = {
    oldAssignmentId: assignmentId,
    watermark: null,
    decidedBatches: new Map(),
    pendingBatches: new Map(),
    inFlightDecision: null,
    sendReadyDecision: null,
    executionInvocation: null,
    lastPhase: 'ready',
    unrecoverable: false,
  }
  const batchSeqById = new Map<string, number>()
  const decidedByDecisionIds = new Set<string>()
  let lastDecision: { decisionId: string; batchId: string; status: string } | null = null
  let sendReady: { decisionId: string; batchId: string; inputVersion: number } | null = null
  let execution: { invocationId: string; decisionId: string; inputVersion: number } | null = null
  for (const ev of events) {
    const p = ev.payload as {
      watermark?: ObserverWatermark
      watermark_after?: ObserverWatermark
      batch_id?: string
      input_version?: number
      to?: TaskPhase
      phase_to?: TaskPhase
      decision_id?: string
      invocation_id?: string
      status?: string
    }
    if (ev.record.type === 'baseline' || ev.record.type === 'observation') {
      if (p && p.watermark) work.watermark = p.watermark
    }
    if (ev.record.type === 'batch') {
      if (p && p.watermark_after) work.watermark = p.watermark_after
      if (p && typeof p.batch_id === 'string') {
        work.decidedBatches.set(p.batch_id, typeof p.input_version === 'number' ? p.input_version : 0)
        batchSeqById.set(p.batch_id, ev.record.local_seq)
      }
    }
    if (ev.record.type === 'phase' && p && p.to) work.lastPhase = p.to
    if (ev.record.type === 'decision_phase' && p && p.phase_to) {
      work.lastPhase = p.phase_to
      if (p.phase_to === 'send_ready' && typeof p.decision_id === 'string' && p.decision_id) {
        sendReady = {
          decisionId: p.decision_id,
          batchId: String(p.batch_id ?? ''),
          inputVersion: typeof p.input_version === 'number' ? p.input_version : 0,
        }
      }
      if (p.phase_to === 'waiting_peer') {
        sendReady = null
        execution = null
      }
    }
    if (ev.record.type === 'execution_phase' && p) {
      if (p.phase_to === 'executing' && typeof p.invocation_id === 'string' && typeof p.decision_id === 'string') {
        execution = {
          invocationId: p.invocation_id,
          decisionId: p.decision_id,
          inputVersion: typeof p.input_version === 'number' ? p.input_version : Number.MAX_SAFE_INTEGER,
        }
        sendReady = null
      }
      if (p.phase_to === 'waiting_peer') {
        sendReady = null
        execution = null
      }
      if (p.phase_to === 'blocked') work.lastPhase = 'blocked'
    }
    if (ev.record.type === 'decision' || ev.record.type === 'decision_phase') {
      if (p && typeof p.batch_id === 'string') decidedByDecisionIds.add(p.batch_id)
      lastDecision = {
        decisionId: String(p?.decision_id ?? ''),
        batchId: String(p?.batch_id ?? ''),
        status: String(p?.status ?? ''),
      }
    }
  }
  if (lastDecision && lastDecision.decisionId && !['ready', 'superseded', 'failed'].includes(lastDecision.status)) {
    work.inFlightDecision = lastDecision
  }
  work.sendReadyDecision = sendReady
  work.executionInvocation = execution
  for (const [batchId, seq] of batchSeqById) {
    if (decidedByDecisionIds.has(batchId)) continue
    work.pendingBatches.set(batchId, { inputVersion: work.decidedBatches.get(batchId) ?? 0, batchSeq: seq })
  }
  return work
}

function taskTargetName(spec: Record<string, unknown>): string | undefined {
  const target = spec._runtime_target as { policy?: string; target_name?: string } | undefined
  return target?.policy === "current_login_name" && typeof target.target_name === "string" ? target.target_name : undefined
}
