/**
 * SessionTaskEngine 验收测试（C2，计划 §5 验收项）：
 *
 * 1. 云端「发布后退出」：任务进 fake 队列后测试不再干预，引擎独立领取推进；
 * 2. A 等待（decision_pending/send_ready）时 B 可领取推进，互不阻塞；
 * 3. A 唤醒：等待中对新入站消息形成新批次（input_version 递增）并再决策；
 * 4. 崩溃恢复：引擎重启回放本地日志——水位/已决策批次恢复，同一消息不重复合批；
 * 5. 事件前缀 ACK 到云端；普通日志（emit）不含消息正文。
 * 6. 重启续租接续：租约仍有效时（C1 claim 不重新下发），重启实例对旧
 *    assignment 直接 renew 接续——不重新建基线，在飞决策与未 ACK 事件恢复；
 * 7. 租约过期换代保守阻断：旧 assignment renew 被拒后，同 task_id 新 assignment
 *    claim 时按恢复扫描提取的未完成工作持久化 blocked（recovery_blocked 加密
 *    事件，重启回放仍 blocked）——不迁移水位/不重建基线/不重复创建决策；
 *    事件补交（syncPending）不受阻断影响；
 * 8. meta 缺失/损坏不可归属（审计 P1）：旧日志存在但 meta.json 无法解析出
 *    task_id（缺失/空文件/截断/无 task_id）→ 恢复扫描标记不可归属，本设备
 *    新任务激活保守阻断（不建基线；重启回放 recovery_blocked 仍 blocked）；
 * 9. 运行中控制更新 meta 写入失败（审计 P1）：控制代变化先关门禁再持久化，
 *    失败不回开（停止观察/决策），解除故障后下轮续租重试成功并恢复。
 * 10. 控制更新乱序/并发一致性（审计九轮 P1）：renew 与事件 ACK 两条路径统一
 *     经任务级串行入口 applyControlUpdate——旧响应（epoch 更小）被拒绝，不
 *     回退控制状态、不重开门禁；同版本重复响应幂等；持久化失败窗口内旧 active
 *     被拒且解除后仅应用最新控制；任务换 assignment 后旧回调不影响新任务。
 * 11. 元数据重试纳入串行链（审计十轮）：renewDue 不再于链外重试 persistMeta
 *     ——旧重试悬挂期间新控制经链写入后，旧重试在链内完成也不会把磁盘覆盖回
 *     旧控制代；重试悬挂期间 assignment 换代则旧回调不写入新任务；多次失败后
 *     重试只持久化最新控制代；重启用最新持久化控制代续租。
 *
 * 专用 fake 云端在本文件内实现（不动共享 fakeCloud.ts）；观察器按 C0 冻结
 * 契约语义模拟（水位过滤 + complete_window/gap）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer, type Server } from 'node:http'
import { mkdtempSync, rmSync, existsSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { ApiClient } from '../src/apiClient.js'
import { SessionTaskEngine, type ObserverMessage, type ObserverResult, type ObserverWatermark } from '../src/sessionTasks/engine.js'
import type { SessionCrypto } from '../src/sessionTasks/sessionStore.js'

// ---------------- fake 会话云端 ----------------

interface FakeTask {
  task_id: string
  assignment_id: string
  spec: Record<string, unknown>
  fence: number
  control_epoch: number
  spec_revision: number
}

class FakeSessionCloud {
  private readonly server: Server
  private readonly queue: FakeTask[] = []
  private readonly claimed = new Map<string, FakeTask>() // assignment_id → task
  readonly eventsAcked = new Map<string, number>() // assignment_id → ack_seq
  readonly eventRecords = new Map<string, Array<{ local_seq: number; event_id: string; type: string }>>()
  readonly decisions = new Map<string, { assignmentId: string; batchId: string; status: string; polls: number }>()
  readonly createDecisionCalls: Array<{ assignmentId: string; batchId: string; fence: number; control_epoch: number }> = []
  /** renew 调用记录（对齐 C1：不校验 runtime_instance_id，只校验 fence/control_epoch） */
  readonly renewCalls: Array<{ assignmentId: string; fence: number; control_epoch: number }> = []
  readyAfterPolls = 1
  offline = false
  baseUrl = ''
  /** 控制状态注入（模拟服务端 pause/resume 换代）：renew/events ACK 返回注入值 */
  private readonly controlStatus = new Map<string, string>()
  /** renew 响应冻结/迟到注入（乱序测试）：holdRenew 后到达的 renew 请求挂起，
   * 响应按请求时刻的服务端状态冻结；releaseRenews 才送达——模拟网络乱序 */
  private readonly renewGates = new Map<string, Array<() => void>>()
  private readonly heldRenews = new Map<string, number>()
  /** 单次控制覆盖（迟到旧响应注入）：下一次 renew / events ACK 返回该控制 */
  nextRenewControl: { status: string; control_epoch: number } | null = null
  nextEventsControl: { status: string; control_epoch: number } | null = null

  holdRenew(assignmentId: string): void {
    if (!this.renewGates.has(assignmentId)) this.renewGates.set(assignmentId, [])
  }

  heldRenewCount(assignmentId: string): number {
    return this.heldRenews.get(assignmentId) ?? 0
  }

  releaseRenews(assignmentId: string): void {
    const list = this.renewGates.get(assignmentId)
    this.renewGates.delete(assignmentId)
    for (const r of list ?? []) r()
  }

  /** 注入服务端控制变化：改写 assignment 当前 control_epoch 与控制状态。renew
   * 对旧 control_epoch 放行（控制变化经 ACK 传播给 runtime——对齐引擎对
   * ack.control.control_epoch !== 本地 epoch 的处理路径） */
  setControl(assignmentId: string, control: { control_epoch: number; status: string }): void {
    const task = this.claimed.get(assignmentId)
    if (task) task.control_epoch = control.control_epoch
    this.controlStatus.set(assignmentId, control.status)
  }

  constructor() {
    this.server = createServer((req, res) => {
      void this.handle(req, res).catch(() => {
        res.statusCode = 500
        res.end()
      })
    })
  }

  enqueue(task: FakeTask): void {
    this.queue.push(task)
  }

  /** 模拟 C1 租约过期换代：旧 assignment 不再接受 renew/events（409 STALE）；
   * 同 task_id 的新 assignment 可正常 claim（claimed 按 assignment_id 记录） */
  expireAssignmentLease(assignmentId: string): void {
    this.claimed.delete(assignmentId)
  }

  async start(): Promise<string> {
    await new Promise<void>((resolve) => this.server.listen(0, '127.0.0.1', resolve))
    const addr = this.server.address()
    this.baseUrl = `http://127.0.0.1:${typeof addr === 'object' && addr ? addr.port : 0}`
    return this.baseUrl
  }

  async stop(): Promise<void> {
    // undici fetch 连接池保持 keep-alive：close 等待空闲连接会永久挂起，
    // 先强制断开所有连接再关（Node ≥18.2 closeAllConnections）
    const srv = this.server as Server & { closeAllConnections?: () => void }
    srv.closeAllConnections?.()
    await new Promise<void>((resolve) => this.server.close(() => resolve()))
  }

  decisionReady(decisionId: string): void {
    const d = this.decisions.get(decisionId)
    if (d) d.status = 'ready'
  }

  private async handle(req: import('node:http').IncomingMessage, res: import('node:http').ServerResponse): Promise<void> {
    if (this.offline) {
      req.socket.destroy()
      return
    }
    const url = new URL(req.url ?? '/', 'http://localhost')
    const path = url.pathname
    let body: Record<string, unknown> = {}
    if (req.method === 'POST') {
      body = await new Promise((resolve) => {
        let data = ''
        req.on('data', (c) => (data += c))
        req.on('end', () => {
          try {
            resolve(JSON.parse(data || '{}') as Record<string, unknown>)
          } catch {
            resolve({})
          }
        })
      })
    }
    const json = (status: number, payload: unknown) => {
      res.statusCode = status
      res.setHeader('content-type', 'application/json')
      res.end(JSON.stringify(payload))
    }
    const ok = (data: unknown) => json(200, { success: true, data })

    if (path === '/api/local-tools/runtime/session-tasks/claim' && req.method === 'POST') {
      const task = this.queue.shift()
      if (!task) {
        res.statusCode = 204
        res.end()
        return
      }
      this.claimed.set(task.assignment_id, task)
      ok({
        assignment_id: task.assignment_id,
        task_id: task.task_id,
        spec: task.spec,
        spec_revision: task.spec_revision,
        fence: task.fence,
        control_epoch: task.control_epoch,
        server_control_seq: 0,
        lease_seconds: 60,
        conversation_binding_id: String(task.spec.conversation_binding_id ?? ''),
        binding_version: 0,
        account_identity_version: 1,
      })
      return
    }
    const mRenew = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/renew$/.exec(path)
    if (mRenew && req.method === 'POST') {
      const assignmentId = mRenew[1] as string
      this.renewCalls.push({ assignmentId, fence: Number(body['fence']), control_epoch: Number(body['control_epoch']) })
      const task = this.claimed.get(assignmentId)
      // 对齐 C1 renew_assignment：查 assignment 存在 + fence（不查
      // runtime_instance_id——重启后的新实例可续租接续）；control_epoch 以服务
      // 端为准（控制换代经 ACK 返回值传播给 runtime）
      if (!task || Number(body['fence']) !== task.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'assignment 不存在或过时' })
        return
      }
      // 响应在请求到达时按此刻服务端状态冻结（供乱序/迟到响应测试）；可被单次
      // 注入覆盖为任意旧控制
      const injectedRenew = this.nextRenewControl
      this.nextRenewControl = null
      const renewControl = injectedRenew ?? {
        status: this.controlStatus.get(assignmentId) ?? 'active',
        control_epoch: task.control_epoch,
        server_control_seq: 0,
      }
      const gate = this.renewGates.get(assignmentId)
      if (gate) {
        this.heldRenews.set(assignmentId, (this.heldRenews.get(assignmentId) ?? 0) + 1)
        await new Promise<void>((resolve) => gate.push(resolve))
      }
      ok({ lease_seconds: 60, control: renewControl })
      return
    }
    const mEvents = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/events$/.exec(path)
    if (mEvents && req.method === 'POST') {
      const assignmentId = mEvents[1] as string
      const task = this.claimed.get(assignmentId)
      if (!task || Number(body['fence']) !== task.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'fence 不匹配' })
        return
      }
      const records = (body['records'] as Array<Record<string, unknown>>) ?? []
      const list = this.eventRecords.get(assignmentId) ?? []
      const knownIds = new Set(list.map((r) => r.event_id))
      for (const r of records) {
        if (!knownIds.has(String(r['event_id']))) {
          list.push({ local_seq: Number(r['local_seq']), event_id: String(r['event_id']), type: String(r['type']) })
          knownIds.add(String(r['event_id']))
        }
      }
      this.eventRecords.set(assignmentId, list)
      const prevAck = this.eventsAcked.get(assignmentId) ?? 0
      const ack = Math.max(prevAck, Number(records[records.length - 1]?.['local_seq'] ?? prevAck))
      this.eventsAcked.set(assignmentId, ack)
      // 迟到旧响应注入（一次性）：下一次 events ACK 返回注入控制
      const injectedEvents = this.nextEventsControl
      this.nextEventsControl = null
      const eventsControl = injectedEvents ?? {
        status: this.controlStatus.get(assignmentId) ?? 'active',
        control_epoch: task.control_epoch,
        server_control_seq: 0,
      }
      ok({ ack_seq: ack, control: eventsControl })
      return
    }
    const mDec = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/decisions$/.exec(path)
    if (mDec && req.method === 'POST') {
      const assignmentId = mDec[1] as string
      const task = this.claimed.get(assignmentId)
      if (!task || Number(body['fence']) !== task.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'fence 不匹配' })
        return
      }
      this.createDecisionCalls.push({
        assignmentId,
        batchId: String(body['batch_id']),
        fence: Number(body['fence']),
        control_epoch: Number(body['control_epoch']),
      })
      const decisionId = randomUUID()
      this.decisions.set(decisionId, {
        assignmentId,
        batchId: String(body['batch_id']),
        status: 'pending',
        polls: 0,
      })
      ok({ decision_id: decisionId, status: 'pending' })
      return
    }
    const mGet = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/decisions\/([^/]+)$/.exec(path)
    if (mGet && req.method === 'GET') {
      const d = this.decisions.get(mGet[2] as string)
      if (!d || d.assignmentId !== mGet[1]) {
        json(404, { success: false, code: 'NOT_FOUND', error: '决策不存在' })
        return
      }
      d.polls += 1
      if (d.polls >= this.readyAfterPolls) d.status = 'ready'
      ok({ decision_id: mGet[2], status: d.status, decision_kind: 'reply', batch_id: d.batchId, input_version: 1 })
      return
    }
    json(404, { success: false, error: 'unknown path' })
  }
}

// ---------------- fake 观察器（契约语义：水位过滤） ----------------

class FakeConversation {
  readonly messages: ObserverMessage[] = []
  observationCount = 0
  failNext = false
  constructor(readonly bindingId: string) {}

  push(text: string, sender: 'peer' | 'self' = 'peer'): ObserverMessage {
    const msg: ObserverMessage = {
      sender,
      text,
      local_message_id: `m-${randomUUID()}`,
      source_evidence_ref: `evd:${randomUUID().slice(0, 8)}`,
    }
    this.messages.push(msg)
    return msg
  }

  private complete(ordered: ObserverMessage[]): ObserverResult {
    return {
      observation_id: randomUUID(),
      account_identity_version: 1,
      conversation_binding_id: this.bindingId,
      binding_version: 0,
      observed_at: new Date().toISOString(),
      coverage: 'complete_window',
      ordered_messages: ordered,
      window_fingerprint: `fp_${randomUUID().slice(0, 12)}`,
      gap_reason: null,
    }
  }

  observer = async (_task: { taskId: string; conversationBindingId: string }, request: { watermark: ObserverWatermark | null }): Promise<ObserverResult> => {
    this.observationCount += 1
    if (this.failNext) {
      this.failNext = false
      throw new Error('观察器不可用（注入）')
    }
    const watermark = request.watermark
    if (!watermark) {
      // 基线（契约 §6）：返回整个当前可见窗口，引擎锚定历史、不回复旧消息
      return this.complete(this.messages.slice())
    }
    const anchorIndex = watermark.last_local_message_id
      ? this.messages.findIndex((m) => m.local_message_id === watermark.last_local_message_id)
      : -1
    if (anchorIndex < 0 && watermark.last_local_message_id) {
      // 水位锚点不在当前窗口（滚动断层语义）：契约应报 gap；本 fake 简化为空窗
      return this.complete([])
    }
    const newMessages = this.messages.slice(anchorIndex + 1)
    return this.complete(newMessages)
  }
}

// ---------------- 公共装配 ----------------

function fakeCrypto(): SessionCrypto {
  return {
    async protect(p: string) {
      return Buffer.from(p, 'utf-8').toString('base64')
    },
    async unprotect(c: string) {
      return Buffer.from(c, 'base64').toString('utf-8')
    },
  }
}

/** 可注入故障的 crypto（审计 P1：运行中 persistMeta 失败场景——protect 抛错
 * 使 writeMeta/appendEncrypted 失败，解除后自动恢复）；protectCalls 计数供
 * 幂等验证（persistMeta/appendEncrypted 均经 protect）；holdProtect 挂起下一
 * 次 protect（审计十轮：模拟串行链内持久化写入悬挂） */
function togglableCrypto(): SessionCrypto & {
  failProtect: boolean
  protectCalls: number
  holdProtect(): { release: () => void; engaged: boolean }
} {
  const base = fakeCrypto()
  let pendingRelease: Promise<void> | null = null
  const handle = { engaged: false, release: (): void => {} }
  const wrapper = {
    failProtect: false,
    protectCalls: 0,
    holdProtect() {
      handle.engaged = false
      pendingRelease = new Promise<void>((resolve) => {
        handle.release = resolve
      })
      return handle
    },
    async protect(p: string) {
      if (wrapper.failProtect) throw new Error('protect 注入失败（运行中）')
      if (pendingRelease !== null) {
        const release = pendingRelease
        pendingRelease = null
        handle.engaged = true
        await release
      }
      const out = await base.protect(p)
      wrapper.protectCalls += 1
      return out
    },
    async unprotect(c: string) {
      return base.unprotect(c)
    },
  }
  return wrapper
}

interface Stack {
  cloud: FakeSessionCloud
  api: ApiClient
  home: string
  cleanup: () => Promise<void>
}

async function newStack(): Promise<Stack> {
  const cloud = new FakeSessionCloud()
  const baseUrl = await cloud.start()
  const home = mkdtempSync(join(tmpdir(), 'st-engine-'))
  const api = new ApiClient(baseUrl, 'fake-device-token')
  return {
    cloud,
    api,
    home,
    cleanup: async () => {
      await cloud.stop()
      rmSync(home, { recursive: true, force: true })
    },
  }
}

function makeTask(taskId: string, bindingId: string): FakeTask {
  const assignmentId = randomUUID()
  return {
    task_id: taskId,
    assignment_id: assignmentId,
    spec: { goal: '测试目标', conversation_binding_id: bindingId },
    fence: 1,
    control_epoch: 1,
    spec_revision: 1,
  }
}

function newEngine(
  stack: Stack,
  observers: Map<string, FakeConversation>,
  events: string[],
  opts?: { claimIntervalMs?: number; crypto?: SessionCrypto },
): SessionTaskEngine {
  return new SessionTaskEngine({
    api: stack.api,
    runtimeHome: stack.home,
    crypto: opts?.crypto ?? fakeCrypto(),
    runtimeInstanceId: `rt-${randomUUID().slice(0, 8)}`,
    observer: async (task, request) => {
      const conv = observers.get(task.taskId)
      if (!conv) throw new Error('无会话脚本')
      return conv.observer(task, request)
    },
    emit: (msg) => events.push(msg),
    claimIntervalMs: opts?.claimIntervalMs ?? 40,
    renewIntervalMs: 500,
    batchSilenceMs: 80,
    batchMaxWaitMs: 400,
    syncRetryBaseMs: 50,
    syncRetryMaxMs: 200,
  })
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function waitFor(predicate: () => boolean, timeoutMs: number, what: string): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return
    await sleep(20)
  }
  assert.ok(false, `等待超时: ${what}`)
}

// ---------------- 用例 ----------------

test('A 等待→B 就绪→A 唤醒；发布后退出独立推进；无明文日志', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-A')
  const convB = new FakeConversation('bind-B')
  const observers = new Map([
    ['task-A', convA],
    ['task-B', convB],
  ])
  const events: string[] = []
  const engine = newEngine(stack, observers, events)
  const controller = new AbortController()
  t.after(() => {
    controller.abort()
    return stack.cleanup()
  })
  const engineDone = engine.run(controller.signal)

  // 「云端发布后退出」：A 入队，测试此后只通过会话脚本推进
  const taskA = makeTask('task-A', 'bind-A')
  stack.cloud.enqueue(taskA)
  convA.push('历史消息（基线锚点）') // 基线前的旧消息，不得触发回复

  await waitFor(() => engine.phaseOf('task-A') === 'ready' || convA.observationCount > 0, 2_000, 'A 被领取')
  await waitFor(() => convA.observationCount >= 1, 2_000, 'A 基线观察')
  // 基线后新消息 → 合批 → 决策
  convA.push('在吗')
  await waitFor(() => engine.phaseOf('task-A') === 'decision_pending' || engine.phaseOf('task-A') === 'send_ready', 3_000, 'A 决策提交')

  // A 等待期间 B 发布（发布方即测试，此后不再干预）；基线建立后才有新消息
  const taskB = makeTask('task-B', 'bind-B')
  stack.cloud.enqueue(taskB)
  await waitFor(() => engine.phaseOf('task-B') !== undefined, 3_000, 'B 被领取')
  await waitFor(() => convB.observationCount >= 1, 3_000, 'B 基线观察')
  convB.push('B 的首条消息')
  await waitFor(() => ['decision_pending', 'send_ready'].includes(engine.phaseOf('task-B') ?? ''), 4_000, 'B 在 A 等待期间推进')

  // A 唤醒：send_ready 等待中的 A 收到新消息 → 新批次（input_version 2）→ 再决策
  await waitFor(() => engine.phaseOf('task-A') === 'send_ready', 3_000, 'A 决策 ready')
  const aDecisionsBefore = stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA.assignment_id).length
  convA.push('还有一件事')
  // send_ready 停留 5s 后回观察（C2 无执行链；唤醒按观察节奏到达）
  await waitFor(
    () => stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA.assignment_id).length > aDecisionsBefore,
    9_000,
    'A 唤醒后再次提交决策',
  )

  // 事件前缀 ACK 到云端
  await waitFor(() => (stack.cloud.eventsAcked.get(taskA.assignment_id) ?? 0) >= 5, 4_000, 'A 事件 ACK')
  await waitFor(() => (stack.cloud.eventsAcked.get(taskB.assignment_id) ?? 0) >= 4, 4_000, 'B 事件 ACK')

  // 本地日志存在且可回放（隐含：engine 内部 adoptTask 重启路径在下一用例覆盖）
  assert.ok(existsSync(join(stack.home, 'session-tasks', taskA.assignment_id, 'events.jsonl')))

  // 普通日志不含消息正文（脱敏约束）
  const emitJoined = events.join('\n')
  for (const secret of ['在吗', '还有一件事', 'B 的首条消息', '历史消息']) {
    assert.ok(!emitJoined.includes(secret), `emit 日志泄漏正文: ${secret}`)
  }

  controller.abort()
  await engineDone
})

test('崩溃恢复：重启回放水位与已决策批次，同一消息不重复合批', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-R')
  const observers = new Map([['task-R', conv]])
  const events: string[] = []
  const task = makeTask('task-R', 'bind-R')
  stack.cloud.enqueue(task)

  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => {
    c1.abort()
  })
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-R') !== undefined, 3_000, '首轮领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '首轮基线观察')
  conv.push('第一条')
  await waitFor(() => stack.cloud.createDecisionCalls.some((x) => x.assignmentId === task.assignment_id), 5_000, '首轮决策提交')
  // 模拟崩溃：直接停止循环（本地日志已 fsync）
  c1.abort()
  await run1
  // 按唯一批次计数（同批次重放被云端五元唯一键幂等吸收；fake 记原始调用）
  const uniqueBatches = () =>
    new Set(stack.cloud.createDecisionCalls.filter((x) => x.assignmentId === task.assignment_id).map((x) => x.batchId)).size
  const decisionsBefore = uniqueBatches()
  assert.ok(decisionsBefore >= 1)

  // 重启：同任务重新可领取（fake 云端队列重新放入同一 assignment）
  stack.cloud.enqueue(task)
  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => {
    c2.abort()
  })
  const run2 = engine2.run(c2.signal)
  await waitFor(() => engine2.phaseOf('task-R') !== undefined, 3_000, '重启后重新领取')
  await sleep(600) // 给回放后观察/同步留时间

  // 同一消息（'第一条' 已在水位内）不得形成新批次：唯一批次数不增长
  assert.equal(uniqueBatches(), decisionsBefore, '恢复后未重复合批')

  // 新消息才触发新批次
  conv.push('重启后的新消息')
  await waitFor(() => uniqueBatches() > decisionsBefore, 9_000, '恢复后新消息形成新批次（2s 观察退避节奏）')
  c2.abort()
  await run2
})

test('观察不可用退避恢复（引擎不猜测、不死循环）', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-F')
  const observers = new Map([['task-F', conv]])
  const events: string[] = []
  const task = makeTask('task-F', 'bind-F')
  stack.cloud.enqueue(task)
  const engine = newEngine(stack, observers, events)
  const c = new AbortController()
  t.after(() => {
    c.abort()
  })
  const done = engine.run(c.signal)
  conv.failNext = true // 首次观察失败
  await waitFor(() => conv.observationCount >= 2, 8_000, '退避后重试观察（首败退避 5s）')
  assert.equal(engine.phaseOf('task-F') !== 'blocked', true, '观察失败不进入 blocked')
  c.abort()
  await done
})

test('同步前后崩溃边界：断网事件保留退避，恢复后补投（不丢不重）', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-O')
  const observers = new Map([['task-O', conv]])
  const events: string[] = []
  const task = makeTask('task-O', 'bind-O')

  // 在线领取并完成基线，随后断网：本地观察/落日志继续，云端零 ACK
  stack.cloud.enqueue(task)
  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => c1.abort())
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-O') !== undefined, 3_000, '在线领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  stack.cloud.offline = true
  conv.push('离线期间的消息')
  await waitFor(() => conv.observationCount >= 2, 5_000, '离线期间本地观察继续')
  await sleep(300) // 留出落日志与同步退避时间
  assert.equal(stack.cloud.eventsAcked.get(task.assignment_id) ?? 0, 0, '断网期间云端零 ACK')
  c1.abort()
  await run1 // 模拟崩溃：本地日志已 fsync，云端零 ACK

  // 云端恢复 + 引擎重启：本地事实按前缀补投，不丢不重
  stack.cloud.offline = false
  stack.cloud.enqueue(task)
  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => c2.abort())
  const run2 = engine2.run(c2.signal)
  await waitFor(() => (stack.cloud.eventsAcked.get(task.assignment_id) ?? 0) >= 2, 6_000, '恢复后事件补投 ACK')
  const records = stack.cloud.eventRecords.get(task.assignment_id) ?? []
  const ids = records.map((r) => r.event_id)
  assert.equal(new Set(ids).size, ids.length, '补投不重复')
  c2.abort()
  await run2
})

test('重启续租接续：旧 assignment 经 renew 接续，不重新建基线', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-RS')
  const observers = new Map([['task-A', convA]])
  const events: string[] = []
  const taskA = makeTask('task-A', 'bind-RS')
  stack.cloud.enqueue(taskA)

  // 第一代：领取 → 基线 → 新消息合批 → 决策在飞（本地日志/meta 已 fsync）
  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => c1.abort())
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-A') !== undefined, 3_000, '首轮领取')
  await waitFor(() => convA.observationCount >= 1, 3_000, '首轮基线观察')
  convA.push('重启前的消息')
  await waitFor(() => engine1.phaseOf('task-A') === 'decision_pending', 5_000, '首轮决策提交（在飞）')
  c1.abort()
  await run1 // 模拟重启：本地日志与 meta.json 已持久化

  // 审计 P1：meta.json 中 spec 业务正文必须加密（encrypted_spec），不得明文落盘
  const metaRaw = readFileSync(join(stack.home, 'session-tasks', taskA.assignment_id, 'meta.json'), 'utf-8')
  assert.ok(!metaRaw.includes('测试目标'), 'meta.json 不含 spec 业务正文明文')
  assert.ok(!metaRaw.includes('"spec"'), 'meta.json 不含明文 spec 字段')
  assert.ok(metaRaw.includes('"encrypted_spec"'), 'meta.json 以密文字段保存 spec')

  const uniqueBatches = () =>
    new Set(stack.cloud.createDecisionCalls.filter((x) => x.assignmentId === taskA.assignment_id).map((x) => x.batchId)).size
  const decisionsBefore = uniqueBatches()
  assert.ok(decisionsBefore >= 1, '重启前已有在飞决策')

  // 模拟 C1 行为：租约仍有效 → claim 不重新下发（不重新入队，claim 返回 204）
  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => c2.abort())
  const run2 = engine2.run(c2.signal)

  // 重启后不依赖 claim：通过 renew 接续旧 assignment，相位从日志回放恢复
  await waitFor(
    () => ['decision_pending', 'send_ready'].includes(engine2.phaseOf('task-A') ?? ''),
    4_000,
    '重启后经 renew 接续旧 assignment',
  )
  assert.ok(
    stack.cloud.renewCalls.some((r) => r.assignmentId === taskA.assignment_id && r.fence === taskA.fence),
    '接续路径使用了 renew（非 claim）',
  )

  // 未 ACK 事件经接续后的 syncPending 补交
  await waitFor(() => (stack.cloud.eventsAcked.get(taskA.assignment_id) ?? 0) >= 3, 5_000, '事件补交 ACK')

  // 在飞决策恢复轮询并推进为 ready
  await waitFor(() => engine2.phaseOf('task-A') === 'send_ready', 6_000, '恢复的在飞决策推进为 ready')

  // 没有重新建基线：assignment 的 baseline 事件唯一（水位延续，不回到 0）
  const baselineCount = () =>
    (stack.cloud.eventRecords.get(taskA.assignment_id) ?? []).filter((r) => r.type === 'baseline').length
  assert.equal(baselineCount(), 1, '未重建基线（水位延续）')

  // 水位延续验证：旧消息（'重启前的消息'）在水位内不再形成新批次；只有新消息触发新决策
  convA.push('重启后的新消息')
  await waitFor(() => uniqueBatches() > decisionsBefore, 9_000, '接续后新消息形成新批次')
  assert.equal(baselineCount(), 1, '全程未重建基线')

  c2.abort()
  await run2
})

test('租约过期换代保守阻断：决策在飞时新 assignment 持久化 blocked（不重建不重复）', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-EX')
  const observers = new Map([['task-A', convA]])
  const events: string[] = []
  const taskA1 = makeTask('task-A', 'bind-EX')
  stack.cloud.enqueue(taskA1)

  // 第一代：领取 → 基线 → 新消息合批 → 决策在飞（本地日志/meta 已 fsync）
  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => c1.abort())
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-A') !== undefined, 3_000, '首轮领取')
  convA.push('历史消息（基线锚点）')
  await waitFor(() => convA.observationCount >= 1, 3_000, '首轮基线观察')
  convA.push('换代前的旧消息')
  await waitFor(() => engine1.phaseOf('task-A') === 'decision_pending', 5_000, '首轮决策提交（在飞）')
  const decisionsBefore = stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA1.assignment_id).length
  assert.ok(decisionsBefore >= 1, '换代前已有在飞决策（云端可查，不丢失跟踪）')
  c1.abort()
  await run1

  // 模拟 C1 租约过期换代：旧 assignment 的 renew/events 一律 STALE；
  // 重新入队的是同 task_id 的新 assignment（不重新入队旧 assignment）
  stack.cloud.expireAssignmentLease(taskA1.assignment_id)
  const taskA2 = makeTask('task-A', 'bind-EX')
  stack.cloud.enqueue(taskA2)

  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => c2.abort())
  const run2 = engine2.run(c2.signal)
  await waitFor(() => engine2.phaseOf('task-A') === 'blocked', 5_000, '换代后新 assignment 保守阻断')

  // 旧 assignment 续租被拒（租约过期）；阻断经 recovery_blocked 加密事件持久化
  assert.ok(stack.cloud.renewCalls.some((r) => r.assignmentId === taskA1.assignment_id), '对旧 assignment 尝试过 renew')
  assert.ok(
    events.some((e) => e.includes('换代恢复保守阻断') && e.includes('generation_change_requires_manual_review')),
    'emit 记录了保守阻断（原因=generation_change_requires_manual_review）',
  )
  const logA2 = join(stack.home, 'session-tasks', taskA2.assignment_id, 'events.jsonl')
  assert.ok(existsSync(logA2), '新 assignment 日志已落盘')
  assert.ok(readFileSync(logA2, 'utf-8').includes('"type":"recovery_blocked"'), '阻断状态持久化（recovery_blocked 事件）')

  // blocked 持续：不观察（无新 baseline）、不重复创建决策（旧在飞决策仍在云端）
  const obsAtBlock = convA.observationCount
  await sleep(900) // 跨过 renewIntervalMs(500)：续租 active 也不得解除 blocked
  assert.equal(engine2.phaseOf('task-A'), 'blocked', 'blocked 持续（不被续租/调度解除）')
  assert.equal(convA.observationCount, obsAtBlock, 'blocked 期间不观察')
  assert.equal(
    (stack.cloud.eventRecords.get(taskA2.assignment_id) ?? []).filter((r) => r.type === 'baseline').length,
    0,
    '新 assignment 云端无 baseline 事件（未重建基线）',
  )
  assert.equal(
    stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA2.assignment_id).length,
    0,
    '旧在飞决策不重复创建（新 assignment 零决策调用）',
  )

  // 事件补交不受阻断影响：recovery_blocked 事件本身经 syncPending 投递云端
  await waitFor(() => (stack.cloud.eventsAcked.get(taskA2.assignment_id) ?? 0) >= 1, 5_000, 'recovery_blocked 事件补交 ACK')

  c2.abort()
  await run2
})

test('批次冻结未提交时换代：新 assignment blocked，未完成批次本地保留不丢弃', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-PB')
  const observers = new Map([['task-A', convA]])
  const events: string[] = []
  const taskA1 = makeTask('task-A', 'bind-PB')
  stack.cloud.enqueue(taskA1)

  // 第一代：领取 → 基线（在线 ACK）→ 断网 → 新消息冻结批次。批次事件已本地
  // fsync 但未获云端 ACK → 决策提交被 ACK 门禁挡住（未提交）
  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => c1.abort())
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-A') !== undefined, 3_000, '首轮领取')
  convA.push('基线锚点')
  await waitFor(() => convA.observationCount >= 1, 3_000, '首轮基线观察')
  stack.cloud.offline = true
  convA.push('断网期间冻结的消息')
  const logA1 = join(stack.home, 'session-tasks', taskA1.assignment_id, 'events.jsonl')
  await waitFor(() => existsSync(logA1) && readFileSync(logA1, 'utf-8').includes('"type":"batch"'), 5_000, '断网期间批次冻结落盘')
  await sleep(300) // 留出决策提交窗口：批次事件未 ACK → 不得提交
  assert.equal(
    stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA1.assignment_id).length,
    0,
    '批次未 ACK 前不提交决策',
  )
  c1.abort()
  await run1

  // 换代：旧租约过期，新 assignment 领取 → 待提交批次是未完成工作 → 保守阻断
  stack.cloud.offline = false
  stack.cloud.expireAssignmentLease(taskA1.assignment_id)
  const taskA2 = makeTask('task-A', 'bind-PB')
  stack.cloud.enqueue(taskA2)
  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => c2.abort())
  const run2 = engine2.run(c2.signal)
  await waitFor(() => engine2.phaseOf('task-A') === 'blocked', 5_000, '换代后新 assignment 保守阻断')
  assert.ok(events.some((e) => e.includes('换代恢复保守阻断')), 'emit 记录了保守阻断')

  // 未完成批次不丢弃：云端 STALE 拒收补交，本地事实保留在旧 assignment 日志
  assert.ok(readFileSync(logA1, 'utf-8').includes('"type":"batch"'), '未完成批次仍保留在旧 assignment 本地日志')
  assert.equal(
    stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA2.assignment_id).length,
    0,
    '新 assignment 不提交决策（未完成批次语义已阻断）',
  )
  const logA2 = join(stack.home, 'session-tasks', taskA2.assignment_id, 'events.jsonl')
  assert.ok(readFileSync(logA2, 'utf-8').includes('"type":"recovery_blocked"'), '阻断已持久化')

  c2.abort()
  await run2
})

test('换代阻断后再次重启：回放 recovery_blocked 事件仍为 blocked', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-RB')
  const observers = new Map([['task-A', convA]])
  const events: string[] = []
  const taskA1 = makeTask('task-A', 'bind-RB')
  stack.cloud.enqueue(taskA1)

  // 第一代：建基线 + 在飞决策 → 停止 → 过期换代 → 新 assignment 持久化 blocked
  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => c1.abort())
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-A') !== undefined, 3_000, '首轮领取')
  convA.push('基线锚点')
  await waitFor(() => convA.observationCount >= 1, 3_000, '首轮基线观察')
  convA.push('换代前的消息')
  await waitFor(() => engine1.phaseOf('task-A') === 'decision_pending', 5_000, '首轮决策提交')
  c1.abort()
  await run1
  stack.cloud.expireAssignmentLease(taskA1.assignment_id)
  const taskA2 = makeTask('task-A', 'bind-RB')
  stack.cloud.enqueue(taskA2)

  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => c2.abort())
  const run2 = engine2.run(c2.signal)
  await waitFor(() => engine2.phaseOf('task-A') === 'blocked', 5_000, '换代后保守阻断')
  const logA2 = join(stack.home, 'session-tasks', taskA2.assignment_id, 'events.jsonl')
  assert.ok(readFileSync(logA2, 'utf-8').includes('"type":"recovery_blocked"'), '阻断事件已持久化')
  c2.abort()
  await run2

  // 第三次启动（新 assignment 租约仍有效 → renew 接续回放）：仍 blocked
  const engine3 = newEngine(stack, observers, events)
  const c3 = new AbortController()
  t.after(() => c3.abort())
  const run3 = engine3.run(c3.signal)
  await waitFor(() => engine3.phaseOf('task-A') === 'blocked', 5_000, '重启后仍为 blocked')
  const obsAtRestart = convA.observationCount
  await sleep(900) // 留出续租/调度/事件 ACK 窗口：阻断不被解除
  assert.equal(engine3.phaseOf('task-A'), 'blocked', 'blocked 在重启回放后持续')
  assert.equal(convA.observationCount, obsAtRestart, '重启后仍不观察')
  assert.equal(
    stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA2.assignment_id).length,
    0,
    '重启后仍不创建新决策',
  )
  c3.abort()
  await run3
})

test('meta.json 写入失败：任务不激活（不观察、不提交决策）', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-MW')
  const observers = new Map([['task-A', convA]])
  const events: string[] = []
  const taskA = makeTask('task-A', 'bind-MW')
  stack.cloud.enqueue(taskA)

  // 注入 crypto.protect 故障：adoptTask 的 writeMeta 加密 spec 时抛错 → 不激活
  const failingCrypto: SessionCrypto = {
    protect: async () => {
      throw new Error('protect 注入失败')
    },
    unprotect: async (c) => c,
  }
  const engine = newEngine(stack, observers, events, { crypto: failingCrypto })
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await sleep(600) // 留出 claim + adoptTask 窗口

  assert.equal(engine.runningTaskCount, 0, '任务未激活（不进入 tasks Map）')
  assert.equal(engine.phaseOf('task-A'), undefined, '无相位')
  assert.equal(convA.observationCount, 0, '未观察')
  assert.equal(stack.cloud.createDecisionCalls.length, 0, '未提交决策')
  assert.ok(events.some((e) => e.includes('任务不激活')), 'emit 记录了不激活原因')
  assert.ok(
    !existsSync(join(stack.home, 'session-tasks', taskA.assignment_id, 'events.jsonl')),
    '未写入任何事件日志',
  )

  c.abort()
  await done
})

test('旧 assignment 元数据解密失败：新领取 blocked（原因=old_log_corrupt）', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-CM')
  const observers = new Map([['task-A', convA]])
  const events: string[] = []
  const taskA1 = makeTask('task-A', 'bind-CM')
  stack.cloud.enqueue(taskA1)

  // 第一代：建基线 + 在飞决策 → 停止
  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => c1.abort())
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-A') !== undefined, 3_000, '首轮领取')
  convA.push('基线锚点')
  await waitFor(() => convA.observationCount >= 1, 3_000, '首轮基线观察')
  convA.push('换代前的消息')
  await waitFor(() => engine1.phaseOf('task-A') === 'decision_pending', 5_000, '首轮决策提交')
  c1.abort()
  await run1

  // 篡改 meta.json 密文字段：readMeta 解密失败返回 null（不降级明文），但明文
  // task_id 仍可提取 → 恢复扫描标记不可恢复
  const metaPath = join(stack.home, 'session-tasks', taskA1.assignment_id, 'meta.json')
  const metaDoc = JSON.parse(readFileSync(metaPath, 'utf-8')) as Record<string, unknown>
  metaDoc['encrypted_spec'] = '!!!not-valid-cipher!!!'
  writeFileSync(metaPath, JSON.stringify(metaDoc), 'utf-8')

  // 换代：新 assignment 领取 → 旧工作不可恢复 → blocked
  stack.cloud.expireAssignmentLease(taskA1.assignment_id)
  const taskA2 = makeTask('task-A', 'bind-CM')
  stack.cloud.enqueue(taskA2)
  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => c2.abort())
  const run2 = engine2.run(c2.signal)
  await waitFor(() => engine2.phaseOf('task-A') === 'blocked', 5_000, '不可恢复 → blocked')
  assert.ok(
    events.some((e) => e.includes('换代恢复保守阻断') && e.includes('old_log_corrupt')),
    'emit 记录阻断原因=old_log_corrupt',
  )
  const logA2 = join(stack.home, 'session-tasks', taskA2.assignment_id, 'events.jsonl')
  assert.ok(readFileSync(logA2, 'utf-8').includes('"type":"recovery_blocked"'), '阻断已持久化')
  assert.equal(
    stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA2.assignment_id).length,
    0,
    '新 assignment 不提交决策',
  )

  c2.abort()
  await run2
})

test('恢复阻断后事件 ACK 与续租均不解除 blocked', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-AB')
  const observers = new Map([['task-A', convA]])
  const events: string[] = []
  const taskA1 = makeTask('task-A', 'bind-AB')
  stack.cloud.enqueue(taskA1)

  // 第一代：建基线 + 在飞决策 → 过期换代 → 新 assignment blocked
  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => c1.abort())
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-A') !== undefined, 3_000, '首轮领取')
  convA.push('基线锚点')
  await waitFor(() => convA.observationCount >= 1, 3_000, '首轮基线观察')
  convA.push('换代前的消息')
  await waitFor(() => engine1.phaseOf('task-A') === 'decision_pending', 5_000, '首轮决策提交')
  c1.abort()
  await run1
  stack.cloud.expireAssignmentLease(taskA1.assignment_id)
  const taskA2 = makeTask('task-A', 'bind-AB')
  stack.cloud.enqueue(taskA2)

  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => c2.abort())
  const run2 = engine2.run(c2.signal)
  await waitFor(() => engine2.phaseOf('task-A') === 'blocked', 5_000, '换代后保守阻断')

  // blocked 任务的事件补交照常进行：recovery_blocked 事件获云端 ACK（active 控制）
  await waitFor(() => (stack.cloud.eventsAcked.get(taskA2.assignment_id) ?? 0) >= 1, 5_000, '阻断事件获 ACK')
  const obsAtAck = convA.observationCount
  await sleep(900) // 跨过 renewIntervalMs(500)：ACK + 续租 active 轮次后仍不得解除
  assert.equal(engine2.phaseOf('task-A'), 'blocked', 'ACK 与续租后仍 blocked')
  assert.equal(convA.observationCount, obsAtAck, 'ACK 后仍不观察')
  assert.equal(
    stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA2.assignment_id).length,
    0,
    'ACK 后仍不提交决策',
  )

  c2.abort()
  await run2
})

test('meta 缺失/损坏不可归属：阻断不建新基线（重启回放仍 blocked）', async (t) => {
  // 参数化四种 meta 不可用形态：文件缺失 / 空文件 / 截断 JSON / 缺 task_id——
  // 均无法归属任务 → 恢复扫描标记不可归属，新 assignment 保守阻断
  const modes = ['missing', 'empty', 'truncated', 'no_task_id'] as const
  for (const mode of modes) {
    await t.test(`meta=${mode}`, async () => {
      const stack = await newStack()
      const convA = new FakeConversation('bind-UA')
      const observers = new Map([['task-A', convA]])
      const events: string[] = []
      const taskA1 = makeTask('task-A', 'bind-UA')
      stack.cloud.enqueue(taskA1)

      // 第一代：建基线 + 在飞决策（本地日志/meta 已 fsync）
      const engine1 = newEngine(stack, observers, events)
      const c1 = new AbortController()
      const run1 = engine1.run(c1.signal)
      await waitFor(() => engine1.phaseOf('task-A') !== undefined, 3_000, '首轮领取')
      convA.push('基线锚点')
      await waitFor(() => convA.observationCount >= 1, 3_000, '首轮基线观察')
      convA.push('换代前的消息')
      await waitFor(() => engine1.phaseOf('task-A') === 'decision_pending', 5_000, '首轮决策提交')
      c1.abort()
      await run1

      // 破坏 meta.json（旧日志完好）
      const metaPath = join(stack.home, 'session-tasks', taskA1.assignment_id, 'meta.json')
      switch (mode) {
        case 'missing':
          rmSync(metaPath)
          break
        case 'empty':
          writeFileSync(metaPath, '', 'utf-8')
          break
        case 'truncated': {
          const raw = readFileSync(metaPath, 'utf-8')
          writeFileSync(metaPath, raw.slice(0, Math.max(1, Math.floor(raw.length / 2))), 'utf-8')
          break
        }
        case 'no_task_id': {
          const doc = JSON.parse(readFileSync(metaPath, 'utf-8')) as Record<string, unknown>
          delete doc['task_id']
          writeFileSync(metaPath, JSON.stringify(doc), 'utf-8')
          break
        }
      }

      // 换代：旧 assignment 过期，新 assignment 领取 → 不可归属 → 保守阻断
      stack.cloud.expireAssignmentLease(taskA1.assignment_id)
      const taskA2 = makeTask('task-A', 'bind-UA')
      stack.cloud.enqueue(taskA2)
      const engine2 = newEngine(stack, observers, events)
      const c2 = new AbortController()
      const run2 = engine2.run(c2.signal)
      await waitFor(() => engine2.phaseOf('task-A') === 'blocked', 5_000, '不可归属 → 保守阻断')
      assert.ok(
        events.some((e) => e.includes('unattributable_old_assignment_exists')),
        'emit 记录不可归属阻断原因',
      )
      const logA2 = join(stack.home, 'session-tasks', taskA2.assignment_id, 'events.jsonl')
      assert.ok(readFileSync(logA2, 'utf-8').includes('"type":"recovery_blocked"'), '阻断已持久化')

      // 不观察、不决策、不建基线
      const obsAtBlock = convA.observationCount
      await sleep(900) // 跨过 renewIntervalMs(500)：续租 active 也不得解除阻断
      assert.equal(engine2.phaseOf('task-A'), 'blocked', '阻断持续')
      assert.equal(convA.observationCount, obsAtBlock, '阻断期间不观察')
      assert.equal(
        (stack.cloud.eventRecords.get(taskA2.assignment_id) ?? []).filter((r) => r.type === 'baseline').length,
        0,
        '云端无 baseline 事件（未重建基线）',
      )
      assert.equal(
        stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA2.assignment_id).length,
        0,
        '不创建决策',
      )
      // 阻断事件照常补交（syncPending 不受阻断影响）
      await waitFor(() => (stack.cloud.eventsAcked.get(taskA2.assignment_id) ?? 0) >= 1, 5_000, '阻断事件补交 ACK')

      c2.abort()
      await run2

      // 重启：recovery_blocked 事件回放仍 blocked（不可归属目录未清除）
      const engine3 = newEngine(stack, observers, events)
      const c3 = new AbortController()
      const run3 = engine3.run(c3.signal)
      await waitFor(() => engine3.phaseOf('task-A') === 'blocked', 5_000, '重启后仍为 blocked')
      const obsAtRestart = convA.observationCount
      await sleep(900)
      assert.equal(engine3.phaseOf('task-A'), 'blocked', 'blocked 在重启回放后持续')
      assert.equal(convA.observationCount, obsAtRestart, '重启后仍不观察')
      assert.equal(
        stack.cloud.createDecisionCalls.filter((c) => c.assignmentId === taskA2.assignment_id).length,
        0,
        '重启后仍不创建新决策',
      )
      c3.abort()
      await run3

      await stack.cleanup()
    })
  }
})

test('renew 控制更新时 meta 写入失败：门禁关闭停止动作，解除后恢复', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const convA = new FakeConversation('bind-CT')
  const observers = new Map([['task-A', convA]])
  const events: string[] = []
  const taskA = makeTask('task-A', 'bind-CT')
  stack.cloud.enqueue(taskA)
  const crypto = togglableCrypto()
  const engine = newEngine(stack, observers, events, { crypto })
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-A') !== undefined, 3_000, '领取')
  await waitFor(() => convA.observationCount >= 1, 3_000, '基线观察')

  // 注入 writeMeta 失败（protect 抛错）+ 服务端控制换代（paused）：
  // 控制更新路径须先关门禁再持久化——失败不回开
  crypto.failProtect = true
  stack.cloud.setControl(taskA.assignment_id, { control_epoch: 2, status: 'paused' })
  await waitFor(() => events.some((e) => e.includes('门禁保持关闭')), 5_000, 'meta 更新失败被记录（门禁关闭）')

  // 门禁关闭期间：不观察、不决策（跨过 2 轮 renewIntervalMs）
  const obsAt = convA.observationCount
  const decAt = stack.cloud.createDecisionCalls.filter((x) => x.assignmentId === taskA.assignment_id).length
  await sleep(1_200)
  assert.equal(convA.observationCount, obsAt, '门禁关闭期间不观察')
  assert.equal(
    stack.cloud.createDecisionCalls.filter((x) => x.assignmentId === taskA.assignment_id).length,
    decAt,
    '门禁关闭期间不提交决策',
  )

  // 解除注入 + 控制恢复 active（再换代）：下轮 renew 持久化成功 → 门禁回开
  crypto.failProtect = false
  stack.cloud.setControl(taskA.assignment_id, { control_epoch: 3, status: 'active' })
  await waitFor(() => convA.observationCount > obsAt, 8_000, '门禁恢复后继续观察')
  const metaRaw = readFileSync(join(stack.home, 'session-tasks', taskA.assignment_id, 'meta.json'), 'utf-8')
  assert.ok(metaRaw.includes('"control_epoch":3'), 'meta.json 已持久化新控制代')

  c.abort()
  await done
})

// ---------------- 控制更新乱序/并发一致性（审计九轮 P1） ----------------

test('乱序控制：新 paused（事件 ACK 先到）后旧 active renew 迟到 → 拒绝不回退', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-OO1')
  const observers = new Map([['task-OO1', conv]])
  const events: string[] = []
  const task = makeTask('task-OO1', 'bind-OO1')
  stack.cloud.enqueue(task)
  const engine = newEngine(stack, observers, events)
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-OO1') !== undefined, 3_000, '领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  await waitFor(() => (stack.cloud.eventsAcked.get(task.assignment_id) ?? 0) >= 1, 4_000, '基线事件 ACK（epoch1 active）')

  // 冻结一条 renew（响应按请求时刻 epoch1 active 快照，暂不送达），随后服务端暂停（epoch2）
  stack.cloud.holdRenew(task.assignment_id)
  await waitFor(() => stack.cloud.heldRenewCount(task.assignment_id) >= 1, 3_000, 'renew 已挂起（响应冻结为 epoch1 active）')
  stack.cloud.setControl(task.assignment_id, { control_epoch: 2, status: 'paused' })

  // 事件 ACK 路径先送达 paused（epoch2）：新消息 → 批次事件 → ACK 控制应用
  conv.push('暂停前的入站消息')
  await waitFor(() => engine.controlStateOf('task-OO1')?.gate === 'paused_control', 5_000, 'ACK 路径应用 paused（epoch2）')
  assert.equal(engine.controlStateOf('task-OO1')?.controlEpoch, 2)

  const obsAt = conv.observationCount
  const decAt = stack.cloud.createDecisionCalls.filter((x) => x.assignmentId === task.assignment_id).length

  // 旧 renew 响应（epoch1 active）此刻迟到到达 → 应被拒绝
  stack.cloud.releaseRenews(task.assignment_id)
  await waitFor(() => events.some((e) => e.includes('丢弃旧控制响应')), 3_000, '旧 active renew 被拒绝')
  await sleep(700) // 跨一轮 renew（服务端现为 epoch2 paused，同版本幂等）

  const st = engine.controlStateOf('task-OO1')
  assert.equal(st?.controlEpoch, 2, 'controlEpoch 未回退到 1')
  assert.equal(st?.gate, 'paused_control', '门禁未被旧 active 响应打开')
  assert.equal(conv.observationCount, obsAt, '门禁关闭期间不观察')
  assert.equal(
    stack.cloud.createDecisionCalls.filter((x) => x.assignmentId === task.assignment_id).length,
    decAt,
    '门禁关闭期间不提交决策',
  )

  c.abort()
  await done
})

test('乱序控制：新 active renew 先到后旧 paused ACK 迟到 → 拒绝不误关门禁', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-OO2')
  const observers = new Map([['task-OO2', conv]])
  const events: string[] = []
  const task = makeTask('task-OO2', 'bind-OO2')
  stack.cloud.enqueue(task)
  const engine = newEngine(stack, observers, events)
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-OO2') !== undefined, 3_000, '领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  await waitFor(() => (stack.cloud.eventsAcked.get(task.assignment_id) ?? 0) >= 1, 4_000, '基线事件 ACK')

  // 同代暂停（epoch 不变）→ renew 送达 → 门禁关闭
  stack.cloud.setControl(task.assignment_id, { control_epoch: 1, status: 'paused' })
  await waitFor(() => engine.controlStateOf('task-OO2')?.gate === 'paused_control', 4_000, '同代暂停应用')
  assert.equal(engine.controlStateOf('task-OO2')?.controlEpoch, 1)

  // 恢复（换代 active）→ renew 先送达 → 门禁开放
  stack.cloud.setControl(task.assignment_id, { control_epoch: 2, status: 'active' })
  await waitFor(() => engine.controlStateOf('task-OO2')?.gate === 'open', 4_000, '恢复 active（epoch2）')
  assert.equal(engine.controlStateOf('task-OO2')?.controlEpoch, 2)

  // 旧 paused ACK（暂停时代冻结的 epoch1 响应）迟到注入并经事件 ACK 送达 → 拒绝
  stack.cloud.nextEventsControl = { status: 'paused', control_epoch: 1 }
  conv.push('恢复后的入站消息')
  await waitFor(() => events.some((e) => e.includes('丢弃旧控制响应')), 5_000, '旧 paused ACK 被拒绝')

  const st = engine.controlStateOf('task-OO2')
  assert.equal(st?.controlEpoch, 2, 'controlEpoch 保持 2')
  assert.equal(st?.gate, 'open', '门禁未被旧 paused 响应关闭')

  // 门禁保持开放：新批次正常提交决策
  await waitFor(
    () => stack.cloud.createDecisionCalls.some((x) => x.assignmentId === task.assignment_id),
    5_000,
    '门禁开放，新批次正常决策',
  )

  c.abort()
  await done
})

test('持久化失败窗口内旧 active 被拒；解除后仅应用最新 paused（不回退）', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-OO3')
  const observers = new Map([['task-OO3', conv]])
  const events: string[] = []
  const task = makeTask('task-OO3', 'bind-OO3')
  stack.cloud.enqueue(task)
  const crypto = togglableCrypto()
  const engine = newEngine(stack, observers, events, { crypto })
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-OO3') !== undefined, 3_000, '领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  await waitFor(() => (stack.cloud.eventsAcked.get(task.assignment_id) ?? 0) >= 1, 4_000, '基线事件 ACK')

  // 注入持久化失败 + 控制换代 paused：应用前先关门禁，持久化失败保持关闭
  crypto.failProtect = true
  stack.cloud.setControl(task.assignment_id, { control_epoch: 2, status: 'paused' })
  await waitFor(() => events.some((e) => e.includes('控制更新持久化失败')), 5_000, '持久化失败被记录（门禁关闭）')
  let st = engine.controlStateOf('task-OO3')
  assert.equal(st?.controlEpoch, 2, '已应用 epoch=2')
  assert.equal(st?.gate, 'paused_control', '门禁关闭')
  assert.equal(st?.metaPersistPending, true, 'metaPersistPending 标记待重试')

  // 失败窗口内注入旧 active（epoch1）renew 响应 → 拒绝（不回退、不开门禁）
  stack.cloud.nextRenewControl = { status: 'active', control_epoch: 1 }
  await waitFor(() => events.some((e) => e.includes('丢弃旧控制响应')), 5_000, '失败窗口内旧 active 被拒绝')

  // 解除注入：下轮 renew 重试持久化成功 → 只应用最新（epoch2 paused），门禁仍关闭
  crypto.failProtect = false
  await waitFor(() => engine.controlStateOf('task-OO3')?.metaPersistPending === false, 5_000, '重试持久化成功')
  await sleep(700) // 再跨一轮 renew：同版本 paused 幂等，状态不变
  st = engine.controlStateOf('task-OO3')
  assert.equal(st?.controlEpoch, 2, '最终 controlEpoch=2')
  assert.equal(st?.gate, 'paused_control', '门禁保持关闭（最新控制是 paused）')
  const metaRaw = readFileSync(join(stack.home, 'session-tasks', task.assignment_id, 'meta.json'), 'utf-8')
  assert.ok(metaRaw.includes('"control_epoch":2'), 'meta.json 含 epoch=2')

  c.abort()
  await done
})

test('同版本重复控制响应幂等：不重复持久化、状态不变', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-OO4')
  const observers = new Map([['task-OO4', conv]])
  const events: string[] = []
  const task = makeTask('task-OO4', 'bind-OO4')
  stack.cloud.enqueue(task)
  const crypto = togglableCrypto()
  const engine = newEngine(stack, observers, events, { crypto })
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-OO4') !== undefined, 3_000, '领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  await waitFor(() => (stack.cloud.eventsAcked.get(task.assignment_id) ?? 0) >= 1, 4_000, '基线事件 ACK')

  stack.cloud.setControl(task.assignment_id, { control_epoch: 2, status: 'paused' })
  await waitFor(() => engine.controlStateOf('task-OO4')?.gate === 'paused_control', 5_000, 'paused（epoch2）应用')
  await sleep(650) // 应用该控制的 renew 完成持久化，并跨入下一轮 renew
  const count = crypto.protectCalls

  await sleep(1_100) // ≥2 轮 renew：同版本 paused 重复到达
  assert.equal(crypto.protectCalls, count, '同版本重复响应不重复持久化（无新 protect 调用）')
  const st = engine.controlStateOf('task-OO4')
  assert.equal(st?.controlEpoch, 2, '状态不变')
  assert.equal(st?.gate, 'paused_control', '门禁不变')

  c.abort()
  await done
})

test('换代后旧 assignment 的迟到 renew 不影响新任务', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-OO5')
  const observers = new Map([['task-OO5', conv]])
  const events: string[] = []
  const taskA1 = makeTask('task-OO5', 'bind-OO5')
  stack.cloud.enqueue(taskA1)
  const engine = newEngine(stack, observers, events)
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-OO5') !== undefined, 3_000, '旧 assignment 领取')

  // 旧 assignment A1 的 renew 请求发出（响应冻结为当时 active），随后 A1 被换代
  stack.cloud.holdRenew(taskA1.assignment_id)
  await waitFor(() => stack.cloud.heldRenewCount(taskA1.assignment_id) >= 1, 3_000, 'A1 renew 已挂起（响应冻结）')
  stack.cloud.expireAssignmentLease(taskA1.assignment_id)
  const taskA2 = makeTask('task-OO5', 'bind-OO5')
  stack.cloud.enqueue(taskA2)
  await waitFor(() => engine.controlStateOf('task-OO5')?.assignmentId === taskA2.assignment_id, 4_000, '新 assignment B 领取')

  // A1 的 renew 响应迟到到达 → 丢弃（assignmentId 归属检查）
  stack.cloud.releaseRenews(taskA1.assignment_id)
  await waitFor(() => events.some((e) => e.includes('丢弃旧 assignment 控制响应')), 3_000, '迟到 renew 被丢弃')

  const st = engine.controlStateOf('task-OO5')
  assert.equal(st?.assignmentId, taskA2.assignment_id, '当前运行为新 assignment')
  assert.equal(st?.controlEpoch, taskA2.control_epoch, '新任务 controlEpoch 不受旧回调影响')
  assert.equal(st?.gate, 'open', '新任务门禁不受旧回调影响')

  // 新任务正常推进：建基线 → 新消息 → 决策
  const obsAt = conv.observationCount
  await waitFor(() => conv.observationCount > obsAt, 4_000, '新任务建基线（观察恢复）')
  conv.push('换代后的新消息')
  await waitFor(
    () => stack.cloud.createDecisionCalls.some((x) => x.assignmentId === taskA2.assignment_id),
    6_000,
    '新任务正常提交决策',
  )

  c.abort()
  await done
})

// ---------------- 元数据重试纳入串行链（审计十轮） ----------------

test('旧重试悬挂→新控制经链写入→旧重试链内完成：磁盘不回退旧控制代', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-MR1')
  const observers = new Map([['task-MR1', conv]])
  const events: string[] = []
  const task = makeTask('task-MR1', 'bind-MR1')
  stack.cloud.enqueue(task)
  const crypto = togglableCrypto()
  const engine = newEngine(stack, observers, events, { crypto })
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-MR1') !== undefined, 3_000, '领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  await waitFor(() => (stack.cloud.eventsAcked.get(task.assignment_id) ?? 0) >= 1, 4_000, '基线事件 ACK')

  // epoch=2 paused 持久化失败 → pending=true、门禁关闭、内存 epoch=2
  crypto.failProtect = true
  stack.cloud.setControl(task.assignment_id, { control_epoch: 2, status: 'paused' })
  await waitFor(() => engine.controlStateOf('task-MR1')?.metaPersistPending === true, 5_000, '持久化失败标记 pending')
  assert.equal(engine.controlStateOf('task-MR1')?.controlEpoch, 2)
  assert.equal(engine.controlStateOf('task-MR1')?.gate, 'paused_control')

  // 挂起一条 renew（响应冻结为 epoch2 paused）；解除注入并挂起下一次持久化
  // （模拟旧重试写入悬挂——修复后它在串行链内执行，不再有链外写入）
  stack.cloud.holdRenew(task.assignment_id)
  await waitFor(() => stack.cloud.heldRenewCount(task.assignment_id) >= 1, 3_000, 'renew 已挂起（响应冻结为 epoch2 paused）')
  crypto.failProtect = false
  const hang = crypto.holdProtect()
  // 服务端推进到 epoch=3 active（新控制将经串行链到达）
  stack.cloud.setControl(task.assignment_id, { control_epoch: 3, status: 'active' })
  stack.cloud.releaseRenews(task.assignment_id)
  await waitFor(() => hang.engaged, 5_000, '旧重试持久化已挂起（串行链内）')

  // 悬挂期间新控制的 renew 响应已到达并在链上排队：不得越过未完成的旧重试
  await sleep(700)
  assert.equal(engine.controlStateOf('task-MR1')?.controlEpoch, 2, '悬挂期间新控制未被应用（串行链排队）')

  // 释放悬挂：旧重试（epoch=2）先在链内完成，排队的 epoch=3 随后应用并持久化。
  // 修复前：链外旧重试与链内新控制并发写盘，磁盘最终可能是 epoch=2（内存=3）
  hang.release()
  await waitFor(() => engine.controlStateOf('task-MR1')?.gate === 'open', 6_000, 'epoch=3 active 应用且门禁开放')
  const st = engine.controlStateOf('task-MR1')
  assert.equal(st?.controlEpoch, 3, '内存 controlEpoch=3')
  assert.equal(st?.metaPersistPending, false, 'pending 已清除')
  const metaRaw = readFileSync(join(stack.home, 'session-tasks', task.assignment_id, 'meta.json'), 'utf-8')
  const meta = JSON.parse(metaRaw) as { control_epoch: number }
  assert.equal(meta.control_epoch, 3, '磁盘 meta control_epoch=3（未被旧重试覆盖回 2）')

  c.abort()
  await done
})

test('持久化重试悬挂期间 assignment 换代：旧回调完成不写入不覆盖新任务', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-MR2')
  const observers = new Map([['task-MR2', conv]])
  const events: string[] = []
  const taskA1 = makeTask('task-MR2', 'bind-MR2')
  stack.cloud.enqueue(taskA1)
  const crypto = togglableCrypto()
  const engine = newEngine(stack, observers, events, { crypto })
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-MR2') !== undefined, 3_000, '领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  await waitFor(() => (stack.cloud.eventsAcked.get(taskA1.assignment_id) ?? 0) >= 1, 4_000, '基线事件 ACK')

  // epoch=2 paused 持久化失败 → pending=true
  crypto.failProtect = true
  stack.cloud.setControl(taskA1.assignment_id, { control_epoch: 2, status: 'paused' })
  await waitFor(() => engine.controlStateOf('task-MR2')?.metaPersistPending === true, 5_000, '持久化失败标记 pending')

  // 挂起 renew（响应冻结为 epoch2 paused）→ 解除注入 → 挂起下一次持久化：
  // 旧 assignment 的链内重试悬挂在 persistMeta 中
  stack.cloud.holdRenew(taskA1.assignment_id)
  await waitFor(() => stack.cloud.heldRenewCount(taskA1.assignment_id) >= 1, 3_000, 'renew 已挂起')
  crypto.failProtect = false
  const hang = crypto.holdProtect()
  stack.cloud.releaseRenews(taskA1.assignment_id)
  await waitFor(() => hang.engaged, 5_000, '旧 assignment 的链内重试已挂起')

  // 悬挂期间换代：新 assignment B 被领取（同实例运行中无恢复扫描旧工作，正常激活）
  stack.cloud.expireAssignmentLease(taskA1.assignment_id)
  const taskA2 = makeTask('task-MR2', 'bind-MR2')
  stack.cloud.enqueue(taskA2)
  await waitFor(() => engine.controlStateOf('task-MR2')?.assignmentId === taskA2.assignment_id, 5_000, '新 assignment 领取')
  const obsAt = conv.observationCount

  // 旧重试完成：检测到任务已换代 → 不应用旧状态（不影响新任务的控制/meta）
  hang.release()
  await waitFor(() => events.some((e) => e.includes('持久化完成后检测到任务已换代')), 5_000, '旧重试完成后丢弃旧状态')

  // 新任务不受影响：控制保持领取值、meta 是新 assignment 自己的、门禁不受旧回调触碰
  const st = engine.controlStateOf('task-MR2')
  assert.equal(st?.assignmentId, taskA2.assignment_id)
  assert.equal(st?.controlEpoch, taskA2.control_epoch, '新任务 controlEpoch 不受旧回调影响')
  assert.equal(st?.gate, 'open', '新任务门禁不受旧回调影响')
  assert.equal(st?.metaPersistPending, false, '新任务无待持久化标记')
  const metaRawA2 = readFileSync(join(stack.home, 'session-tasks', taskA2.assignment_id, 'meta.json'), 'utf-8')
  const metaA2 = JSON.parse(metaRawA2) as { control_epoch: number }
  assert.equal(metaA2.control_epoch, taskA2.control_epoch, '新任务 meta 不被旧回调改写')
  await waitFor(() => conv.observationCount > obsAt, 4_000, '新任务照常观察推进（旧回调不影响）')
  assert.notEqual(engine.phaseOf('task-MR2'), 'blocked')

  c.abort()
  await done
})

test('多次控制更新持久化失败→解除后链内重试只持久化最新控制代', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-MR3')
  const observers = new Map([['task-MR3', conv]])
  const events: string[] = []
  const task = makeTask('task-MR3', 'bind-MR3')
  stack.cloud.enqueue(task)
  const crypto = togglableCrypto()
  const engine = newEngine(stack, observers, events, { crypto })
  const c = new AbortController()
  t.after(() => c.abort())
  const done = engine.run(c.signal)
  await waitFor(() => engine.phaseOf('task-MR3') !== undefined, 3_000, '领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  await waitFor(() => (stack.cloud.eventsAcked.get(task.assignment_id) ?? 0) >= 1, 4_000, '基线事件 ACK')

  // 注入失败：多次控制更新（epoch 递增）依次应用到内存，持久化全部失败
  crypto.failProtect = true
  stack.cloud.setControl(task.assignment_id, { control_epoch: 2, status: 'paused' })
  await waitFor(() => engine.controlStateOf('task-MR3')?.controlEpoch === 2, 5_000, 'epoch=2 应用（持久化失败）')
  stack.cloud.setControl(task.assignment_id, { control_epoch: 3, status: 'paused' })
  await waitFor(() => engine.controlStateOf('task-MR3')?.controlEpoch === 3, 5_000, 'epoch=3 应用（持久化失败）')
  stack.cloud.setControl(task.assignment_id, { control_epoch: 4, status: 'active' })
  await waitFor(() => engine.controlStateOf('task-MR3')?.controlEpoch === 4, 5_000, 'epoch=4 应用（持久化失败）')
  assert.equal(engine.controlStateOf('task-MR3')?.metaPersistPending, true, 'pending 保持')
  assert.equal(engine.controlStateOf('task-MR3')?.gate, 'paused_control', '门禁保持关闭')

  // 解除注入：下轮 renew 在链内重试 → 只持久化最新版本（epoch=4）并按其状态开门
  crypto.failProtect = false
  await waitFor(() => engine.controlStateOf('task-MR3')?.gate === 'open', 6_000, '重试成功且按最新控制开放门禁')
  const st = engine.controlStateOf('task-MR3')
  assert.equal(st?.controlEpoch, 4, '内存 controlEpoch=4')
  assert.equal(st?.metaPersistPending, false, 'pending 已清除')
  const metaRaw = readFileSync(join(stack.home, 'session-tasks', task.assignment_id, 'meta.json'), 'utf-8')
  const meta = JSON.parse(metaRaw) as { control_epoch: number }
  assert.equal(meta.control_epoch, 4, '磁盘 meta 为最新 epoch=4（非中间代 2/3）')
  await waitFor(() => conv.observationCount > 1, 9_000, '门禁开放后观察恢复')

  c.abort()
  await done
})

test('重启使用最新持久化控制代：meta epoch=3 续租接续且不重建基线', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-MR4')
  const observers = new Map([['task-MR4', conv]])
  const events: string[] = []
  const task = makeTask('task-MR4', 'bind-MR4')
  stack.cloud.enqueue(task)

  // 第一代：领取 → 基线 → 控制推进到 epoch=3 active（全部持久化成功）
  const engine1 = newEngine(stack, observers, events)
  const c1 = new AbortController()
  t.after(() => c1.abort())
  const run1 = engine1.run(c1.signal)
  await waitFor(() => engine1.phaseOf('task-MR4') !== undefined, 3_000, '领取')
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线观察')
  await waitFor(() => (stack.cloud.eventsAcked.get(task.assignment_id) ?? 0) >= 1, 4_000, '基线事件 ACK')
  stack.cloud.setControl(task.assignment_id, { control_epoch: 2, status: 'paused' })
  await waitFor(() => engine1.controlStateOf('task-MR4')?.gate === 'paused_control', 5_000, 'epoch=2 paused 应用')
  stack.cloud.setControl(task.assignment_id, { control_epoch: 3, status: 'active' })
  await waitFor(() => engine1.controlStateOf('task-MR4')?.gate === 'open', 5_000, 'epoch=3 active 应用')
  const metaPath = join(stack.home, 'session-tasks', task.assignment_id, 'meta.json')
  await waitFor(() => {
    const m = JSON.parse(readFileSync(metaPath, 'utf-8')) as { control_epoch: number }
    return m.control_epoch === 3
  }, 5_000, 'meta.json 已持久化 epoch=3')
  c1.abort()
  await run1

  // 重启：恢复扫描读 meta（epoch=3）→ renew 以最新持久化控制代续租接续
  const obsBefore = conv.observationCount
  const engine2 = newEngine(stack, observers, events)
  const c2 = new AbortController()
  t.after(() => c2.abort())
  const run2 = engine2.run(c2.signal)
  await waitFor(() => engine2.phaseOf('task-MR4') !== undefined, 5_000, '重启后经 renew 接续')
  assert.ok(
    stack.cloud.renewCalls.some((r) => r.assignmentId === task.assignment_id && r.control_epoch === 3),
    '续租请求携带 meta 持久化的 control_epoch=3（非过时代）',
  )
  const st = engine2.controlStateOf('task-MR4')
  assert.equal(st?.controlEpoch, 3, '内存 controlEpoch=3')
  assert.equal(st?.gate, 'open', '接续后门禁按最新控制开放')
  await waitFor(() => conv.observationCount > obsBefore, 9_000, '接续后观察恢复')
  assert.equal(
    (stack.cloud.eventRecords.get(task.assignment_id) ?? []).filter((r) => r.type === 'baseline').length,
    1,
    '接续不重建基线（水位延续）',
  )

  c2.abort()
  await run2
})
