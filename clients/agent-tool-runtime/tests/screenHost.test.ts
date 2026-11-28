/**
 * 契约 2.25：Runtime Host 的画面装配。会话行为用生产同一装配（assembleScreenPorts）驱动
 * 真实 ApiClient/FakeCloud 或内存传输层；能力上报与 screen.stop 走真实 LegacyExecution 全栈。
 * 画面与心跳隔离（设计 4.4）：回调异常不影响心跳成败与在线状态。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import type { RuntimeHost } from '@aid/local-tool-host-core'
import { POLL_INTERVAL_MS, POLL_JITTER_MS } from '@aid/local-tool-host-core/screen/protocol'
import type { ScreenSessionController } from '@aid/local-tool-host-core/screen/session'
import { ApiError, DeviceRevokedError, NetworkError } from '../src/apiClient.js'
import { main } from '../src/cli.js'
import { createScreenShellChannel } from '../src/managed-entry.js'
import { assembleScreenPorts, classifyScreenTransportError, openRuntimeHost, screenTransport } from '../src/runtimeHost.js'
import { FakeCloud } from './helpers/fakeCloud.js'
import { startTestStack } from './helpers/runtimeStack.js'

const SID = '33333333-3333-4333-8333-333333333333'
const SID2 = '44444444-4444-4444-8444-444444444444'
/** 网页侧的内网候选（10.200.100.9 不是测试机的网卡地址）：判定规则 2 的放行证据 */
const WEB_HOST_CANDIDATE = 'candidate:1 1 udp 2130706431 10.200.100.9 50000 typ host'

/** 用 managed-entry 的真实画面通道对象 + 假 Main 侧通道：出向帧记录到 sent，入向经 handleFrame */
function fakeShellIpc() {
  const sent: Array<Record<string, unknown>> = []
  const shell = createScreenShellChannel({
    send: message => { sent.push(message) },
    onMessage: () => {},
    onDisconnect: () => {},
  })
  return {
    shell,
    sent,
    /** 模拟采集页（经壳转交）的一条消息 */
    dispatch(sessionId: string, message: Record<string, unknown>) {
      assert.equal(shell.handleFrame({ event: 'runtime_screen', version: 1, session_id: sessionId, ...message }), true)
    },
    frames: (action?: string) => (action ? sent.filter(frame => frame.action === action) : sent),
  }
}

let sequence = 0
async function request(host: RuntimeHost, method: string, params: Record<string, string> = {}) {
  const reply = await host.request({ request_id: `r-${++sequence}`, method, params })
  assert.equal(reply.code, 0, `${method} 失败：${reply.error}`)
  return reply
}

test('画面接口错误分类：busy/ended/not_found/rejected/network/鉴权失败一律归为被拒', () => {
  assert.equal(classifyScreenTransportError(new ApiError(503, '繁忙', '繁忙', 'SCREEN_STREAM_BUSY')).kind, 'busy')
  assert.equal(classifyScreenTransportError(new ApiError(409, '已结束', '已结束', 'SCREEN_SESSION_ENDED')).kind, 'ended')
  assert.equal(classifyScreenTransportError(new ApiError(404, '不存在')).kind, 'not_found')
  assert.equal(classifyScreenTransportError(new ApiError(409, '上限', '上限', 'SCREEN_SIGNAL_LIMIT')).kind, 'rejected')
  assert.equal(classifyScreenTransportError(new NetworkError('断网')).kind, 'network')
  assert.equal(classifyScreenTransportError(new DeviceRevokedError('token 失效')).kind, 'rejected')
  assert.equal(classifyScreenTransportError(new Error('其他')).kind, 'rejected')
})

test('心跳通知 pending 会话：Host 立即回 prepared；网页 start 后壳收到带 stun_urls 的 open 帧；信令按序 POST', async () => {
  const { shell, dispatch, frames } = fakeShellIpc()
  let controller: ScreenSessionController | null = null
  const stack = await startTestStack({ onScreenHeartbeat: hb => controller?.onHeartbeatScreen(hb) })
  try {
    controller = assembleScreenPorts(screenTransport(stack.api), shell, { taskRunning: () => false })
    stack.cloud.screenSupport = true
    stack.cloud.screenNotice = { id: SID, state: 'pending' }
    await stack.cloud.waitFor(() => stack.cloud.screenSignals.some(s => s.type === 'prepared'), 5_000, 'prepared 信令')
    assert.equal(frames('open').length, 0, '收到网页 start 之前不建窗')
    stack.cloud.screenSessions.set(SID, { state: 'prepared', stun_urls: ['stun:stun.example.test:3478'], signals: [{ seq: 1, type: 'start', payload: {} }] })
    await stack.cloud.waitFor(() => frames('open').length === 1, 5_000, 'open 帧')
    assert.deepEqual(frames('open')[0], { event: 'runtime_screen', version: 1, action: 'open', session_id: SID, stun_urls: ['stun:stun.example.test:3478'] })
    // 壳回 offer 与本机候选 → Host 按调用顺序逐条 POST（public 候选不经内网暂存）
    dispatch(SID, { kind: 'offer', sdp: 'v=0-runtime-offer' })
    dispatch(SID, { kind: 'candidate', candidate: 'candidate:2 1 udp 2130706431 8.8.8.8 50001 typ host' })
    await stack.cloud.waitFor(() => stack.cloud.screenSignals.some(s => s.type === 'candidate'), 5_000, 'candidate 信令')
    const types = stack.cloud.screenSignals.map(s => s.type)
    assert.equal(types[0], 'prepared')
    assert.ok(types.indexOf('offer') < types.indexOf('candidate'), `信令必须按序发送：${types.join(',')}`)
  } finally { await stack.stop() }
})

test('旧服务端（心跳无画面字段）：没有任何画面行为，心跳照常', async () => {
  const { shell, frames } = fakeShellIpc()
  let controller: ScreenSessionController | null = null
  const stack = await startTestStack({ onScreenHeartbeat: hb => controller?.onHeartbeatScreen(hb) })
  try {
    controller = assembleScreenPorts(screenTransport(stack.api), shell, { taskRunning: () => false })
    const before = stack.cloud.heartbeatCount(stack.token)
    await delay(1_000)
    assert.ok(stack.cloud.heartbeatCount(stack.token) >= before + 3, '心跳照常')
    assert.equal(stack.cloud.screenSignals.length, 0, '不发任何信令')
    assert.equal(frames().length, 0, '不发任何私有帧')
  } finally { await stack.stop() }
})

test('画面回调抛异常：心跳仍成功，连接状态不变成 offline', async () => {
  const states: string[] = []
  const stack = await startTestStack({
    onScreenHeartbeat: () => { throw new Error('fixture-screen-callback') },
    onConnection: state => states.push(state),
  })
  try {
    await delay(700)
    assert.ok(stack.cloud.heartbeatCount(stack.token) >= 3, '心跳继续')
    assert.ok(states.length > 0, '有连接状态上报')
    assert.deepEqual([...new Set(states)], ['online'], `连接状态只应为 online：${states.join(',')}`)
  } finally { await stack.stop() }
})

test('画面租约：心跳 streaming 续期；30 秒没有续期就先关窗口并发出 end renewal_timeout（模拟时钟）', async t => {
  t.after(() => { t.mock.timers.reset() })
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] })
  async function drain(rounds = 8) { for (let i = 0; i < rounds; i++) await new Promise(resolve => setImmediate(resolve)) }
  async function advance(ms: number, step = 100) {
    for (let done = 0; done < ms; done += step) { await t.mock.timers.tick(Math.min(step, ms - done)); await drain() }
  }
  const { shell, dispatch, frames } = fakeShellIpc()
  const posted: Array<{ type: string; payload: unknown }> = []
  let queued: Array<{ seq: number; type: string; payload: unknown }> = []
  const transport = {
    async post(path: string, body: unknown) {
      const params = body as Record<string, unknown>
      if (path === '/api/local-tools/runtime/screen/session') {
        const signals = queued; queued = []
        return { session: { id: params['session_id'], state: 'prepared' }, signals }
      }
      posted.push({ type: String(params['type']), payload: params['payload'] })
      return { success: true, seq: posted.length }
    },
  }
  const controller = assembleScreenPorts(transport, shell, { taskRunning: () => false })
  controller.onHeartbeatScreen({ screen_session: { id: SID, state: 'pending' } })
  await advance(100)
  assert.equal(posted[0]?.type, 'prepared')
  queued.push({ seq: 1, type: 'start', payload: {} })
  await advance(POLL_INTERVAL_MS + POLL_JITTER_MS + 100)
  assert.equal(frames('open').length, 1, 'start 后建窗')
  dispatch(SID, { kind: 'offer', sdp: 'v=0-runtime-offer' })
  await advance(100)
  dispatch(SID, { kind: 'remote-candidate', candidate: WEB_HOST_CANDIDATE })
  dispatch(SID, { kind: 'connected' })
  await advance(100)
  assert.equal(posted.some(s => s.type === 'status'), true, '连接建立后报告 status')
  dispatch(SID, { kind: 'streaming', path: 'lan' })
  await advance(100)
  assert.deepEqual(posted.filter(s => s.type === 'status').at(-1)?.payload, { state: 'streaming', path: 'lan' })
  // 每 5 秒一次带续期的心跳，30 秒内不结束
  for (let done = 0; done < 30_000; done += 5_000) {
    await advance(5_000)
    controller.onHeartbeatScreen({ screen_session: { id: SID, state: 'streaming' } })
  }
  assert.equal(posted.some(s => s.type === 'end'), false, '有续期就不结束')
  // 31 秒没有续期：先关窗口，再发 end renewal_timeout
  await advance(31_000)
  assert.equal(frames('close').length, 1, '先关采集窗口')
  assert.equal(frames('close')[0]!.session_id, SID)
  assert.deepEqual(posted.filter(s => s.type === 'end').at(-1)?.payload, { reason: 'renewal_timeout' })
})

test('受管 Host 上报画面能力；CLI 形态不上报', async () => {
  const cloud = new FakeCloud()
  await cloud.start()
  const homes: string[] = []
  const previousHome = process.env.AIDWORK_RUNTIME_HOME
  try {
    // 形态一：desktop + 画面通道 → 心跳能力体带 screen_stream_v1 与 screen_stream
    let home = mkdtempSync(path.join(tmpdir(), 'screen-cap-on-'))
    homes.push(home)
    process.env.AIDWORK_RUNTIME_HOME = home
    cloud.issueCode('SCREEN-CAP-ON')
    assert.equal(await main(['pair', '--code', 'SCREEN-CAP-ON', '--server', cloud.baseUrl]), 0)
    cloud.screenSupport = true
    const { shell } = fakeShellIpc()
    const host = await openRuntimeHost({ supervisor: 'desktop', screen: shell })
    try {
      await request(host, 'start', { request_key: 'screen-cap-start' })
      await cloud.waitFor(() => {
        const capabilities = cloud.lastHeartbeatBody(cloud.lastPairToken!)?.capabilities as { capabilities?: string[] } | undefined
        return Array.isArray(capabilities?.capabilities) && capabilities.capabilities.includes('screen_stream_v1')
      }, 15_000, '画面能力心跳')
      const body = cloud.lastHeartbeatBody(cloud.lastPairToken!)!
      const capabilityBody = body['capabilities'] as Record<string, unknown>
      assert.deepEqual(capabilityBody['screen_stream'], { version: 1 }, '能力体带 screen_stream 版本标记')
      await request(host, 'stop', { request_key: 'screen-cap-stop' })
    } finally { await host.dispose() }
    // 形态二：CLI，无画面通道 → 不上报画面能力
    home = mkdtempSync(path.join(tmpdir(), 'screen-cap-off-'))
    homes.push(home)
    process.env.AIDWORK_RUNTIME_HOME = home
    cloud.issueCode('SCREEN-CAP-OFF')
    assert.equal(await main(['pair', '--code', 'SCREEN-CAP-OFF', '--server', cloud.baseUrl]), 0)
    const cliHost = await openRuntimeHost({ supervisor: 'cli' })
    try {
      await request(cliHost, 'start', { request_key: 'screen-cap-cli-start' })
      await cloud.waitFor(() => cloud.heartbeatCount(cloud.lastPairToken!) > 0, 15_000, 'CLI 心跳')
      const body = cloud.lastHeartbeatBody(cloud.lastPairToken!)!
      const capabilityBody = body['capabilities'] as Record<string, unknown>
      assert.equal((capabilityBody['capabilities'] as string[] | undefined)?.includes('screen_stream_v1') ?? false, false, 'CLI 不上报画面能力')
      assert.equal(Object.hasOwn(capabilityBody, 'screen_stream'), false)
      await request(cliHost, 'stop', { request_key: 'screen-cap-cli-stop' })
    } finally { await cliHost.dispose() }
  } finally {
    await cloud.stop()
    if (previousHome === undefined) delete process.env.AIDWORK_RUNTIME_HOME
    else process.env.AIDWORK_RUNTIME_HOME = previousHome
    for (const home of homes) rmSync(home, { recursive: true, force: true })
  }
})

test('screen.stop 管理请求：connecting 阶段结束为 runtime_rejected，streaming 阶段为 runtime_stopped；会话摘要随 host-state 出现与消失', async () => {
  const cloud = new FakeCloud()
  await cloud.start()
  const home = mkdtempSync(path.join(tmpdir(), 'screen-stop-'))
  const previousHome = process.env.AIDWORK_RUNTIME_HOME
  process.env.AIDWORK_RUNTIME_HOME = home
  try {
    const { shell, dispatch, frames } = fakeShellIpc()
    cloud.issueCode('SCREEN-STOP')
    assert.equal(await main(['pair', '--code', 'SCREEN-STOP', '--server', cloud.baseUrl]), 0)
    const host = await openRuntimeHost({ supervisor: 'desktop', screen: shell })
    try {
      await request(host, 'start', { request_key: 'screen-stop-start' })
      cloud.screenSupport = true
      cloud.screenNotice = { id: SID, state: 'pending' }
      await cloud.waitFor(() => cloud.screenSignals.some(s => s.type === 'prepared'), 20_000, 'prepared（等心跳）')
      const during = await request(host, 'getState') as { result: { screen?: { session_id?: string } } }
      assert.equal(during.result.screen?.session_id, SID, '有会话时 host-state 带 screen 摘要')
      cloud.screenSessions.set(SID, { state: 'prepared', stun_urls: ['stun:stun.example.test:3478'], signals: [{ seq: 1, type: 'start', payload: {} }] })
      await cloud.waitFor(() => frames('open').length === 1, 20_000, 'open 帧（等轮询）')
      // connecting 阶段：本机拒绝
      await request(host, 'screen.stop', { request_key: 'screen-stop-1', session_id: SID })
      cloud.screenNotice = { id: SID2, state: 'pending' }
      await cloud.waitFor(() => cloud.screenSignals.some(s => s.session_id === SID && s.type === 'end'), 5_000, '第一场 end')
      assert.deepEqual(cloud.screenSignals.filter(s => s.session_id === SID && s.type === 'end').at(-1)?.payload, { reason: 'runtime_rejected' })
      assert.equal(frames('close').length, 1, '拒绝同样先关窗口')
      // 第二场：走到 streaming 后停止
      cloud.screenSessions.set(SID2, { state: 'prepared', stun_urls: [], signals: [{ seq: 1, type: 'start', payload: {} }] })
      await cloud.waitFor(() => frames('open').length === 2, 20_000, '第二场 open 帧')
      dispatch(SID2, { kind: 'offer', sdp: 'v=0-runtime-offer' })
      await cloud.waitFor(() => cloud.screenSignals.some(s => s.session_id === SID2 && s.type === 'offer'), 5_000, '第二场 offer')
      cloud.screenSessions.get(SID2)!.signals.push({ seq: 2, type: 'answer', payload: { sdp: 'v=0-web-answer' } })
      dispatch(SID2, { kind: 'remote-candidate', candidate: WEB_HOST_CANDIDATE })
      dispatch(SID2, { kind: 'connected' })
      await cloud.waitFor(() => frames('send').some(frame => (frame.message as { kind?: string } | undefined)?.kind === 'verdict'), 10_000, '同机判定结论交给采集页')
      dispatch(SID2, { kind: 'streaming', path: 'lan' })
      await cloud.waitFor(() => cloud.screenSignals.some(s => s.session_id === SID2 && s.type === 'status'), 5_000, '第二场 status')
      const live = await request(host, 'getState') as { result: { screen?: { session_id?: string; state?: string } } }
      assert.equal(live.result.screen?.session_id, SID2)
      assert.ok(['streaming', 'locked'].includes(live.result.screen?.state ?? ''), `传画面中的摘要状态：${live.result.screen?.state}`)
      await request(host, 'screen.stop', { request_key: 'screen-stop-2', session_id: SID2 })
      await cloud.waitFor(() => cloud.screenSignals.some(s => s.session_id === SID2 && s.type === 'end'), 5_000, '第二场 end')
      assert.deepEqual(cloud.screenSignals.filter(s => s.session_id === SID2 && s.type === 'end').at(-1)?.payload, { reason: 'runtime_stopped' })
      const afterStop = await request(host, 'getState') as { result: { screen?: unknown } }
      assert.equal(Object.hasOwn(afterStop.result, 'screen'), false, '结束后 screen 摘要消失')
      await request(host, 'stop', { request_key: 'screen-stop-final' })
    } finally { await host.dispose() }
  } finally {
    await cloud.stop()
    if (previousHome === undefined) delete process.env.AIDWORK_RUNTIME_HOME
    else process.env.AIDWORK_RUNTIME_HOME = previousHome
    rmSync(home, { recursive: true, force: true })
  }
})
