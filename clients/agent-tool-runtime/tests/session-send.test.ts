/**
 * SessionTaskEngine C3 执行链验收测试（计划 §6）：
 *
 * 1. send_ready → prepare-send → executing → 定向 claim → 注入的 v2 执行器
 *    （write-authorize/journal/outbox 均在真实 runner 内，此处注入 fake）→
 *    waiting_peer；
 * 2. 决策 action=wait → 不进入执行链，直接 waiting_peer；
 * 3. prepare-send 返回 superseded（invocation_id=null）→ 放弃发送相位；
 * 4. opening：基线建立后提交 decision_kind=opening（不占 reply 批次语义）；
 * 5. 崩溃恢复：execution_phase 日志回放恢复 executing，claim 返回终态 →
 *    waiting_peer（unknown 不重发——由服务端评估阻断）；
 * 6. 未注入 runInvocation → 执行相位保守 blocked。
 *
 * 专用 fake 云端在本文件内实现（C3 协议：prepare-send/定向 claim）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer, type Server } from 'node:http'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { ApiClient, type ClaimedInvocation } from '../src/apiClient.js'
import { SessionTaskEngine, type ObserverResult } from '../src/sessionTasks/engine.js'
import type { SessionCrypto } from '../src/sessionTasks/sessionStore.js'

// ---------------- fake C3 云端 ----------------

interface FakeTask {
  task_id: string
  assignment_id: string
  spec: Record<string, unknown>
  fence: number
  control_epoch: number
  spec_revision: number
}

interface DecisionState {
  assignmentId: string
  batchId: string
  kind: string
  status: string
  action: string | null
  polls?: number
}

class FakeC3Cloud {
  private readonly server: Server
  private readonly queue: FakeTask[] = []
  private readonly claimedTasks = new Map<string, FakeTask>()
  readonly decisions = new Map<string, DecisionState>()
  readonly createDecisionCalls: Array<{ assignmentId: string; batchId: string; kind: string }> = []
  readonly prepareCalls: Array<{ assignmentId: string; decisionId: string }> = []
  readonly invocations = new Map<string, { state: string; args: Record<string, unknown> }>()
  readyAfterPolls = 1
  decisionAction: string | null = 'reply'
  prepareResult: { invocation_id: string | null; decision_status?: string } | null = null
  offline = false
  baseUrl = ''

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

  async start(): Promise<string> {
    await new Promise<void>((resolve) => this.server.listen(0, '127.0.0.1', resolve))
    const addr = this.server.address()
    this.baseUrl = `http://127.0.0.1:${typeof addr === 'object' && addr ? addr.port : 0}`
    return this.baseUrl
  }

  async stop(): Promise<void> {
    const srv = this.server as Server & { closeAllConnections?: () => void }
    srv.closeAllConnections?.()
    await new Promise<void>((resolve) => this.server.close(() => resolve()))
  }

  private async handle(req: import('node:http').IncomingMessage, res: import('node:http').ServerResponse): Promise<void> {
    if (this.offline) {
      req.socket.destroy()
      return
    }
    const path = new URL(req.url ?? '/', 'http://localhost').pathname
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
      this.claimedTasks.set(task.assignment_id, task)
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
    let m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/renew$/.exec(path)
    if (m && req.method === 'POST') {
      const task = this.claimedTasks.get(m[1] as string)
      if (!task || Number(body['fence']) !== task.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'stale' })
        return
      }
      ok({
        lease_seconds: 60,
        control: { status: 'active', control_epoch: task.control_epoch, server_control_seq: 0, completion_reason: null, blocked_reason: null },
      })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/events$/.exec(path)
    if (m && req.method === 'POST') {
      const records = (body['records'] as Array<{ local_seq: number }>) ?? []
      const ack = records.length ? records[records.length - 1]!.local_seq : 0
      ok({ ack_seq: ack, control: { status: 'active', control_epoch: 1, server_control_seq: 0 } })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/decisions$/.exec(path)
    if (m && req.method === 'POST') {
      const decisionId = randomUUID()
      const kind = String(body['decision_kind'] ?? 'reply')
      this.createDecisionCalls.push({ assignmentId: m[1] as string, batchId: String(body['batch_id']), kind })
      this.decisions.set(decisionId, {
        assignmentId: m[1] as string,
        batchId: String(body['batch_id']),
        kind,
        status: 'pending',
        action: null,
      })
      ok({ decision_id: decisionId, status: 'pending' })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/decisions\/([^/]+)$/.exec(path)
    if (m && req.method === 'GET') {
      const decision = this.decisions.get(m[2] as string)
      if (!decision) {
        json(404, { success: false, code: 'NOT_FOUND', error: 'not found' })
        return
      }
      if (decision.status === 'pending') {
        decision.polls = (decision.polls ?? 0) + 1
        if (decision.polls >= this.readyAfterPolls) {
          decision.status = 'ready'
          decision.action = this.decisionAction
        }
      }
      ok({
        decision_id: m[2],
        status: decision.status,
        decision_kind: decision.kind,
        batch_id: decision.batchId,
        input_version: 1,
        action: decision.action,
      })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/decisions\/([^/]+)\/prepare-send$/.exec(path)
    if (m && req.method === 'POST') {
      const task = this.claimedTasks.get(m[1] as string)
      if (!task || Number(body['fence']) !== task.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'fence 不匹配（真实服务端口径）' })
        return
      }
      this.prepareCalls.push({ assignmentId: m[1] as string, decisionId: m[2] as string })
      if (this.prepareResult) {
        const result = this.prepareResult
        if (result.invocation_id) {
          this.invocations.set(result.invocation_id, { state: 'queued', args: {} })
        }
        ok({ invocation_id: result.invocation_id, decision_status: result.decision_status ?? null, run_id: null })
        return
      }
      const decision = this.decisions.get(m[2] as string)
      if (!decision || decision.status !== 'ready' || decision.action !== 'reply') {
        ok({ invocation_id: null, decision_status: decision?.status ?? 'unknown' })
        return
      }
      const invocationId = randomUUID()
      this.invocations.set(invocationId, { state: 'queued', args: { operation: 'weixin_message_send_v2' } })
      ok({ invocation_id: invocationId, decision_status: 'ready', run_id: randomUUID() })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/invocations\/([^/]+)\/claim$/.exec(path)
    if (m && req.method === 'POST') {
      const fenceTask = this.claimedTasks.get(m[1] as string)
      if (!fenceTask || Number(body['fence']) !== fenceTask.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'fence 不匹配（真实服务端口径）' })
        return
      }
      const invocation = this.invocations.get(m[2] as string)
      if (!invocation) {
        json(404, { success: false, code: 'NOT_FOUND', error: 'missing' })
        return
      }
      if (invocation.state !== 'queued') {
        ok({ invocation: null, state: invocation.state })
        return
      }
      invocation.state = 'claimed'
      ok({
        invocation_id: m[2],
        tool_name: 'weixin_message_send_v2',
        arguments: invocation.args,
        claim_token: 'ct-' + randomUUID().slice(0, 8),
        lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
        provider: 'weixin',
      })
      return
    }
    json(404, { success: false, code: 'NOT_FOUND', error: `unhandled ${path}` })
  }

  /** 定向 claim 后由测试驱动 invocation 终态（模拟 result 已上报） */
  finishInvocation(invocationId: string, state: string): void {
    const inv = this.invocations.get(invocationId)
    if (inv) inv.state = state
  }
}

// ---------------- fake 会话/观察器 ----------------

function fakeCrypto(): SessionCrypto {
  return {
    protect: async (plain: string) => Buffer.from(plain, 'utf8').toString('base64'),
    unprotect: async (cipher: string) => Buffer.from(cipher, 'base64').toString('utf8'),
  }
}

class FakeConversation {
  private readonly script: ObserverMessage2[]
  private idx = 0
  observationCount = 0

  constructor(private readonly fingerprintBase: string, messages: string[] = []) {
    this.script = messages.map((text, i) => ({
      sender: 'peer',
      text,
      local_message_id: `${fingerprintBase}-m${i}`,
      source_evidence_ref: `ev-${i}`,
    }))
  }

  push(text: string): void {
    this.script.push({
      sender: 'peer',
      text,
      local_message_id: `${this.fingerprintBase}-m${this.script.length}`,
      source_evidence_ref: `ev-${this.script.length}`,
    })
  }

  observer(_task: unknown, request: { watermark: { last_local_message_id: string | null } | null }): ObserverResult {
    this.observationCount += 1
    const after = request.watermark?.last_local_message_id ?? null
    let start = 0
    if (after) {
      const found = this.script.findIndex((m) => m.local_message_id === after)
      start = found >= 0 ? found + 1 : this.script.length
    }
    const ordered = this.script.slice(start)
    return {
      observation_id: `${this.fingerprintBase}-obs-${this.observationCount}`,
      account_identity_version: 1,
      conversation_binding_id: this.fingerprintBase,
      binding_version: 0,
      observed_at: new Date().toISOString(),
      coverage: 'complete_window',
      ordered_messages: ordered,
      window_fingerprint: `${this.fingerprintBase}-fp-${this.observationCount}`,
      gap_reason: null,
    }
  }
}

type ObserverMessage2 = { sender: 'peer'; text: string; local_message_id: string; source_evidence_ref: string }

function makeTask(taskId: string, bindingId: string, specExtra: Record<string, unknown> = {}): FakeTask {
  return {
    task_id: taskId,
    assignment_id: randomUUID(),
    spec: { goal: 'g', conversation_binding_id: bindingId, ...specExtra },
    fence: 1,
    control_epoch: 1,
    spec_revision: 1,
  }
}

function newEngine(
  stack: { api: ApiClient; home: string },
  observers: Map<string, FakeConversation>,
  events: string[],
  runner?: (inv: ClaimedInvocation) => Promise<void>,
): SessionTaskEngine {
  return new SessionTaskEngine({
    api: stack.api,
    runtimeHome: stack.home,
    crypto: fakeCrypto(),
    runtimeInstanceId: `rt-${randomUUID().slice(0, 8)}`,
    observer: async (task, request) => {
      const conv = observers.get(task.taskId)
      if (!conv) throw new Error('无会话脚本')
      return conv.observer(task, request)
    },
    emit: (msg) => events.push(msg),
    runInvocation: runner,
    claimIntervalMs: 40,
    renewIntervalMs: 500,
    batchSilenceMs: 60,
    batchMaxWaitMs: 300,
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

async function newStack(): Promise<{ cloud: FakeC3Cloud; api: ApiClient; home: string; cleanup: () => Promise<void> }> {
  const cloud = new FakeC3Cloud()
  const baseUrl = await cloud.start()
  const api = new ApiClient(baseUrl, 'test-token')
  const home = mkdtempSync(join(tmpdir(), 'st-send-'))
  const cleanup = async () => {
    await cloud.stop()
    rmSync(home, { recursive: true, force: true })
  }
  return { cloud, api, home, cleanup }
}

// ---------------- 用例 ----------------

test('send_ready → prepare-send → 定向 claim → v2 执行器 → waiting_peer', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-S')
  const engine = newEngine(stack, new Map([['task-S', conv]]), [], (inv) => {
    stack.cloud.finishInvocation(inv.invocation_id, 'succeeded')
    return Promise.resolve()
  })
  const controller = new AbortController()
  t.after(() => controller.abort())
  void engine.run(controller.signal)

  const task = makeTask('task-S', 'bind-S')
  stack.cloud.enqueue(task)
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线建立')
  conv.push('在吗')
  await waitFor(() => engine.phaseOf('task-S') === 'decision_pending' || engine.phaseOf('task-S') === 'send_ready', 4_000, '决策提交')
  await waitFor(() => engine.phaseOf('task-S') === 'send_ready', 4_000, '决策 ready')
  await waitFor(() => engine.phaseOf('task-S') === 'executing', 4_000, '进入执行')
  await waitFor(() => engine.phaseOf('task-S') === 'waiting_peer', 6_000, '执行完成回等待')
  assert.ok(stack.cloud.prepareCalls.length >= 1, 'prepare-send 被调用')
  assert.ok([...stack.cloud.invocations.values()].some((i) => i.state === 'succeeded'), 'invocation 终态 succeeded')
  controller.abort()
})

test('决策 action=wait：不进执行链，直接 waiting_peer', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  stack.cloud.decisionAction = 'wait'
  const conv = new FakeConversation('bind-W')
  const runnerCalls: string[] = []
  const engine = newEngine(stack, new Map([['task-W', conv]]), [], (inv) => {
    runnerCalls.push(inv.invocation_id)
    return Promise.resolve()
  })
  const controller = new AbortController()
  t.after(() => controller.abort())
  void engine.run(controller.signal)
  stack.cloud.enqueue(makeTask('task-W', 'bind-W'))
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线建立')
  conv.push('先别回')
  await waitFor(() => engine.phaseOf('task-W') === 'waiting_peer', 6_000, 'wait 决策回等待')
  await sleep(300)
  assert.deepEqual(runnerCalls, [], 'wait 不触发执行')
  assert.deepEqual(stack.cloud.prepareCalls, [], 'wait 不触发 prepare-send')
  controller.abort()
})

test('prepare-send 返回 superseded：放弃发送相位', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-X')
  const engine = newEngine(stack, new Map([['task-X', conv]]), [])
  const controller = new AbortController()
  t.after(() => controller.abort())
  void engine.run(controller.signal)
  stack.cloud.enqueue(makeTask('task-X', 'bind-X'))
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线建立')
  conv.push('在吗')
  await waitFor(() => engine.phaseOf('task-X') === 'send_ready', 5_000, '决策 ready')
  stack.cloud.prepareResult = { invocation_id: null, decision_status: 'superseded' }
  await waitFor(() => engine.phaseOf('task-X') === 'waiting_peer', 5_000, '放弃发送回等待')
  controller.abort()
})

test('opening：基线后提交 decision_kind=opening', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-O')
  const engine = newEngine(stack, new Map([['task-O', conv]]), [], (inv) => {
    stack.cloud.finishInvocation(inv.invocation_id, 'succeeded')
    return Promise.resolve()
  })
  const controller = new AbortController()
  t.after(() => controller.abort())
  void engine.run(controller.signal)
  stack.cloud.enqueue(makeTask('task-O', 'bind-O', { opening_text: '您好，想确认周五时间' }))
  conv.push('历史消息')
  await waitFor(
    () => stack.cloud.createDecisionCalls.some((c) => c.kind === 'opening'),
    5_000,
    'opening 决策提交',
  )
  const openingCall = stack.cloud.createDecisionCalls.find((c) => c.kind === 'opening')
  assert.equal(openingCall?.batchId, 'opening')
  // opening ready(action=reply) → 走完整发送链
  await waitFor(() => engine.phaseOf('task-O') === 'waiting_peer', 8_000, 'opening 执行完成')
  controller.abort()
})

test('崩溃恢复：executing 相位经日志回放接续，终态收敛 waiting_peer', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-R')
  const events: string[] = []
  const controller = new AbortController()
  t.after(() => controller.abort())

  // 第一段实例：推进到 executing 后停止（不执行 runner——模拟崩溃）
  const engine1 = newEngine(stack, new Map([['task-R', conv]]), events, () => Promise.resolve())
  void engine1.run(controller.signal)
  stack.cloud.enqueue(makeTask('task-R', 'bind-R'))
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线建立')
  conv.push('在吗')
  await waitFor(() => engine1.phaseOf('task-R') === 'executing', 6_000, '进入执行')
  controller.abort()
  await sleep(100)

  // invocation 由"旧进程"置为 succeeded（结果链已上报）→ 新实例 claim 得终态
  const invocationId = [...stack.cloud.invocations.keys()][0]!
  stack.cloud.finishInvocation(invocationId, 'succeeded')

  // 第二段实例：同 assignment 续租接续（renew 放行），回放 execution_phase
  const controller2 = new AbortController()
  t.after(() => controller2.abort())
  const engine2 = newEngine(stack, new Map([['task-R', conv]]), events, () => {
    throw new Error('不应重复执行（unknown/终态不重发）')
  })
  void engine2.run(controller2.signal)
  await waitFor(() => engine2.phaseOf('task-R') === 'waiting_peer', 8_000, '恢复后收敛 waiting_peer')
  controller2.abort()
})

test('未注入 v2 执行器：执行相位保守 blocked', async (t) => {
  const stack = await newStack()
  t.after(() => stack.cleanup())
  const conv = new FakeConversation('bind-N')
  const engine = newEngine(stack, new Map([['task-N', conv]]), []) // 无 runInvocation
  const controller = new AbortController()
  t.after(() => controller.abort())
  void engine.run(controller.signal)
  stack.cloud.enqueue(makeTask('task-N', 'bind-N'))
  await waitFor(() => conv.observationCount >= 1, 3_000, '基线建立')
  conv.push('在吗')
  await waitFor(() => engine.phaseOf('task-N') === 'blocked', 8_000, '执行相位保守 blocked')
  controller.abort()
})
