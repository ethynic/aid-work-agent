/**
 * 测试智能体独立探针（第 B 轮）：不走开发智能体的既有用例组织，直接断言两件它没固定下来的事。
 * 1) pending → screen.stop 的完整私有帧动作序列（open → send* → close）与信令类型顺序；
 * 2) 画面信令接口 401（鉴权失败）被归类为 rejected：会话不撤销、连接状态只有 online、心跳照常。
 */
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import type { RuntimeHost } from '@aid/local-tool-host-core'
import type { ScreenSessionController } from '@aid/local-tool-host-core/screen/session'
import { SCREEN_SIGNAL_PATH } from '@aid/local-tool-host-core/screen/protocol'
import { main } from '../src/cli.js'
import { createScreenShellChannel } from '../src/managed-entry.js'
import { assembleScreenPorts, openRuntimeHost, screenTransport } from '../src/runtimeHost.js'
import { FakeCloud } from './helpers/fakeCloud.js'
import { startTestStack } from './helpers/runtimeStack.js'

const SID = '55555555-5555-4555-8555-555555555555'
/** 网页侧的内网候选（不是测试机的网卡地址） */
const WEB_HOST_CANDIDATE = 'candidate:1 1 udp 2130706431 10.200.100.9 50000 typ host'

/** managed-entry 的真实画面通道 + 假 Main 侧通道：出向帧记录到 sent，入向经 handleFrame */
function fakeShellIpc() {
  const sent: Array<Record<string, unknown>> = []
  const shell = createScreenShellChannel({ send: message => { sent.push(message) }, onMessage: () => {}, onDisconnect: () => {} })
  return {
    shell,
    sent,
    dispatch(sessionId: string, message: Record<string, unknown>) {
      assert.equal(shell.handleFrame({ event: 'runtime_screen', version: 1, session_id: sessionId, ...message }), true)
    },
  }
}

let sequence = 0
async function request(host: RuntimeHost, method: string, params: Record<string, string> = {}) {
  const reply = await host.request({ request_id: `probe-${++sequence}`, method, params })
  assert.equal(reply.code, 0, `${method} 失败：${reply.error}`)
  return reply
}

test('探针1：pending → start → open → send(answer,candidate,verdict) → streaming → screen.stop，帧动作与信令顺序固定', async () => {
  const cloud = new FakeCloud()
  await cloud.start()
  const home = mkdtempSync(path.join(tmpdir(), 'probe-seq-home-'))
  const previousHome = process.env.AIDWORK_RUNTIME_HOME
  process.env.AIDWORK_RUNTIME_HOME = home
  const { shell, dispatch, sent } = fakeShellIpc()
  try {
    cloud.issueCode('PROBE-SEQ')
    assert.equal(await main(['pair', '--code', 'PROBE-SEQ', '--server', cloud.baseUrl]), 0)
    const host = await openRuntimeHost({ supervisor: 'desktop', screen: shell })
    try {
      await request(host, 'start', { request_key: 'probe-seq-start' })
      cloud.screenSupport = true
      cloud.screenNotice = { id: SID, state: 'pending' }
      const waitFor = (predicate: () => boolean, what: string) => cloud.waitFor(predicate, 20_000, what)
      await waitFor(() => cloud.screenSignals.some(s => s.type === 'prepared'), 'prepared（等心跳）')
      cloud.screenSessions.set(SID, { state: 'prepared', stun_urls: ['stun:stun.example.test:3478'], signals: [{ seq: 1, type: 'start', payload: {} }] })
      await waitFor(() => sent.some(frame => frame.action === 'open'), 'open 帧')
      assert.deepEqual(sent[0], { event: 'runtime_screen', version: 1, action: 'open', session_id: SID, stun_urls: ['stun:stun.example.test:3478'] })

      // 壳回 offer 与本机候选 → Host 逐条 POST
      dispatch(SID, { kind: 'offer', sdp: 'v=0-probe-offer' })
      dispatch(SID, { kind: 'candidate', candidate: 'candidate:2 1 udp 2130706431 8.8.8.8 50001 typ host' })
      await waitFor(() => cloud.screenSignals.some(s => s.type === 'candidate'), 'candidate 信令')
      // 网页 answer 与 candidate 经读取接口下发 → Host 原样转给采集页
      cloud.screenSessions.get(SID)!.signals.push(
        { seq: 2, type: 'answer', payload: { sdp: 'v=0-probe-answer' } },
        { seq: 3, type: 'candidate', payload: { candidate: 'candidate:3 1 udp 2130706431 10.200.100.9 50002 typ host' } })
      await waitFor(() => sent.filter(frame => frame.action === 'send').length >= 2, 'answer/candidate 下发帧')
      // 网页候选 + 连接建立 → 同机判定结论交给采集页
      dispatch(SID, { kind: 'remote-candidate', candidate: WEB_HOST_CANDIDATE })
      dispatch(SID, { kind: 'connected' })
      await waitFor(() => sent.some(frame => (frame.message as { kind?: string } | undefined)?.kind === 'verdict'), 'verdict 下发帧')
      dispatch(SID, { kind: 'streaming', path: 'lan' })
      await waitFor(() => cloud.screenSignals.some(s => s.type === 'status'), 'status 信令')

      // 到达 streaming 后 screen.stop：close 帧先行，最后一条信令是 end runtime_stopped
      await request(host, 'screen.stop', { request_key: 'probe-seq-stop', session_id: SID })
      await waitFor(() => cloud.screenSignals.some(s => s.session_id === SID && s.type === 'end'), 'end 信令')

      const actions = sent.map(frame => frame.action)
      assert.deepEqual(actions, ['open', 'send', 'send', 'send', 'close'], `私有帧动作序列：${JSON.stringify(actions)}`)
      const sendKinds = sent.filter(frame => frame.action === 'send').map(frame => (frame.message as { kind: string }).kind)
      assert.deepEqual(sendKinds, ['answer', 'candidate', 'verdict'], `下发消息顺序：${JSON.stringify(sendKinds)}`)
      const signals = cloud.screenSignals.filter(s => s.session_id === SID).map(s => s.type)
      assert.deepEqual(signals, ['prepared', 'offer', 'candidate', 'status', 'status', 'end'], `信令顺序：${JSON.stringify(signals)}`)
      const lastStatus = cloud.screenSignals.filter(s => s.type === 'status').at(-1)!.payload as Record<string, unknown>
      assert.deepEqual(lastStatus, { state: 'streaming', path: 'lan' })
      assert.deepEqual(cloud.screenSignals.filter(s => s.type === 'end').at(-1)!.payload, { reason: 'runtime_stopped' })
      assert.equal(sent.at(-1)!.action, 'close', '最后一步私有帧是 close')
      await request(host, 'stop', { request_key: 'probe-seq-final' })
    } finally { await host.dispose() }
  } finally {
    await cloud.stop()
    if (previousHome === undefined) delete process.env.AIDWORK_RUNTIME_HOME
    else process.env.AIDWORK_RUNTIME_HOME = previousHome
    rmSync(home, { recursive: true, force: true })
  }
})

test('探针2：画面信令接口 401 → DeviceRevokedError 归为 rejected：会话继续、连接状态只有 online、心跳照常', async () => {
  const states: string[] = []
  let controller: ScreenSessionController | null = null
  const stack = await startTestStack({ onConnection: value => states.push(value), onScreenHeartbeat: hb => controller?.onHeartbeatScreen(hb) })
  // fakeCloud 的 handle 是私有方法，探针在运行时把它包一层：画面信令接口一律 401（鉴权失败）
  const cloud = stack.cloud as unknown as { handle: (req: unknown, res: unknown) => Promise<void> }
  const originalHandle = cloud.handle.bind(stack.cloud)
  cloud.handle = async (req, res) => {
    const url = (req as { url?: string }).url ?? ''
    if (url.startsWith(SCREEN_SIGNAL_PATH)) {
      const response = res as { writeHead(code: number, headers: Record<string, string>): void; end(body: string): void }
      response.writeHead(401, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ detail: { error: 'DEVICE_TOKEN_INVALID' } }))
      return
    }
    await originalHandle(req, res)
  }
  const { shell, dispatch, sent } = fakeShellIpc()
  try {
    controller = assembleScreenPorts(screenTransport(stack.api), shell, { taskRunning: () => false })
    stack.cloud.screenSupport = true
    // 先放会话表再开通知，避免首次读取恰好 404 把会话收掉
    stack.cloud.screenSessions.set(SID, { state: 'prepared', stun_urls: [], signals: [{ seq: 1, type: 'start', payload: {} }] })
    stack.cloud.screenNotice = { id: SID, state: 'pending' }
    await stack.cloud.waitFor(() => sent.some(frame => frame.action === 'open'), 10_000, 'open 帧（prepared 401 不挡会话推进）')
    assert.equal(sent[0]!.action, 'open')
    dispatch(SID, { kind: 'offer', sdp: 'v=0-probe-401' })
    await stack.cloud.waitFor(() => stack.cloud.heartbeatCount(stack.token) >= 3, 10_000, '心跳照常')
    assert.deepEqual([...new Set(states)], ['online'], `连接状态只应为 online：${JSON.stringify(states)}`)
    assert.equal(sent.some(frame => frame.action === 'close'), false, '鉴权失败不结束会话')
  } finally { await stack.stop() }
})
