/**
 * C3 门禁 #1/#4 集成验收（计划 §6：真实 Runtime 执行链 + fake Provider）：
 *
 * 链路保真：SessionTaskEngine → 定向 claim → **真实 invocationRunner**
 * （started → 桌面锁 → sessionPrecheck 锁内会话复核 → write-authorize → journal
 * fsync → provider call → operation-result/result outbox），Provider 为受信
 * fakeV2Provider 子进程；不注入空 runner。
 *
 * 覆盖：
 * 1. 无变化：正常发送一次（applied/verified + journal + outbox ACK）；
 * 2. 生成后新消息 / 等待锁期间人工回复（self）/ opening 前新入站 / 观察失败：
 *    发送副作用为 0（无 applied 回执、无 journal），invocation effect=none 收敛；
 * 3. 崩溃恢复四点：prepare 后未 claim（接续原 invocation，不重建执行单元）、
 *    已 claim 在途（等待终态不重发）、已有 journal/结果待补投（outbox 落盘，
 *    终态收敛不重发）、terminal（直接收敛）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer, type Server } from 'node:http'
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { ApiClient, type ClaimedInvocation } from '../src/apiClient.js'
import { SessionTaskEngine, type ObserverResult } from '../src/sessionTasks/engine.js'
import type { SessionCrypto } from '../src/sessionTasks/sessionStore.js'
import { runInvocation } from '../src/invocationRunner.js'
import { desktopLockName, deriveResourceKey, withDesktopLock } from '../src/desktopLock.js'
import { ResultOutbox, resultOutboxDir } from '../src/resultOutbox.js'
import type { ProviderManifest } from '../src/providers.js'

const V2_WEIXIN_MANIFEST: ProviderManifest = {
  provider_key: 'weixin',
  provider_id: 'ai.aidwork.weixin',
  tools: ['weixin_message_send_v2'],
  execution_target: 'local_required',
  protocol_version: 2,
  shared_lock_capable: true,
  write_tools: new Set(['weixin_message_send_v2']),
}

// ---------------- 会话 + 运行时双协议 fake 云端 ----------------

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
  polls: number
}

interface InvState {
  state: string
  claim_token: string | null
  args: Record<string, unknown>
  device_id: string
  permit: { permit_id: string } | null
}

type CallLog = Array<{ type: string; invocation_id?: string; payload?: unknown; at: number }>

class SessionExecCloud {
  private readonly server: Server
  private readonly queue: FakeTask[] = []
  private readonly claimedTasks = new Map<string, FakeTask>()
  readonly decisions = new Map<string, DecisionState>()
  readonly invocations = new Map<string, InvState>()
  readonly callLog: CallLog = []
  readonly prepareCalls: Array<{ assignmentId: string; decisionId: string }> = []
  readyAfterPolls = 1
  decisionAction: string | null = 'reply'
  /** operation-result 接线模式：'ok' | 'fail500'（退避重试）| 'fail409'（确定性 4xx → gave_up 保留） */
  resultMode: 'ok' | 'fail500' | 'fail409' = 'ok'
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

  appliedResults(): number {
    return this.callLog.filter((c) => c.type === 'operation_result' && (c.payload as Record<string, unknown>)?.['effect'] === 'applied').length
  }

  firstAssignmentId(): string {
    return [...this.claimedTasks.keys()][0]!
  }

  private async handle(req: import('node:http').IncomingMessage, res: import('node:http').ServerResponse): Promise<void> {
    const url = new URL(req.url ?? '/', 'http://localhost')
    const p = url.pathname
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

    if (p === '/api/local-tools/runtime/session-tasks/claim' && req.method === 'POST') {
      const task = this.queue.shift()
      if (!task) {
        res.statusCode = 204
        res.end()
        return
      }
      this.claimedTasks.set(task.assignment_id, task)
      ok({
        assignment_id: task.assignment_id, task_id: task.task_id, spec: task.spec,
        spec_revision: task.spec_revision, fence: task.fence, control_epoch: task.control_epoch,
        server_control_seq: 0, lease_seconds: 60,
        conversation_binding_id: String(task.spec.conversation_binding_id ?? ''),
        binding_version: 0, account_identity_version: 1,
      })
      return
    }
    let m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/renew$/.exec(p)
    if (m && req.method === 'POST') {
      const task = this.claimedTasks.get(m[1] as string)
      if (!task || Number(body['fence']) !== task.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'stale' })
        return
      }
      ok({ lease_seconds: 60, control: { status: 'active', control_epoch: task.control_epoch, server_control_seq: 0, completion_reason: null, blocked_reason: null } })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/events$/.exec(p)
    if (m && req.method === 'POST') {
      const records = (body['records'] as Array<{ local_seq: number }>) ?? []
      ok({ ack_seq: records.length ? records[records.length - 1]!.local_seq : 0, control: { status: 'active', control_epoch: 1, server_control_seq: 0 } })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/decisions$/.exec(p)
    if (m && req.method === 'POST') {
      const decisionId = randomUUID()
      this.decisions.set(decisionId, {
        assignmentId: m[1] as string, batchId: String(body['batch_id']), kind: String(body['decision_kind'] ?? 'reply'),
        status: 'pending', action: null, polls: 0,
      })
      ok({ decision_id: decisionId, status: 'pending' })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/decisions\/([^/]+)$/.exec(p)
    if (m && req.method === 'GET') {
      const decision = this.decisions.get(m[2] as string)
      if (!decision) {
        json(404, { success: false, code: 'NOT_FOUND', error: 'missing' })
        return
      }
      if (decision.status === 'pending') {
        decision.polls += 1
        if (decision.polls >= this.readyAfterPolls) {
          decision.status = 'ready'
          decision.action = this.decisionAction
        }
      }
      ok({
        decision_id: m[2], status: decision.status, decision_kind: decision.kind,
        batch_id: decision.batchId, input_version: 1, action: decision.action,
      })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/decisions\/([^/]+)\/prepare-send$/.exec(p)
    if (m && req.method === 'POST') {
      const task = this.claimedTasks.get(m[1] as string)
      if (!task || Number(body['fence']) !== task.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'fence 不匹配（真实服务端口径）' })
        return
      }
      this.prepareCalls.push({ assignmentId: m[1] as string, decisionId: m[2] as string })
      const decision = this.decisions.get(m[2] as string)
      if (!decision || decision.status !== 'ready' || decision.action !== 'reply') {
        ok({ invocation_id: null, decision_status: decision?.status ?? 'unknown', run_id: null })
        return
      }
      const invocationId = randomUUID()
      this.invocations.set(invocationId, {
        state: 'queued', claim_token: null,
        args: {
          protocol_version: 2, operation: 'weixin_message_send_v2', provider_key: 'weixin',
          target_ref: 'bind-1', target_handle: 'h', target_version: 'iv-1',
          payload_ref: `da:weixin.conversation.v1:session-reply:${m[2]}`,
          payload_hash: 'f'.repeat(64), request_id: `req-${randomUUID()}`,
          delivery_id: `del-${randomUUID()}`, authorization_revision: 'spec-1',
          authorization_epoch: 1, resource_key: 'rk', deadline_at: new Date(Date.now() + 600_000).toISOString(),
        },
        device_id: 'dev-session', permit: null,
      })
      ok({ invocation_id: invocationId, decision_status: 'ready', run_id: randomUUID() })
      return
    }
    m = /^\/api\/local-tools\/runtime\/session-tasks\/([^/]+)\/invocations\/([^/]+)\/claim$/.exec(p)
    if (m && req.method === 'POST') {
      const task = this.claimedTasks.get(m[1] as string)
      if (!task || Number(body['fence']) !== task.fence) {
        json(409, { success: false, code: 'STALE_ASSIGNMENT', error: 'fence 不匹配（真实服务端口径）' })
        return
      }
      const inv = this.invocations.get(m[2] as string)
      if (!inv) {
        json(404, { success: false, code: 'NOT_FOUND', error: 'missing' })
        return
      }
      if (inv.state !== 'queued') {
        ok({ invocation: null, state: inv.state })
        return
      }
      inv.state = 'claimed'
      inv.claim_token = 'ct-' + randomUUID().slice(0, 8)
      ok({
        invocation_id: m[2], tool_name: 'weixin_message_send_v2', arguments: inv.args,
        claim_token: inv.claim_token, lease_expires_at: new Date(Date.now() + 60_000).toISOString(),
        provider: 'weixin',
      })
      return
    }
    // ---- 运行时 invocation 生命周期（对齐 FakeCloud 语义） ----
    m = /^\/api\/local-tools\/runtime\/invocations\/([^/]+)\/(started|progress|result|write-authorize|operation-result)$/.exec(p)
    if (m && req.method === 'POST') {
      const inv = this.invocations.get(m[1] as string)
      const action = m[2] as string
      if (action === 'started') {
        this.callLog.push({ type: 'started', invocation_id: m[1], at: Date.now() })
        if (inv && inv.state === 'claimed') inv.state = 'running'
        ok({ state: 'running' })
        return
      }
      if (action === 'progress') {
        ok({ seq: Number(body['seq'] ?? 0), cancel: false })
        return
      }
      if (!inv || (inv.claim_token && inv.claim_token !== body['claim_token'])) {
        json(404, { success: false, detail: { error: 'CLAIM_MISMATCH', message: 'claim 不匹配' } })
        return
      }
      if (action === 'write-authorize') {
        const invRequestId = inv!.args['request_id']
        if (typeof invRequestId === 'string' && invRequestId !== body['request_id']) {
          json(409, { detail: { error: 'REQUEST_ID_MISMATCH', message: 'request_id 不匹配' } })
          return
        }
        if (inv!.permit) {
          json(409, { detail: { error: 'PERMIT_ALREADY_ISSUED', message: '一次性' } })
          return
        }
        const permit = { permit_id: randomUUID(), permit_token: randomUUID().replace(/-/g, ''), deadline_at: new Date(Date.now() + 120_000).toISOString() }
        inv!.permit = { permit_id: permit.permit_id }
        this.callLog.push({ type: 'write_authorize', invocation_id: m[1], payload: body, at: Date.now() })
        json(200, { success: true, ...permit })
        return
      }
      if (action === 'operation-result') {
        if (this.resultMode === 'fail500') {
          json(500, { detail: { error: 'INJECTED', message: '结果上报注入失败（待补投）' } })
          return
        }
        if (this.resultMode === 'fail409') {
          json(409, { detail: { error: 'INJECTED_CONFLICT', message: '结果上报注入确定性拒绝（gave_up 保留）' } })
          return
        }
        const effect = String(body['effect'] ?? '')
        this.callLog.push({ type: 'operation_result', invocation_id: m[1], payload: body, at: Date.now() })
        inv!.state = effect === 'applied' ? 'succeeded' : effect === 'unknown' ? 'unknown' : 'failed'
        ok({ success: true, state: inv!.state, effect, run_state: null, late: false })
        return
      }
      if (action === 'result') {
        ok({ state: 'succeeded' })
        return
      }
    }
    json(404, { success: false, detail: { error: 'NOT_FOUND', message: `unhandled ${p}` } })
  }
}

// ---------------- 可注入剧本的观察器 ----------------

class ScriptedConversation {
  private readonly messages: Array<{ sender: 'peer' | 'self'; text: string; id: string }> = []
  observationCount = 0
  /** 观察失败注入（precheck 场景置 true；观察异常不得发送） */
  failObservations = false

  constructor(readonly bindingId: string) {}

  push(text: string, sender: 'peer' | 'self' = 'peer'): void {
    this.messages.push({ sender, text, id: `${this.bindingId}-m${this.messages.length}` })
  }

  observer(_task: unknown, request: { watermark: { last_local_message_id: string | null } | null }): ObserverResult {
    this.observationCount += 1
    if (this.failObservations) throw new Error('注入：观察失败')
    const after = request.watermark?.last_local_message_id ?? null
    let start = 0
    if (after) {
      const found = this.messages.findIndex((m) => m.id === after)
      start = found >= 0 ? found + 1 : this.messages.length
    }
    return {
      observation_id: `${this.bindingId}-obs-${this.observationCount}`,
      account_identity_version: 1,
      conversation_binding_id: this.bindingId,
      binding_version: 0,
      observed_at: new Date().toISOString(),
      coverage: 'complete_window',
      ordered_messages: this.messages.slice(start).map((m) => ({
        sender: m.sender, text: m.text, local_message_id: m.id, source_evidence_ref: `ev-${m.id}`,
      })),
      window_fingerprint: `${this.bindingId}-fp-${this.observationCount}`,
      gap_reason: null,
    }
  }
}

function fakeCrypto(): SessionCrypto {
  return {
    protect: async (plain: string) => Buffer.from(plain, 'utf8').toString('base64'),
    unprotect: async (cipher: string) => Buffer.from(cipher, 'base64').toString('utf8'),
  }
}

interface ExecStack {
  cloud: SessionExecCloud
  api: ApiClient
  conv: ScriptedConversation
  home: string
  dataDir: string
  outbox: ResultOutbox
  resourceKey: string
  cleanup: () => Promise<void>
  newEngine: (events: string[], signal?: AbortSignal) => SessionTaskEngine
}

async function newExecStack(taskId: string, specExtra: Record<string, unknown> = {}): Promise<ExecStack> {
  const cloud = new SessionExecCloud()
  const baseUrl = await cloud.start()
  const api = new ApiClient(baseUrl, 'test-token')
  const conv = new ScriptedConversation('bind-1')
  const home = mkdtempSync(path.join(os.tmpdir(), 'st-exec-home-'))
  const dataDir = mkdtempSync(path.join(os.tmpdir(), 'st-exec-data-'))
  const outbox = new ResultOutbox(resultOutboxDir(dataDir))
  const resourceKey = deriveResourceKey(randomUUID())
  const { ProviderSet } = await import('../src/providerManager.js')
  const here = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'))
  const providers = new ProviderSet(
    { weixin: path.join(here, 'helpers', 'fakeV2Provider.js') },
    { shutdownTimeoutMs: 2_000 },
  )
  const lockName = desktopLockName(resourceKey)
  const newEngine = (events: string[], signal?: AbortSignal): SessionTaskEngine => {
    const runner = (inv: ClaimedInvocation, sessionPrecheck: () => Promise<void>): Promise<void> =>
      runInvocation(inv, {
        api,
        providers,
        desktopCheck: async () => true,
        desktopResourceKey: resourceKey,
        runtimeDataDir: dataDir,
        resultOutbox: outbox,
        manifests: { weixin: V2_WEIXIN_MANIFEST },
        sessionPrecheck,
        shutdownSignal: signal,
        onEvent: (msg) => events.push(msg),
      })
    return new SessionTaskEngine({
      api,
      runtimeHome: home,
      crypto: fakeCrypto(),
      runtimeInstanceId: `rt-${randomUUID().slice(0, 8)}`,
      withLock: <T,>(fn: () => Promise<T>) => withDesktopLock(lockName, fn),
      observer: async (task, request) => conv.observer(task, request),
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
  cloud.enqueue({
    task_id: taskId,
    assignment_id: randomUUID(),
    spec: { goal: 'g', conversation_binding_id: 'bind-1', ...specExtra },
    fence: 1,
    control_epoch: 1,
    spec_revision: 1,
  })
  const cleanup = async () => {
    await providers.shutdownAll()
    await cloud.stop()
    rmSync(home, { recursive: true, force: true })
    rmSync(dataDir, { recursive: true, force: true })
  }
  return { cloud, api, conv, home, dataDir, outbox, resourceKey, cleanup, newEngine }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function waitFor(predicate: () => boolean, timeoutMs: number, what: string): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return
    await sleep(25)
  }
  assert.ok(false, `等待超时: ${what}`)
}

function journalFiles(dir: string): string[] {
  const journalDir = path.join(dir, 'journal')
  return existsSync(journalDir) ? readdirSync(journalDir).filter((f) => f.endsWith('.jsonl')) : []
}

async function driveToExecuting(stack: ExecStack, controller: AbortController, events: string[]): Promise<SessionTaskEngine> {
  const engine = stack.newEngine(events, controller.signal)
  void engine.run(controller.signal)
  await waitFor(() => stack.conv.observationCount >= 1, 4_000, '基线建立')
  stack.conv.push('在吗')
  await waitFor(() => ['send_ready', 'executing'].includes(engine.phaseOf('st-exec') ?? ''), 6_000, '决策 ready')
  await waitFor(() => engine.phaseOf('st-exec') === 'executing', 6_000, '进入执行')
  return engine
}

// ---------------- 用例 ----------------

test('无变化：真实链路发送一次（write-authorize + journal + operation-result）', async (t) => {
  const stack = await newExecStack('st-exec')
  t.after(() => stack.cleanup())
  const events: string[] = []
  const controller = new AbortController()
  t.after(() => controller.abort())
  const engine = await driveToExecuting(stack, controller, events)
  await waitFor(() => engine.phaseOf('st-exec') === 'waiting_peer', 15_000, '执行完成回等待')
  assert.equal(stack.cloud.appliedResults(), 1, '恰好一次 applied 发送')
  assert.ok(stack.cloud.callLog.some((c) => c.type === 'write_authorize'), '真实许可链')
  assert.ok(journalFiles(stack.dataDir).length >= 1, 'journal 已落盘')
  assert.equal(stack.cloud.prepareCalls.length, 1, '单一执行单元')
  controller.abort()
})

test('生成后新消息：锁内复核拦截，发送副作用为 0', async (t) => {
  const stack = await newExecStack('st-exec')
  t.after(() => stack.cleanup())
  const events: string[] = []
  const controller = new AbortController()
  t.after(() => controller.abort())
  const engine = stack.newEngine(events, controller.signal)
  void engine.run(controller.signal)
  await waitFor(() => stack.conv.observationCount >= 1, 4_000, '基线建立')
  stack.conv.push('在吗')
  await waitFor(() => engine.phaseOf('st-exec') === 'send_ready', 6_000, '决策 ready')
  // 决策生成后、执行器锁内复核前，客户追加消息
  stack.conv.push('等等，我补充一句')
  await waitFor(() => engine.phaseOf('st-exec') === 'waiting_peer', 15_000, '复核拦截后回等待')
  await sleep(500)
  assert.equal(stack.cloud.appliedResults(), 0, '不得有发送副作用')
  assert.ok(!stack.cloud.callLog.some((c) => c.type === 'write_authorize'), '未申请许可')
  assert.deepEqual(journalFiles(stack.dataDir), [], '无 journal')
  // 拦截后事实不丢：水位未推进，新消息进入常规观察合批 → 新决策
  await waitFor(
    () => [...stack.cloud.decisions.values()].filter((d) => d.kind === 'reply').length >= 2,
    10_000,
    '新消息形成新决策',
  )
  controller.abort()
})

test('等待锁期间人工回复（self 消息）：发送副作用为 0', async (t) => {
  const stack = await newExecStack('st-exec')
  t.after(() => stack.cleanup())
  const events: string[] = []
  const controller = new AbortController()
  t.after(() => controller.abort())
  const engine = stack.newEngine(events, controller.signal)
  void engine.run(controller.signal)
  await waitFor(() => stack.conv.observationCount >= 1, 4_000, '基线建立')
  stack.conv.push('在吗')
  await waitFor(() => engine.phaseOf('st-exec') === 'send_ready', 6_000, '决策 ready')
  stack.conv.push('我先人工回一句', 'self')
  await waitFor(() => engine.phaseOf('st-exec') === 'waiting_peer', 15_000, '复核拦截')
  await sleep(300)
  assert.equal(stack.cloud.appliedResults(), 0, '人工回复期间不得发送')
  controller.abort()
})

test('opening 前新入站：开场白发送副作用为 0', async (t) => {
  const stack = await newExecStack('st-exec', { opening_text: '您好，想确认周五时间' })
  t.after(() => stack.cleanup())
  const events: string[] = []
  const controller = new AbortController()
  t.after(() => controller.abort())
  const engine = stack.newEngine(events, controller.signal)
  void engine.run(controller.signal)
  stack.conv.push('历史消息')
  await waitFor(() => stack.conv.observationCount >= 1, 4_000, '基线建立')
  await waitFor(() => engine.phaseOf('st-exec') === 'send_ready', 8_000, 'opening 决策 ready')
  stack.conv.push('客户抢先回复了')
  await waitFor(() => engine.phaseOf('st-exec') === 'waiting_peer', 15_000, 'opening 复核拦截')
  await sleep(300)
  assert.equal(stack.cloud.appliedResults(), 0, 'opening 前有新入站不得发送')
  controller.abort()
})

test('观察失败：发送副作用为 0', async (t) => {
  const stack = await newExecStack('st-exec')
  t.after(() => stack.cleanup())
  const events: string[] = []
  const controller = new AbortController()
  t.after(() => controller.abort())
  const engine = stack.newEngine(events, controller.signal)
  void engine.run(controller.signal)
  await waitFor(() => stack.conv.observationCount >= 1, 4_000, '基线建立')
  stack.conv.push('在吗')
  await waitFor(() => engine.phaseOf('st-exec') === 'send_ready', 6_000, '决策 ready')
  stack.conv.failObservations = true
  // 复核观察失败 → 不发送；解除注入后常规观察恢复（消息不丢）
  await waitFor(() => engine.phaseOf('st-exec') === 'waiting_peer', 15_000, '观察失败拦截')
  await sleep(300)
  assert.equal(stack.cloud.appliedResults(), 0, '观察失败不得发送')
  stack.conv.failObservations = false
  await waitFor(
    () => [...stack.cloud.decisions.values()].filter((d) => d.kind === 'reply').length >= 1,
    10_000,
    '观察恢复后批次继续',
  )
  controller.abort()
})

test('崩溃点①：prepare 后未 claim——重启接续原 invocation，不重建执行单元', async (t) => {
  const stack = await newExecStack('st-exec')
  t.after(() => stack.cleanup())
  const events: string[] = []
  const controller1 = new AbortController()
  const engine1 = await driveToExecuting(stack, controller1, events)
  // 引擎已进入 executing 且 invocation 仍 queued（runner 尚未领取即"崩溃"）
  const invocationId = [...stack.cloud.invocations.keys()][0]!
  assert.equal(stack.cloud.invocations.get(invocationId)!.state, 'queued', '未 claim 崩溃点')
  controller1.abort()
  await sleep(150)

  const controller2 = new AbortController()
  t.after(() => controller2.abort())
  const engine2 = stack.newEngine(events, controller2.signal)
  void engine2.run(controller2.signal)
  // 恢复后必须回到 executing 并按原 invocation 推进（不新建 prepare/invocation）
  await waitFor(() => engine2.phaseOf('st-exec') === 'executing', 8_000, '恢复 executing 相位')
  await waitFor(() => engine2.phaseOf('st-exec') === 'waiting_peer', 15_000, '原 invocation 执行完成')
  assert.equal(stack.cloud.prepareCalls.length, 1, '未重建执行单元')
  assert.equal(stack.cloud.invocations.size, 1, '未产生新 invocation')
  assert.equal(stack.cloud.appliedResults(), 1, '原 invocation 恰好发送一次')
  controller2.abort()
})

test('崩溃点②：已 claim 在途——等待终态，unknown 不重发', async (t) => {
  const stack = await newExecStack('st-exec')
  t.after(() => stack.cleanup())
  const events: string[] = []
  const controller1 = new AbortController()
  const engine1 = stack.newEngine(events, controller1.signal)
  // 用"许可服务不可用"阻断在 claim 之后、执行之前：改为直接占用 invocation
  void engine1.run(controller1.signal)
  await waitFor(() => stack.conv.observationCount >= 1, 4_000, '基线建立')
  stack.conv.push('在吗')
  await waitFor(() => engine1.phaseOf('st-exec') === 'executing', 8_000, '进入执行')
  const invocationId = [...stack.cloud.invocations.keys()][0]!
  const inv = stack.cloud.invocations.get(invocationId)!

  // 模拟"已 claim"崩溃点：置为 running（他进程持有），重启后必须等待而非重发
  inv.state = 'running'
  controller1.abort()
  await sleep(150)

  const controller2 = new AbortController()
  t.after(() => controller2.abort())
  const engine2 = stack.newEngine(events, controller2.signal)
  void engine2.run(controller2.signal)
  await waitFor(() => engine2.phaseOf('st-exec') === 'executing', 8_000, '恢复 executing 相位')
  await sleep(600)
  assert.equal(stack.cloud.appliedResults(), 0, '在途不得重复执行')
  // 终态 unknown（租约回收语义）→ 收敛 waiting_peer，仍不重发
  inv.state = 'unknown'
  await waitFor(() => engine2.phaseOf('st-exec') === 'waiting_peer', 10_000, 'unknown 终态收敛')
  assert.equal(stack.cloud.appliedResults(), 0, 'unknown 不重发')
  assert.equal(stack.cloud.prepareCalls.length, 1, '未重建执行单元')
  controller2.abort()
})

test('崩溃点③④：journal 已落盘/结果待补投——outbox 保留，恢复不重发', async (t) => {
  const stack = await newExecStack('st-exec')
  t.after(() => stack.cleanup())
  const events: string[] = []
  const controller1 = new AbortController()
  // 确定性 4xx：runner 标记 gave_up 保留 outbox 条目后正常结束（不悬挂）
  stack.cloud.resultMode = 'fail409'
  const engine1 = stack.newEngine(events, controller1.signal)
  void engine1.run(controller1.signal)
  await waitFor(() => stack.conv.observationCount >= 1, 4_000, '基线建立')
  stack.conv.push('在吗')
  await waitFor(() => engine1.phaseOf('st-exec') === 'executing', 8_000, '进入执行')
  const invocationId = [...stack.cloud.invocations.keys()][0]!
  // journal（动作已发生）+ outbox（结果待补投，gave_up 保留）均落盘
  await waitFor(
    () => journalFiles(stack.dataDir).length >= 1 && existsSync(path.join(resultOutboxDir(stack.dataDir), `${invocationId}.json`)),
    30_000,
    'journal + outbox 落盘',
  )
  // 等 engine1 的动作单元收尾：日志中出现第 2 条 execution_phase（waiting_peer）
  const assignmentId = stack.cloud.firstAssignmentId()
  const logPath = path.join(stack.home, 'session-tasks', assignmentId, 'events.jsonl')
  await waitFor(() => {
    if (!existsSync(logPath)) return false
    const lines = readFileSync(logPath, 'utf8').trim().split('\n').filter(Boolean)
    return lines.filter((l) => { try { return JSON.parse(l)['type'] === 'execution_phase' } catch { return false } }).length >= 2
  }, 20_000, 'engine1 收尾事件落盘')
  controller1.abort()
  await sleep(200)

  // 构造"finishExecution 未落盘即崩溃"的现场：用真实 SessionStore API 追加
  // execution_phase(executing)（与引擎写入格式一致：加密 payload + fsync + 序号连续）
  const { SessionStore } = await import('../src/sessionTasks/sessionStore.js')
  const store = new SessionStore({ runtimeHome: stack.home, assignmentId, crypto: fakeCrypto() })
  await store.replay() // 初始化 localSeq/seenEventIds——追加序号必须接续既有日志
  const decisionId = stack.cloud.prepareCalls[0]!.decisionId
  await store.appendEncrypted(
    `execution-phase-crash-${randomUUID()}`,
    'execution_phase',
    { decision_id: decisionId, invocation_id: invocationId, phase_from: 'waiting_peer', phase_to: 'executing' },
    1,
  )

  // invocation 处于 running（结果未补投）→ 恢复 executing 后等待终态
  const inv = stack.cloud.invocations.get(invocationId)!
  assert.equal(inv.state, 'running', '结果未补投（云端未见终态）')
  const controller2 = new AbortController()
  t.after(() => controller2.abort())
  const engine2 = stack.newEngine(events, controller2.signal)
  void engine2.run(controller2.signal)
  await waitFor(() => engine2.phaseOf('st-exec') === 'executing', 8_000, '恢复 executing 相位')
  await sleep(400)
  assert.equal(
    stack.cloud.callLog.filter((c) => c.type === 'write_authorize').length,
    1,
    '在途/待补投不得再次申请许可',
  )
  // 补投后终态 unknown：收敛 waiting_peer，仍不重发
  inv.state = 'unknown'
  await waitFor(() => engine2.phaseOf('st-exec') === 'waiting_peer', 10_000, '终态收敛')
  assert.equal(stack.cloud.prepareCalls.length, 1, '未重建执行单元')
  assert.equal(stack.cloud.appliedResults(), 0, '结果待补投场景不重复发送')
  controller2.abort()
})
