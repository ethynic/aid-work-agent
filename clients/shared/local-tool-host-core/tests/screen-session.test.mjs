import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { test } from 'node:test'
import { POLL_INTERVAL_MS, POLL_JITTER_MS } from '../dist/screen/protocol.js'
import { ScreenSessionController } from '../dist/screen/session.js'

// 画面会话控制器：把信令、同机判定、画面租约、锁屏检测和任务优先串起来。
// 外部世界全部通过注入的端口对象交互，时间用 node:test 的模拟时钟推进（每次小步推进再排空微任务，
// 因为一次大步推进不会在定时器之间拨动模拟时钟）。固定的不变量：
//   任何对外方法都不向调用方抛出；结束会话时先关窗口再发 end；服务器或网页发起的结束不发 end；
//   日志里不出现地址、名字、候选、SDP；会话结束后不再有任何定时器触发的调用。

const SID = '11111111-1111-4111-8111-111111111111'
const SID2 = '22222222-2222-4222-8222-222222222222'
const STUN = ['stun:stun.example.test:3478']
const WEB_HOST = 'candidate:1 1 udp 2130706431 192.168.1.9 50000 typ host'
const WEB_MAPPED_SAME = 'candidate:1 1 udp 1694498815 1.2.3.4 50001 typ srflx raddr 192.168.1.9 rport 50000'
const WEB_MAPPED_OTHER = 'candidate:1 1 udp 84281040 5.6.7.8 50001 typ srflx raddr 10.0.0.9 rport 50000'
const WEB_MDNS = 'candidate:1 1 udp 2130706431 abcd-1234.local 50000 typ host'
const own = (address, type = 'host', id = 1) => `candidate:${id} 1 udp 2130706431 ${address} 5000${id} typ ${type}`
const pending = (id = SID) => ({ screen_session: { id, state: 'pending' } })
const renewal = (id = SID) => ({ screen_session: { id, state: 'streaming' } })

function restartTimers(t) { t.mock.timers.reset(); t.mock.timers.enable({ apis: ['setTimeout', 'setInterval', 'Date'] }) }
async function drain(rounds = 8) { for (let i = 0; i < rounds; i++) await new Promise(resolve => setImmediate(resolve)) }
async function advance(t, ms, step = 100) {
  for (let done = 0; done < ms; done += step) { await t.mock.timers.tick(Math.min(step, ms - done)); await drain() }
}
// 传画面期间推进时间：每 5 秒来一次带续期的心跳，画面租约不会到期
async function viewing(t, c, ms) {
  for (let done = 0; done < ms; done += 5_000) { await advance(t, 5_000); c.onHeartbeatScreen(renewal()) }
}

function harness(patch = () => {}) {
  const calls = []   // 全部端口调用：[名称, ...参数]
  const events = []  // 决定先后顺序的几类：open / close / post:<type> / send:<kind> / abandon
  const posted = []  // 发给服务器的信令
  const sent = []    // 交给采集页的消息
  const logs = []
  const reads = []   // 每次轮询的应答，按先后取用；用完返回「没有新信令」
  const state = {
    readDelayMs: 0, stunUrlsInRead: STUN, interactive: true, taskRunning: false, cpu: null,
    local: ['192.168.1.5', '127.0.0.1'], isLocalPort: async () => false, resolve: async () => ({ ok: false, code: 'ENOTFOUND' }),
  }
  const signaling = {
    // 返回的是 ScreenSignalingClient.readSession 解析之后的形状
    async readSession(id, after) {
      calls.push(['read', after, Date.now()])
      if (state.readDelayMs > 0) await new Promise(resolve => setTimeout(resolve, state.readDelayMs))
      const next = reads.shift()
      const stunUrls = next && 'stunUrls' in next ? next.stunUrls : state.stunUrlsInRead
      return { session: { id, state: next?.state ?? 'prepared', endReason: next?.endReason ?? null, stunUrls }, signals: next?.signals ?? [] }
    },
    async postSignal(id, type, payload) { events.push(`post:${type}`); posted.push({ id, type, payload }); return { ok: true, attempts: 1 } },
    abandon() { calls.push(['abandon']) },
  }
  const shell = {
    open(session) { calls.push(['open', session]); events.push('open') },
    send(id, message) { calls.push(['send', message.kind]); events.push(`send:${message.kind}`); sent.push(message) },
    close(id) { calls.push(['close', id]); events.push('close') },
  }
  const ports = {
    signaling, shell,
    network: {
      localAddresses() { calls.push(['localAddresses']); return state.local },
      async resolveHost(name) { calls.push(['resolveHost']); return state.resolve(name) },
    },
    async isLocalPort(port, protocol) { calls.push(['isLocalPort', port, protocol]); return state.isLocalPort(port, protocol) },
    async desktopInteractive() { calls.push(['desktopInteractive']); return state.interactive },
    cpuBusyPercent() { calls.push(['cpuBusyPercent']); return state.cpu },
    taskRunning() { calls.push(['taskRunning']); return state.taskRunning },
    stunUrls: STUN,
    log(event, fields) { calls.push(['log', event]); logs.push({ event, fields }) },
  }
  patch({ ports, state, signaling })
  const count = name => calls.filter(call => call[0] === name).length
  const types = () => posted.map(signal => signal.type)
  const kinds = () => sent.map(message => message.kind)
  return { ports, calls, events, posted, sent, logs, reads, state, count, types, kinds, readTimes: () => calls.filter(c => c[0] === 'read').map(c => c[2]) }
}

// 走到「正在建立连接」：pending → prepared → 网页 start → 通知壳建窗
async function toConnecting(t, f, c) {
  c.onHeartbeatScreen(pending())
  await drain()
  f.reads.push({ state: 'prepared', signals: [{ seq: 1, type: 'start', payload: {} }] })
  await advance(t, 700)
}
// 走到「已连上，等待判定」：证据指向同网段的另一台电脑（规则 2）
async function toVerifying(t, f, c) {
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST })
  c.onShellMessage(SID, { kind: 'connected' })
  await drain()
}
// 走到「正在传画面」
async function toStreaming(t, f, c) {
  await toVerifying(t, f, c)
  c.onShellMessage(SID, { kind: 'streaming', path: 'lan' })
  await drain()
}

test('正常走完一遍：pending → prepared → start → 按顺序转交 → connected → 判定为另一台电脑 → 传画面 → 锁屏与解锁 → 续期 → 网页结束', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  assert.equal(c.summary(), null)

  c.onHeartbeatScreen({ success: true, screen_stream: { version: 1 }, ...pending() })
  assert.deepEqual(f.types(), ['prepared'])
  assert.deepEqual(f.posted[0], { id: SID, type: 'prepared', payload: {} })
  assert.deepEqual(c.summary(), { sessionId: SID, state: 'connecting', degraded: false, startedAt: null, secondsSinceRenewal: null })
  await drain()
  assert.equal(f.count('read'), 1, '开始会话后立刻取一次信令')

  // 网页准备好，Runtime 通知壳建窗并带上 STUN 列表
  f.reads.push({ state: 'prepared', signals: [{ seq: 1, type: 'start', payload: {} }] })
  await advance(t, 700)
  assert.deepEqual(f.calls.find(call => call[0] === 'open')?.[1], { sessionId: SID, stunUrls: STUN })

  // 采集页的连接邀约（不含画面）
  c.onShellMessage(SID, { kind: 'offer', sdp: 'v=0 offer' })
  await drain()
  assert.deepEqual(f.posted.at(-1), { id: SID, type: 'offer', payload: { sdp: 'v=0 offer' } })

  // 网页的应答和地址：按到达顺序转交
  f.reads.push({ state: 'connecting', signals: [
    { seq: 2, type: 'answer', payload: { sdp: 'v=0 answer' } },
    { seq: 3, type: 'candidate', payload: { candidate: WEB_HOST, sdp_mid: '0', sdp_mline_index: 0 } },
  ] })
  await advance(t, 700)
  assert.deepEqual(f.sent, [
    { kind: 'answer', sdp: 'v=0 answer' },
    { kind: 'candidate', candidate: WEB_HOST, sdp_mid: '0', sdp_mline_index: 0 },
  ])

  // 采集页报来证据并连上：Runtime 报告已连接，并给出同机判定
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST })
  c.onShellMessage(SID, { kind: 'connected' })
  await drain()
  assert.deepEqual(f.posted.at(-1), { id: SID, type: 'status', payload: { state: 'connected' } })
  assert.deepEqual(f.sent.slice(-1), [{ kind: 'verdict', verdict: 'other', rule: 2 }])
  assert.equal(c.summary().state, 'connecting')
  assert.ok(f.logs.some(log => log.event === 'screen_verdict' && log.fields.verdict === 'other' && log.fields.rule === 2))

  // 开始出画面
  c.onShellMessage(SID, { kind: 'streaming', path: 'lan' })
  await drain()
  assert.deepEqual(f.posted.at(-1), { id: SID, type: 'status', payload: { state: 'streaming', path: 'lan' } })
  const streaming = c.summary()
  assert.equal(streaming.state, 'streaming'); assert.equal(streaming.degraded, false)
  assert.equal(typeof streaming.startedAt, 'number'); assert.equal(streaming.secondsSinceRenewal, 0)

  // 出画面后不再轮询，靠心跳续期和获知结束
  const readsBefore = f.count('read')
  await advance(t, 3_000)
  assert.equal(f.count('read'), readsBefore)

  // 锁屏与解锁：变化时才发 status
  f.state.interactive = false
  await advance(t, 2_100)
  assert.deepEqual(f.posted.at(-1).payload, { state: 'locked' })
  assert.equal(c.summary().state, 'locked')
  f.state.interactive = true
  await advance(t, 2_000)
  assert.deepEqual(f.posted.at(-1).payload, { state: 'unlocked' })
  assert.equal(c.summary().state, 'streaming')
  await advance(t, 4_000)
  assert.equal(f.types().filter(type => type === 'status').length, 4, 'connected、streaming、locked、unlocked 各一条')

  // 心跳续期
  await advance(t, 5_000)
  assert.ok(c.summary().secondsSinceRenewal >= 5)
  c.onHeartbeatScreen(renewal())
  assert.equal(c.summary().secondsSinceRenewal, 0)

  // 网页结束：服务端在心跳里通知，Runtime 关窗口、不再发 end
  c.onHeartbeatScreen({ screen_session: { id: SID, state: 'ended' } })
  assert.equal(c.summary(), null)
  assert.equal(f.events.includes('close'), true)
  assert.equal(f.types().includes('end'), false)
})

test('轮询：下一次从收到上一次响应时起算 500 毫秒加抖动，响应慢时不叠加', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  c.onHeartbeatScreen(pending())
  await drain()
  assert.equal(f.readTimes().length, 1)
  f.state.readDelayMs = 300 // 之后每次响应要 300 毫秒才回来
  await advance(t, 750, 50)
  assert.equal(f.readTimes().length, 2, '响应没回来之前不发下一次')
  // 第二次请求在 500～600 毫秒发出，响应在 800～900 回来；如果按请求的间隔算，1 250 毫秒时已经有第三次请求
  await advance(t, 500, 50)
  assert.equal(f.readTimes().length, 2, '响应回来后 500 毫秒内不发下一次')
  await advance(t, 400, 50)
  assert.equal(f.readTimes().length, 3)
  const gap = f.readTimes()[2] - (f.readTimes()[1] + 300)
  assert.ok(gap >= 400 && gap < 700, `响应到下一次请求的间隔应为 500 毫秒加少量抖动，实际 ${gap}`)
  assert.deepEqual(f.calls.filter(call => call[0] === 'read').map(call => call[1]), [0, 0, 0], '没有新信令时游标不动')
})

test('轮询间隔带少量随机抖动，不是固定节拍', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  c.onHeartbeatScreen(pending())
  await drain()
  await advance(t, 12_000, 10)
  const times = f.readTimes()
  const gaps = times.slice(1).map((time, index) => time - times[index])
  assert.ok(gaps.length >= 15, `12 秒内应当轮询十几次，实际 ${gaps.length}`)
  // 模拟时钟每 10 毫秒走一步，记录到的时刻最多比真实时刻晚 10 毫秒
  assert.ok(gaps.every(gap => gap >= POLL_INTERVAL_MS && gap < POLL_INTERVAL_MS + POLL_JITTER_MS + 10), `间隔都在 500 毫秒加少量抖动之内：${gaps.join(',')}`)
  assert.ok(new Set(gaps).size > 1, '间隔不全相同')
})

test('画面租约：每次续期都重新计时', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toStreaming(t, f, c)
  // 每 29 秒续期一次：始终不到期
  for (let i = 0; i < 3; i++) {
    await advance(t, 29_000)
    assert.equal(c.summary().state, 'renewing')
    c.onHeartbeatScreen(renewal())
    assert.equal(c.summary().secondsSinceRenewal, 0)
  }
  assert.equal(f.types().includes('end'), false)
  await advance(t, 30_200)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'renewal_timeout' })
})

test('轮询只在不传画面的三个阶段进行，游标随信令前进', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  await advance(t, 1_300)
  // 第一次取信令时游标为 0；收到 start（编号 1）之后游标前进并保持
  const afters = f.calls.filter(call => call[0] === 'read').map(call => call[1])
  assert.deepEqual(afters.slice(0, 4), [0, 0, 1, 1])
  assert.ok(afters.slice(2).every(after => after === 1))
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST })
  c.onShellMessage(SID, { kind: 'connected' })
  c.onShellMessage(SID, { kind: 'streaming', path: 'lan' })
  await drain()
  const before = f.count('read')
  await advance(t, 5_000)
  assert.equal(f.count('read'), before, '传画面后停止轮询')
})

test('规则 1：网页候选解析出本机地址，判同机；页面结束后以 same_machine 结束，全程没有出画面', async t => {
  restartTimers(t)
  const f = harness(({ state }) => { state.resolve = async () => ({ ok: true, address: '192.168.1.5' }) })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 200)
  assert.deepEqual(f.sent.filter(message => message.kind !== 'answer'), [
    { kind: 'resolved', id: 1, address: '192.168.1.5' },
    { kind: 'verdict', verdict: 'same', rule: 1 },
  ])
  assert.equal(f.count('isLocalPort'), 0, '已经有确切结论时不再查端口表')
  // 采集页据此结束连接并报告
  c.onShellMessage(SID, { kind: 'closed', reason: 'same_machine' })
  assert.deepEqual(f.posted.at(-1), { id: SID, type: 'end', payload: { reason: 'same_machine' } })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
  assert.equal(c.summary(), null)
  assert.deepEqual(f.types(), ['prepared', 'status', 'end'])
})

test('规则 3：两端公网映射地址不同，判另一台电脑', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS }) // 解析不出
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_OTHER })
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 200)
  assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'other', rule: 3 })
  assert.equal(f.count('isLocalPort'), 0)
})

test('判定前先等还没回来的证据：名字解析晚到时用晚到的结果', async t => {
  restartTimers(t)
  const f = harness(({ state }) => {
    state.resolve = async () => { await new Promise(resolve => setTimeout(resolve, 600)); return { ok: true, address: '192.168.1.5' } }
  })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 300)
  assert.equal(f.kinds().includes('verdict'), false, '名字还在解析，先不下结论')
  await advance(t, 500)
  assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'same', rule: 1 })
})

test('判定前最多等约 1.5 秒：本机的公网映射地址一直不来也照样下结论', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST }) // 规则 2：不需要映射地址就能判
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 1_400)
  assert.equal(f.kinds().includes('verdict'), false)
  await advance(t, 300)
  assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'other', rule: 2 })
})

test('没有配置 STUN 时不等公网映射地址', async t => {
  restartTimers(t)
  const f = harness(({ state }) => { state.stunUrlsInRead = [] })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  assert.deepEqual(f.calls.find(call => call[0] === 'open')?.[1].stunUrls, [])
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST })
  c.onShellMessage(SID, { kind: 'connected' })
  await drain()
  assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'other', rule: 2 })
})

test('映射地址为空的 mapped 消息按缺字段忽略，不算已经报来映射地址', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST })
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 1_400)
  assert.equal(f.kinds().includes('verdict'), false, '仍在等本机的映射地址')
  await advance(t, 300)
  assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'other', rule: 2 })
})

test('名字解析不出时按对端端口判定：全是本机套接字，判同机（规则 5）', async t => {
  restartTimers(t)
  const f = harness(({ state }) => { state.isLocalPort = async port => port === 53000 })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_SAME }) // 两端映射地址相同，规则 3 分不出来
  c.onShellMessage(SID, { kind: 'peer-ports', ports: [{ port: 53000, protocol: 'udp' }, { port: 53000, protocol: 'tcp' }] })
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 200)
  assert.deepEqual(f.sent.filter(message => message.kind === 'resolved' || message.kind === 'verdict'), [
    { kind: 'resolved', id: 1, address: null },
    { kind: 'verdict', verdict: 'same', rule: 5 },
  ])
  assert.deepEqual(f.calls.filter(call => call[0] === 'isLocalPort'), [['isLocalPort', 53000, 'udp'], ['isLocalPort', 53000, 'tcp']])
  assert.ok(f.logs.some(log => log.event === 'screen_verdict' && log.fields.verdict === 'same' && log.fields.rule === 5))
})

test('名字解析不出时按对端端口判定：有端口不属于本机，判另一台电脑（规则 6），页面随后开始出画面', async t => {
  restartTimers(t)
  const f = harness(({ state }) => { state.isLocalPort = async port => port !== 40001 })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_SAME })
  c.onShellMessage(SID, { kind: 'connected' })
  // 对端端口在连接之后才报来：等它，最多约 1.5 秒
  await advance(t, 700)
  assert.equal(f.kinds().includes('verdict'), false)
  c.onShellMessage(SID, { kind: 'peer-ports', ports: [{ port: 53000, protocol: 'udp' }, { port: 40001, protocol: 'udp' }] })
  await drain()
  assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'other', rule: 6 })
  c.onShellMessage(SID, { kind: 'streaming', path: 'lan' })
  await drain()
  assert.deepEqual(f.posted.at(-1).payload, { state: 'streaming', path: 'lan' })
  assert.equal(c.summary().state, 'streaming')
})

test('端口表查不到时保持无法确认：判不出就不采集，最后按验证超时结束', async t => {
  restartTimers(t)
  const f = harness(({ state }) => { state.isLocalPort = async () => null })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_SAME })
  c.onShellMessage(SID, { kind: 'peer-ports', ports: [{ port: 53000, protocol: 'udp' }] })
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 300)
  assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'unknown', rule: 4 })
  await advance(t, 15_000)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'machine_check_unknown' })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
  assert.equal(c.summary(), null)
})

test('一个对端端口都没报来：等约 1.5 秒后交出无法确认，不去查端口表', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_SAME })
  c.onShellMessage(SID, { kind: 'connected' })
  c.onShellMessage(SID, { kind: 'peer-ports', ports: [] })
  await advance(t, 1_400)
  assert.equal(f.kinds().includes('verdict'), false)
  await advance(t, 300)
  assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'unknown', rule: 4 })
  assert.equal(f.count('isLocalPort'), 0)
})

test('两端公网出口不同时，内网类地址的候选不下发；其余原样下发', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_OTHER })
  const candidates = [own('192.168.1.5'), own('1.2.3.4', 'srflx', 2), own('abcd-1234.local', 'host', 3), own('100.64.0.1', 'host', 4), own('127.0.0.1', 'host', 5)]
  candidates.forEach((candidate, index) => c.onShellMessage(SID, { kind: 'candidate', candidate, sdp_mid: '0', sdp_mline_index: index }))
  await drain()
  // 内网类的三条先暂存（网页的映射地址已经到了但出口不同，暂存等的是出口「相同」的迹象），公网和回环立即下发
  const before = f.posted.filter(signal => signal.type === 'candidate')
  assert.deepEqual(before.map(signal => signal.payload), [
    { candidate: candidates[1], sdp_mid: '0', sdp_mline_index: 1 },
    { candidate: candidates[4], sdp_mid: '0', sdp_mline_index: 4 },
  ])
  await advance(t, 3_100)
  const forwarded = f.posted.filter(signal => signal.type === 'candidate')
  assert.deepEqual(forwarded.map(signal => signal.payload), [
    { candidate: candidates[1], sdp_mid: '0', sdp_mline_index: 1 },
    { candidate: candidates[4], sdp_mid: '0', sdp_mline_index: 4 },
  ], '暂存到期时出口已确定不同，内网类候选被丢弃')
  const filtered = f.logs.filter(log => log.event === 'screen_candidate_filtered')
  assert.deepEqual(filtered.map(log => log.fields.kind), ['private', 'mdns', 'shared'])
  // 出口不同这件事不影响连接邀约
  c.onShellMessage(SID, { kind: 'offer', sdp: 'v=0' })
  await drain()
  assert.deepEqual(f.posted.at(-1).payload, { sdp: 'v=0' })
})

test('两端公网出口相同时，暂存的内网候选在网页映射地址到达时立即下发', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  // Runtime 的内网候选先报（实际时序就是这样），此时还比不出两端出口
  c.onShellMessage(SID, { kind: 'candidate', candidate: own('192.168.1.5'), sdp_mid: null, sdp_mline_index: null })
  await drain()
  assert.equal(f.posted.some(signal => signal.type === 'candidate'), false, '暂存中，还没发')
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_SAME })
  await drain()
  assert.deepEqual(f.posted.at(-1), { id: SID, type: 'candidate', payload: { candidate: own('192.168.1.5'), sdp_mid: null, sdp_mline_index: null } })
  assert.equal(f.logs.some(log => log.event === 'screen_candidate_filtered'), false)
})

test('信息不全时（缺少任一端的映射地址），暂存到期后照常下发内网候选', async t => {
  for (const [ownMapped, webMapped] of [[null, WEB_MAPPED_OTHER], ['1.2.3.4', null], [null, null]]) {
    restartTimers(t)
    const f = harness()
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    if (ownMapped) c.onShellMessage(SID, { kind: 'mapped', address: ownMapped })
    if (webMapped) c.onShellMessage(SID, { kind: 'remote-candidate', candidate: webMapped })
    c.onShellMessage(SID, { kind: 'candidate', candidate: own('192.168.1.5'), sdp_mid: '0', sdp_mline_index: 0 })
    await drain()
    assert.equal(f.posted.some(signal => signal.type === 'candidate' && signal.payload.candidate === own('192.168.1.5')), false, '暂存中')
    await advance(t, 3_100)
    assert.deepEqual(f.posted.at(-1).payload, { candidate: own('192.168.1.5'), sdp_mid: '0', sdp_mline_index: 0 })
    assert.equal(f.logs.some(log => log.event === 'screen_candidate_filtered'), false)
  }
})

test('没有配置 STUN 时不暂存：内网候选直接下发', async t => {
  restartTimers(t)
  const f = harness()
  f.ports.stunUrls = []
  f.state.stunUrlsInRead = []
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'candidate', candidate: own('192.168.1.5'), sdp_mid: '0', sdp_mline_index: 0 })
  await drain()
  assert.deepEqual(f.posted.at(-1), { id: SID, type: 'candidate', payload: { candidate: own('192.168.1.5'), sdp_mid: '0', sdp_mline_index: 0 } })
  assert.equal(f.logs.some(log => log.event === 'screen_candidate_filtered'), false)
})

test('建连 30 秒内没有连上：先关窗口，再以 connect_failed 结束', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  await advance(t, 29_000)
  assert.equal(f.types().includes('end'), false)
  assert.notEqual(c.summary(), null)
  await advance(t, 1_200)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'connect_failed' })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
  assert.equal(c.summary(), null)
})

test('连接建立后 15 秒内没有开始出画面：以 machine_check_unknown 结束', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toVerifying(t, f, c)
  await advance(t, 14_500)
  assert.equal(f.types().includes('end'), false)
  await advance(t, 1_200)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'machine_check_unknown' })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
})

test('画面租约：传画面期间连续 30 秒没有续期就结束（renewal_timeout），有续期就继续', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toStreaming(t, f, c)
  await viewing(t, c, 20_000)
  assert.equal(c.summary().state, 'streaming')
  await advance(t, 29_000)
  assert.equal(c.summary().state, 'renewing')
  await advance(t, 1_200)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'renewal_timeout' })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
  assert.equal(c.summary(), null)
})

test('画面租约：心跳响应缺少画面字段、类型不对或编号不认识时，这一次不续期', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toStreaming(t, f, c)
  const notRenewals = [
    {}, { success: true }, null, 'ok',
    { screen_session: null }, { screen_session: { id: SID } }, { screen_session: { id: SID, state: 'endedx' } },
    { screen_session: { id: SID2, state: 'streaming' } }, { screen_session: { id: 7, state: 'streaming' } },
  ]
  // 传画面后 30 秒（700 毫秒处开始，租约到 30 700 毫秒）：前 25 秒里塞满不续期的心跳，都不会推迟到期
  for (let i = 0; i < 5; i++) {
    await advance(t, 5_000)
    for (const response of notRenewals) c.onHeartbeatScreen(response)
    assert.notEqual(c.summary(), null, `${(i + 1) * 5} 秒时不该结束`)
  }
  await advance(t, 5_200)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'renewal_timeout' }, '满 30 秒就按租约结束')
  assert.equal(c.summary(), null)
})

test('建连和验证阶段的心跳通知不给画面续期，也不打断会话', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toVerifying(t, f, c)
  for (let i = 0; i < 14; i++) { await advance(t, 1_000); c.onHeartbeatScreen(renewal()) }
  assert.equal(c.summary().state, 'connecting', 'streaming 通知不会把会话提前带进传画面')
  await advance(t, 1_200)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'machine_check_unknown' }, '不改变 15 秒的验证时限')
  assert.equal(f.posted.filter(signal => signal.type === 'end').length, 1)
})

test('心跳通知 ended：结束当前会话；编号不认识时忽略', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toStreaming(t, f, c)
  c.onHeartbeatScreen({ screen_session: { id: SID2, state: 'ended' } })
  assert.equal(c.summary().state, 'streaming')
  c.onHeartbeatScreen({ screen_session: { id: SID, state: 'ended' } })
  assert.equal(c.summary(), null)
  assert.deepEqual(f.events.slice(-1), ['close'])
  assert.equal(f.types().includes('end'), false, '服务器已结束的会话不再发 end')
})

test('心跳通知 pending：同一编号忽略；出现新编号时先关掉旧会话的窗口（不发 end）再开始新会话', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toStreaming(t, f, c)
  const postedBefore = f.posted.length
  c.onHeartbeatScreen(pending())
  assert.equal(f.posted.length, postedBefore, '当前会话的编号相同，忽略')
  c.onHeartbeatScreen(pending(SID2))
  assert.deepEqual(f.events.slice(-2), ['close', 'post:prepared'])
  assert.deepEqual(c.summary(), { sessionId: SID2, state: 'connecting', degraded: false, startedAt: null, secondsSinceRenewal: null })
  assert.equal(f.types().includes('end'), false)
  await drain()
  // 旧会话的窗口迟到的消息不再处理
  const sentBefore = f.sent.length
  c.onShellMessage(SID, { kind: 'offer', sdp: 'late' })
  await drain()
  assert.equal(f.sent.length, sentBefore)
  assert.equal(f.types().includes('offer'), false)
})

test('网页发来 end，或读到会话已结束：关窗口，不发 end', async t => {
  for (const read of [{ signals: [{ seq: 4, type: 'end', payload: { reason: 'closed' } }] }, { state: 'ended', endReason: 'viewer_closed' }]) {
    restartTimers(t)
    const f = harness()
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    const before = f.posted.length
    f.reads.push(read)
    await advance(t, 700)
    assert.equal(c.summary(), null)
    assert.equal(f.posted.length, before, 'Runtime 不为服务器或网页发起的结束再发 end')
    assert.equal(f.events.includes('close'), true)
  }
})

test('本机用户拒绝：出画面前是 runtime_rejected，出画面后按停止处理', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.reject(SID2)
  assert.notEqual(c.summary(), null, '编号不匹配时没有效果')
  c.reject(SID)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'runtime_rejected' })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
  assert.equal(c.summary(), null)
  c.reject(SID)
  assert.equal(f.posted.filter(signal => signal.type === 'end').length, 1)

  restartTimers(t)
  const g = harness()
  const d = new ScreenSessionController(g.ports)
  await toStreaming(t, g, d)
  d.reject(SID)
  assert.deepEqual(g.posted.at(-1).payload, { reason: 'runtime_stopped' })
  assert.equal(d.summary(), null)
})

test('本机用户停止：先关窗口，再发 runtime_stopped；可以重复调用', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  c.stop(SID)
  assert.equal(f.calls.length, 0, '没有会话时没有效果')
  await toStreaming(t, f, c)
  c.stop(SID)
  assert.equal(c.summary(), null)
  assert.deepEqual(f.posted.at(-1), { id: SID, type: 'end', payload: { reason: 'runtime_stopped' } })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
  c.stop(SID)
  c.stop(SID2)
  assert.equal(f.posted.filter(signal => signal.type === 'end').length, 1)
})

test('采集窗口的渲染进程异常退出：还没出画面按 connect_failed，已出画面按 runtime_stopped', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.windowCrashed(SID)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'connect_failed' })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])

  restartTimers(t)
  const g = harness()
  const d = new ScreenSessionController(g.ports)
  await toStreaming(t, g, d)
  d.windowCrashed(SID)
  assert.deepEqual(g.posted.at(-1).payload, { reason: 'runtime_stopped' })
  assert.deepEqual(g.events.slice(-2), ['close', 'post:end'])
  assert.equal(d.summary(), null)
})

test('采集页自己结束：原因映射成服务端接受的 Runtime 结束原因', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'closed', reason: 'machine_check_unknown' })
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'machine_check_unknown' })

  restartTimers(t)
  const g = harness()
  const d = new ScreenSessionController(g.ports)
  await toConnecting(t, g, d)
  d.onShellMessage(SID, { kind: 'closed', reason: 'ice-failed' })
  assert.deepEqual(g.posted.at(-1).payload, { reason: 'connect_failed' }, '不是七个合法原因之一时，出画面前按 connect_failed')
  assert.equal(d.summary(), null)

  restartTimers(t)
  const h = harness()
  const e = new ScreenSessionController(h.ports)
  await toStreaming(t, h, e)
  e.onShellMessage(SID, { kind: 'closed' })
  assert.deepEqual(h.posted.at(-1).payload, { reason: 'runtime_stopped' }, '出画面后按 runtime_stopped')
  assert.deepEqual(h.events.slice(-2), ['close', 'post:end'])
})

test('dispose：关窗口、清定时器、发一次 runtime_stopped，不等网络请求，之后不再接受会话', async t => {
  restartTimers(t)
  let finishPost
  const f = harness()
  // 每条信令都发得出去，但请求一直不返回
  f.ports.signaling.postSignal = (id, type, payload) => {
    f.events.push(`post:${type}`); f.posted.push({ id, type, payload })
    return new Promise(resolve => { finishPost = resolve })
  }
  const c = new ScreenSessionController(f.ports)
  await toStreaming(t, f, c)
  c.dispose()
  assert.equal(c.summary(), null)
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'runtime_stopped' })
  const before = f.calls.length
  c.onHeartbeatScreen(pending(SID2))
  c.onShellMessage(SID, { kind: 'offer', sdp: 'late' })
  c.stop(SID)
  finishPost({ ok: true })
  await advance(t, 60_000)
  assert.equal(f.calls.length, before)
  // 没有会话时 dispose 什么都不发
  const g = harness()
  new ScreenSessionController(g.ports).dispose()
  assert.equal(g.calls.length, 0)
})

// 让某一种信令失败、其余照常发出的传输层：照常记录发出的每一条，断言才有依据
function failingSignal(f, failing, outcome) {
  const post = f.ports.signaling.postSignal
  f.ports.signaling.postSignal = async (id, type, payload) => {
    const recorded = await post(id, type, payload)
    return type === failing ? { ok: false, attempts: 20, ...outcome } : recorded
  }
}

test('发信令时发现服务器已结束这个会话：同样结束本地会话，关窗口', async t => {
  for (const kind of ['ended', 'not_found']) {
    restartTimers(t)
    const f = harness()
    failingSignal(f, 'offer', { kind })
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    c.onShellMessage(SID, { kind: 'offer', sdp: 'v=0' })
    await drain()
    assert.equal(c.summary(), null, kind)
    assert.deepEqual(f.events.slice(-1), ['close'])
    assert.deepEqual(f.types(), ['prepared', 'offer'], '服务器已经结束，不再发 end')
  }
})

test('发信令被拒绝或重试后放弃：不结束会话，会话仍由自己的时限收尾', async t => {
  for (const kind of ['rejected', 'busy']) {
    restartTimers(t)
    const f = harness()
    failingSignal(f, 'offer', { kind })
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    c.onShellMessage(SID, { kind: 'offer', sdp: 'v=0' })
    await drain()
    assert.notEqual(c.summary(), null, kind)
    await advance(t, 30_000)
    assert.deepEqual(f.posted.at(-1).payload, { reason: 'connect_failed' })
    assert.equal(f.events.includes('close'), true)
  }
})

test('资源不足驱动的降低画质、恢复和停止（设计 4.5）', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toStreaming(t, f, c)
  // 任务运行中，电脑 CPU 占用 95%
  f.state.taskRunning = true
  f.state.cpu = 95
  await viewing(t, c, 5_000)
  assert.equal(f.kinds().includes('degrade'), false, '第一次采样只计数')
  await viewing(t, c, 5_000)
  assert.deepEqual(f.sent.slice(-1), [{ kind: 'degrade' }])
  assert.deepEqual(f.posted.at(-1), { id: SID, type: 'status', payload: { state: 'degraded' } })
  assert.equal(c.summary().degraded, true)
  assert.equal(c.summary().state, 'streaming', '降低画质不中断观看')
  // 任务还在跑但资源不再紧张：保持降低画质，不恢复
  f.state.cpu = 30
  await viewing(t, c, 10_000)
  assert.equal(f.kinds().includes('restore'), false)
  assert.equal(c.summary().degraded, true)
  // 任务结束：恢复
  f.state.taskRunning = false
  await viewing(t, c, 5_000)
  assert.deepEqual(f.sent.slice(-1), [{ kind: 'restore' }])
  assert.deepEqual(f.posted.at(-1).payload, { state: 'restored' })
  assert.equal(c.summary().degraded, false)
  assert.equal(c.summary().state, 'streaming')
  // 任务再次开始，降低后仍然资源不足：结束观看，原因 resource_busy
  f.state.taskRunning = true
  f.state.cpu = 99
  await advance(t, 20_000)
  assert.equal(f.kinds().filter(kind => kind === 'degrade').length, 2)
  assert.deepEqual(f.posted.at(-1).payload, { reason: 'resource_busy' })
  assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
  assert.equal(c.summary(), null)
})

test('没有任务在跑时，电脑再忙也不降低画质、不结束观看；CPU 读数缺失时只看编码器的信号', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toStreaming(t, f, c)
  f.state.cpu = 100
  await viewing(t, c, 30_000)
  assert.deepEqual(f.kinds().filter(kind => kind === 'degrade' || kind === 'restore'), [])
  assert.equal(f.types().includes('end'), false)
  // 任务运行中，CPU 读数不可用：编码器没报受限就不算资源不足，报了才算
  f.state.taskRunning = true
  f.state.cpu = null
  await viewing(t, c, 10_000)
  assert.equal(f.kinds().includes('degrade'), false)
  c.onShellMessage(SID, { kind: 'cpu-limited', limited: true })
  await viewing(t, c, 5_000)
  assert.equal(f.kinds().includes('degrade'), false)
  await viewing(t, c, 5_000)
  assert.deepEqual(f.sent.slice(-1), [{ kind: 'degrade' }])
  // 编码器不再报受限：保持降低画质，直到任务结束
  c.onShellMessage(SID, { kind: 'cpu-limited', limited: false })
  await viewing(t, c, 10_000)
  assert.equal(f.kinds().includes('restore'), false)
  f.state.taskRunning = false
  await viewing(t, c, 5_000)
  assert.deepEqual(f.sent.slice(-1), [{ kind: 'restore' }])
})

test('summary 在各状态的形状；没有会话时为 null', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  assert.equal(c.summary(), null)
  c.onHeartbeatScreen(pending())
  assert.equal(c.summary().state, 'connecting')
  await drain()
  const shape = summary => Object.keys(summary).sort().join(',')
  f.reads.push({ signals: [{ seq: 1, type: 'start', payload: {} }] })
  await advance(t, 700)
  assert.equal(c.summary().state, 'connecting', 'connecting 阶段')
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST })
  c.onShellMessage(SID, { kind: 'connected' })
  await drain()
  assert.equal(c.summary().state, 'connecting', 'verifying 阶段')
  assert.equal(c.summary().startedAt, null)
  assert.equal(c.summary().secondsSinceRenewal, null)
  assert.equal(shape(c.summary()), 'degraded,secondsSinceRenewal,sessionId,startedAt,state')
  const startedAt = Date.now()
  c.onShellMessage(SID, { kind: 'streaming', path: 'p2p' })
  await drain()
  assert.equal(c.summary().startedAt, startedAt)
  f.state.interactive = false
  await advance(t, 11_000)
  assert.equal(c.summary().state, 'renewing', '收不到服务器确认的提示优先于锁屏提示')
  assert.ok(c.summary().secondsSinceRenewal >= 11)
  c.onHeartbeatScreen(renewal())
  assert.equal(c.summary().state, 'locked')
  assert.equal(c.summary().secondsSinceRenewal, 0)
  assert.equal(shape(c.summary()), 'degraded,secondsSinceRenewal,sessionId,startedAt,state')
  c.stop(SID)
  assert.equal(c.summary(), null)
})

test('类型不认识的信令、载荷不是对象的信令、重复的 start 与 connected 都忽略，不算错误', async t => {
  restartTimers(t)
  const f = harness()
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  const sentBefore = f.sent.length
  f.reads.push({ signals: [
    { seq: 4, type: 'restart', payload: {} },
    { seq: 5, type: 'answer', payload: 'v=0' },
    { seq: 6, type: 'answer', payload: null },
    { seq: 7, type: 'candidate', payload: {} },
    { seq: 8, type: 'candidate', payload: { candidate: 7 } },
    { seq: 9, type: 'start', payload: {} },
  ] })
  await advance(t, 700)
  assert.equal(f.sent.length, sentBefore)
  assert.equal(f.count('open'), 1, 'start 只在 prepared 状态有效')
  const postedBefore = f.posted.length
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST })
  c.onShellMessage(SID, { kind: 'connected' })
  await drain()
  assert.equal(f.posted.length - postedBefore, 1, '只多出一条 connected 的 status')
  assert.equal(f.kinds().includes('resolved'), false, '网页的明文候选不需要解析')
  c.onShellMessage(SID, { kind: 'connected' })
  c.onShellMessage(SID, { kind: 'unknown-kind' })
  c.onShellMessage(SID, { kind: 'offer' })
  c.onShellMessage(SID, { kind: 'offer', sdp: 7 })
  c.onShellMessage(SID, { kind: 'candidate', sdp_mid: '0' })
  c.onShellMessage(SID, { kind: 'mapped' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: 5 })
  c.onShellMessage(SID, { kind: 'cpu-limited' })
  c.onShellMessage(SID, null)
  c.onShellMessage(SID, 'connected')
  c.onShellMessage(7, { kind: 'offer', sdp: 'x' })
  await drain()
  assert.equal(f.posted.length - postedBefore, 1, '重复的 connected 与缺字段的消息都不产生信令')
  await advance(t, 1_800)
  assert.deepEqual(f.sent.slice(-1), [{ kind: 'verdict', verdict: 'other', rule: 2 }])
})

test('建窗时带上的 STUN 列表取自会话响应；响应里没有时用注入的那份', async t => {
  restartTimers(t)
  // 响应里的列表是 ScreenSignalingClient 解析之后的形状（只含 STUN 地址，过滤在信令客户端的测试里覆盖）
  const f = harness(({ state }) => { state.stunUrlsInRead = ['stun:from-server:3478'] })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  assert.deepEqual(f.calls.find(call => call[0] === 'open')?.[1].stunUrls, ['stun:from-server:3478'], '用服务端给的列表，不用注入的缺省值')

  restartTimers(t)
  const g = harness()
  const d = new ScreenSessionController(g.ports)
  d.onHeartbeatScreen(pending())
  await drain()
  g.reads.push({ signals: [{ seq: 1, type: 'start', payload: {} }], stunUrls: null })
  await advance(t, 700)
  assert.deepEqual(g.calls.find(call => call[0] === 'open')?.[1].stunUrls, STUN)
})

test('日志只含会话编号、状态、判定、原因、计数和耗时，不含地址、名字、候选、SDP 和 STUN 地址', async t => {
  restartTimers(t)
  const f = harness(({ state }) => {
    state.resolve = async () => ({ ok: true, address: '192.168.1.5' })
    state.isLocalPort = async () => true
  })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_OTHER })
  c.onShellMessage(SID, { kind: 'candidate', candidate: own('192.168.1.5'), sdp_mid: '0', sdp_mline_index: 0 })
  c.onShellMessage(SID, { kind: 'offer', sdp: 'v=0\r\no=- 4611731400430051336 2 IN IP4 127.0.0.1\r\n' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 300)
  c.onShellMessage(SID, { kind: 'streaming', path: 'lan' })
  f.state.taskRunning = true
  f.state.cpu = 95
  await viewing(t, c, 11_000)
  c.stop(SID)
  assert.ok(f.logs.length > 5)
  assert.equal(f.logs.some(log => log.event === 'screen_candidate_filtered'), true)
  const text = JSON.stringify(f.logs)
  for (const secret of ['192.168.1.5', '1.2.3.4', '5.6.7.8', 'abcd-1234', '.local', 'candidate:', 'v=0', '127.0.0.1', 'stun:', 'sdp']) {
    assert.equal(text.includes(secret), false, `日志里出现了 ${secret}`)
  }
  for (const log of f.logs) {
    for (const [key, value] of Object.entries(log.fields)) {
      assert.equal(['string', 'number', 'boolean'].includes(typeof value) || value === null, true, `${log.event}.${key} 的类型`)
    }
  }
})

test('会话结束后所有定时器都被清掉：再推进时间不会产生任何新的调用', async t => {
  const endings = [
    ['本机停止', async (f, c) => c.stop(SID)],
    ['网页结束', async (f, c) => c.onHeartbeatScreen({ screen_session: { id: SID, state: 'ended' } })],
    ['验证超时', async (f, c) => { await advance(t, 15_500) }],
  ]
  for (const [name, ending] of endings) {
    restartTimers(t)
    const f = harness()
    const c = new ScreenSessionController(f.ports)
    await toVerifying(t, f, c)
    if (name !== '验证超时') c.onShellMessage(SID, { kind: 'streaming', path: 'lan' })
    await ending(f, c)
    await drain()
    assert.equal(c.summary(), null, name)
    const before = f.calls.length
    await advance(t, 90_000)
    assert.equal(f.calls.length, before, `${name}：结束后仍有调用`)
  }
})

test('记录异常时只记错误的类型名和出错位置：错误信息里会带地址或名字（例如解析器的报错），不写进日志', async t => {
  restartTimers(t)
  const secret = 'getaddrinfo ENOTFOUND abcd-1234.local at 192.168.1.5'
  const leaky = () => { throw new Error(secret) }
  const f = harness(({ ports, state }) => {
    ports.shell.send = leaky
    state.resolve = async () => { throw new Error(secret) }
    ports.isLocalPort = async () => { throw new Error(secret) }
  })
  const c = new ScreenSessionController(f.ports)
  await toConnecting(t, f, c)
  c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
  c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
  c.onShellMessage(SID, { kind: 'peer-ports', ports: [{ port: 53000, protocol: 'udp' }] })
  c.onShellMessage(SID, { kind: 'connected' })
  await advance(t, 1_800)
  // 把解析结果和判定交给采集页、查端口表，三处各记一条；名字解析本身抛异常不算错误，不记
  const errors = f.logs.filter(log => log.event === 'screen_error')
  assert.deepEqual(errors.map(log => log.fields.where), ['send:resolved', 'isLocalPort', 'send:verdict'])
  assert.ok(errors.every(log => log.fields.error === 'Error' && log.fields.session === SID))
  const text = JSON.stringify(f.logs)
  for (const part of ['abcd-1234', '192.168.1.5', 'getaddrinfo', 'ENOTFOUND']) {
    assert.equal(text.includes(part), false, `日志里出现了 ${part}`)
  }
})

// 用真实定时器在子进程里跑：会话在传画面、轮询和各路定时器都还排着的时候脚本结束，
// 进程必须自己退出。任何一个定时器漏了 unref，进程都会被它拖住直到超时。
test('定时器不阻止 Host 进程退出', () => {
  const entry = new URL('../dist/screen/session.js', import.meta.url).href
  const script = `
    import { ScreenSessionController } from ${JSON.stringify(entry)}
    let reads = 0
    const signaling = {
      async readSession(id, after) {
        reads++
        return { session: { id, state: 'connecting', endReason: null, stunUrls: ['stun:s:3478'] }, signals: after === 0 ? [{ seq: 1, type: 'start', payload: {} }] : [] }
      },
      async postSignal() { return { ok: true, attempts: 1 } },
      abandon() {},
    }
    const ports = {
      signaling,
      shell: { open() {}, send() {}, close() {} },
      network: { localAddresses: () => ['192.168.1.5'], resolveHost: () => new Promise(() => {}) },
      isLocalPort: () => new Promise(() => {}),
      desktopInteractive: async () => true, cpuBusyPercent: () => null, taskRunning: () => false,
      stunUrls: [], log() {},
    }
    const c = new ScreenSessionController(ports)
    c.onHeartbeatScreen({ screen_session: { id: 'sid', state: 'pending' } })
    await new Promise(resolve => setImmediate(resolve))
    c.onShellMessage('sid', { kind: 'remote-candidate', candidate: 'candidate:1 1 udp 1 abcd-1.local 50000 typ host' })
    c.onShellMessage('sid', { kind: 'connected' })
    c.onShellMessage('sid', { kind: 'streaming', path: 'lan' })
    if (reads !== 1 || c.summary()?.state !== 'streaming') process.exitCode = 3
  `
  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', script], { timeout: 15_000, encoding: 'utf8' })
  assert.equal(child.signal, null, `子进程没有自行退出：${child.stderr}`)
  assert.equal(child.status, 0, child.stderr)
})

test('隔离：注入的端口对象抛异常时，对外方法不抛出，会话仍能正常结束', async t => {
  const boom = () => { throw new Error('boom') }

  await t.test('日志输出抛异常不影响整条流程', async t => {
    restartTimers(t)
    const f = harness(({ ports }) => { ports.log = boom })
    const c = new ScreenSessionController(f.ports)
    await toStreaming(t, f, c)
    assert.equal(c.summary().state, 'streaming')
    c.stop(SID)
    assert.equal(c.summary(), null)
    assert.deepEqual(f.posted.at(-1).payload, { reason: 'runtime_stopped' })
  })

  await t.test('建窗失败按 connect_failed 结束', async t => {
    restartTimers(t)
    const f = harness(({ ports }) => { ports.shell.open = boom })
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    assert.deepEqual(f.posted.at(-1).payload, { reason: 'connect_failed' })
    assert.deepEqual(f.events.slice(-2), ['close', 'post:end'])
    assert.equal(c.summary(), null)
  })

  await t.test('转交给采集页失败：判定交不出去，会话按验证时限结束', async t => {
    restartTimers(t)
    const f = harness(({ ports }) => { ports.shell.send = boom })
    const c = new ScreenSessionController(f.ports)
    await toVerifying(t, f, c)
    assert.equal(c.summary().state, 'connecting')
    await advance(t, 15_500)
    assert.deepEqual(f.posted.at(-1).payload, { reason: 'machine_check_unknown' })
  })

  await t.test('关窗口失败不影响结束会话和发 end', async t => {
    restartTimers(t)
    const f = harness(({ ports, signaling }) => { ports.shell.close = boom; signaling.abandon = boom })
    const c = new ScreenSessionController(f.ports)
    await toStreaming(t, f, c)
    c.stop(SID)
    assert.equal(c.summary(), null)
    assert.deepEqual(f.posted.at(-1).payload, { reason: 'runtime_stopped' })
  })

  await t.test('读取会话抛异常：这一次读取作废，轮询继续', async t => {
    restartTimers(t)
    let attempts = 0
    const f = harness(({ signaling }) => {
      const read = signaling.readSession
      signaling.readSession = async (id, after) => { if (++attempts === 1) throw new Error('offline'); return read(id, after) }
    })
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    assert.equal(attempts, 2, '失败之后隔 500 毫秒再取')
    assert.equal(f.count('open'), 1, '读取恢复后仍能收到 start')
  })

  await t.test('发信令抛异常：对外方法不抛出，本地会话照常结束', async t => {
    restartTimers(t)
    const f = harness(({ signaling }) => {
      signaling.postSignal = async () => { throw new Error('offline') }
    })
    const c = new ScreenSessionController(f.ports)
    c.onHeartbeatScreen(pending())
    await drain()
    c.stop(SID)
    assert.equal(c.summary(), null)
    assert.equal(f.events.includes('close'), true)
  })

  await t.test('名字解析抛异常：按解析不出处理，判定照常给出', async t => {
    restartTimers(t)
    const f = harness(({ state }) => { state.resolve = async () => { throw new Error('dns') } })
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
    c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
    c.onShellMessage(SID, { kind: 'connected' })
    await advance(t, 1_800)
    assert.deepEqual(f.sent.filter(message => message.kind === 'resolved' || message.kind === 'verdict'), [
      { kind: 'resolved', id: 1, address: null },
      { kind: 'verdict', verdict: 'unknown', rule: 4 },
    ])
  })

  await t.test('查端口表抛异常：按查不到处理，不放行', async t => {
    restartTimers(t)
    const f = harness(({ ports }) => { ports.isLocalPort = async () => { throw new Error('netstat') } })
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    c.onShellMessage(SID, { kind: 'mapped', address: '1.2.3.4' })
    c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MDNS })
    c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_MAPPED_SAME })
    c.onShellMessage(SID, { kind: 'peer-ports', ports: [{ port: 53000, protocol: 'udp' }] })
    c.onShellMessage(SID, { kind: 'connected' })
    await advance(t, 300)
    assert.deepEqual(f.sent.at(-1), { kind: 'verdict', verdict: 'unknown', rule: 4 })
  })

  await t.test('取不到本机网卡地址：不下结论，会话按验证时限结束', async t => {
    restartTimers(t)
    const f = harness(({ ports }) => { ports.network.localAddresses = boom })
    const c = new ScreenSessionController(f.ports)
    await toConnecting(t, f, c)
    c.onShellMessage(SID, { kind: 'remote-candidate', candidate: WEB_HOST })
    c.onShellMessage(SID, { kind: 'connected' })
    await advance(t, 15_500)
    assert.equal(f.kinds().includes('verdict'), false)
    assert.deepEqual(f.posted.at(-1).payload, { reason: 'machine_check_unknown' })
  })

  await t.test('锁屏检测出错按没有变化处理', async t => {
    restartTimers(t)
    const f = harness(({ ports }) => { ports.desktopInteractive = async () => { throw new Error('probe') } })
    const c = new ScreenSessionController(f.ports)
    await toStreaming(t, f, c)
    await advance(t, 6_000)
    assert.equal(f.types().filter(type => type === 'status').length, 2)
    assert.equal(c.summary().state, 'streaming')
  })

  await t.test('读任务状态或 CPU 占用出错：跳过这一次采样，观看继续', async t => {
    for (const broken of ['taskRunning', 'cpuBusyPercent']) {
      restartTimers(t)
      const f = harness(({ ports }) => { ports[broken] = boom })
      const c = new ScreenSessionController(f.ports)
      await toStreaming(t, f, c)
      await viewing(t, c, 21_000)
      assert.deepEqual(f.kinds().filter(kind => kind === 'degrade' || kind === 'restore'), [], broken)
      assert.equal(f.types().includes('end'), false)
      assert.equal(c.summary().state, 'streaming')
    }
  })
})
