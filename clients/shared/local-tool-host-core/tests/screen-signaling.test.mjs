import assert from 'node:assert/strict'
import { test } from 'node:test'
import { SIGNAL_MAX_ATTEMPTS } from '../dist/screen/protocol.js'
import { ScreenSignalingClient, ScreenTransportError } from '../dist/screen/signaling.js'

// 设备侧的两个画面接口（设计 5.3）。HTTP 走注入的传输层，这里用内存里的假实现。
// 最重要的两条：Runtime 发往网页的信令严格按调用顺序发；繁忙和网络错误重试，服务器已结束会话不重试。

const SID = '11111111-1111-4111-8111-111111111111'
const SESSION_PATH = '/api/local-tools/runtime/screen/session'
const SIGNAL_PATH = '/api/local-tools/runtime/screen/signal'

async function drain(rounds = 6) { for (let i = 0; i < rounds; i++) await new Promise(resolve => setImmediate(resolve)) }
const error = kind => new ScreenTransportError(kind)

test('按调用顺序逐条发送：上一条请求没返回之前，后一条不会发出', async () => {
  const inFlight = []
  const releases = []
  const transport = { post(path, body) { inFlight.push(body.type); return new Promise(resolve => releases.push(resolve)) } }
  const client = new ScreenSignalingClient(transport)
  const results = [
    client.postSignal(SID, 'prepared', {}),
    client.postSignal(SID, 'offer', { sdp: 'v=0' }),
    client.postSignal(SID, 'candidate', { candidate: 'c1' }),
    client.postSignal(SID, 'candidate', { candidate: 'c2' }),
  ]
  await drain()
  assert.deepEqual(inFlight, ['prepared'], '并发调用时也只发第一条')
  releases.shift()({ success: true, seq: 1 }); await drain()
  assert.deepEqual(inFlight, ['prepared', 'offer'])
  releases.shift()({ success: true, seq: 2 }); await drain()
  assert.deepEqual(inFlight, ['prepared', 'offer', 'candidate'])
  releases.shift()({ success: true, seq: 3 }); await drain()
  assert.deepEqual(inFlight, ['prepared', 'offer', 'candidate', 'candidate'], '第三条返回之后才发第四条')
  releases.shift()({ success: true, seq: 4 }); await drain()
  assert.deepEqual((await Promise.all(results)).map(result => result.ok), [true, true, true, true])
})

test('请求体是 {session_id, type, payload}，地址是信令接口', async () => {
  const calls = []
  const client = new ScreenSignalingClient({ async post(path, body) { calls.push([path, body]); return { success: true, seq: 3 } } })
  const result = await client.postSignal(SID, 'status', { state: 'streaming', path: 'lan' })
  assert.deepEqual(calls, [[SIGNAL_PATH, { session_id: SID, type: 'status', payload: { state: 'streaming', path: 'lan' } }]])
  assert.deepEqual(result, { ok: true, attempts: 1 })
})

test('繁忙：隔 500 毫秒重试，达到上限后放弃并如实报告', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let attempts = 0
  const client = new ScreenSignalingClient({ async post() { attempts++; throw error('busy') } })
  const result = client.postSignal(SID, 'offer', { sdp: 'v=0' })
  await drain()
  assert.equal(attempts, 1)
  await t.mock.timers.tick(499); await drain()
  assert.equal(attempts, 1, '间隔不到不重试')
  for (let i = 1; i < SIGNAL_MAX_ATTEMPTS; i++) { await t.mock.timers.tick(501); await drain() }
  assert.equal(attempts, SIGNAL_MAX_ATTEMPTS)
  assert.deepEqual(await result, { ok: false, kind: 'busy', attempts: SIGNAL_MAX_ATTEMPTS })
})

test('网络错误：隔 500 毫秒重试，恢复后成功，并且只算一条信令', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const sent = []
  let failures = 2
  const client = new ScreenSignalingClient({
    async post(_path, body) { sent.push(body.type); if (failures-- > 0) throw error('network'); return { success: true, seq: 1 } },
  })
  const result = client.postSignal(SID, 'prepared', {})
  await drain()
  await t.mock.timers.tick(501); await drain()
  await t.mock.timers.tick(501); await drain()
  assert.deepEqual(await result, { ok: true, attempts: 3 })
  assert.deepEqual(sent, ['prepared', 'prepared', 'prepared'])
})

test('服务器已结束这个会话（ended、not_found）：不重试，直接报告', async () => {
  for (const kind of ['ended', 'not_found']) {
    let attempts = 0
    const client = new ScreenSignalingClient({ async post() { attempts++; throw error(kind) } })
    assert.deepEqual(await client.postSignal(SID, 'candidate', { candidate: 'c1' }), { ok: false, kind, attempts: 1 }, kind)
    assert.equal(attempts, 1)
  }
})

test('被拒绝（其余 4xx/5xx，以及不是 ScreenTransportError 的异常）：不重试，报告失败', async () => {
  for (const thrown of [error('rejected'), new TypeError('adapter bug')]) {
    let attempts = 0
    const client = new ScreenSignalingClient({ async post() { attempts++; throw thrown } })
    assert.deepEqual(await client.postSignal(SID, 'status', { state: 'locked' }), { ok: false, kind: 'rejected', attempts: 1 })
    assert.equal(attempts, 1)
  }
})

test('放弃（abandon）：排队的不发，正在等重试的不再重试，之后发的照常发', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const sent = []
  let failFirst = true
  const client = new ScreenSignalingClient({
    async post(_path, body) { sent.push(body.type); if (failFirst) { failFirst = false; throw error('network') } return { success: true } },
  })
  const retrying = client.postSignal(SID, 'offer', { sdp: 'v=0' }) // 第一次失败，正在等重试
  const queued = client.postSignal(SID, 'candidate', { candidate: 'c1' }) // 排在它后面
  await drain()
  assert.deepEqual(sent, ['offer'])
  client.abandon()
  const after = client.postSignal(SID, 'end', { reason: 'runtime_stopped' })
  await t.mock.timers.tick(5_000); await drain()
  assert.deepEqual(sent, ['offer', 'end'])
  assert.deepEqual(await retrying, { ok: false, kind: 'abandoned', attempts: 1 })
  assert.deepEqual(await queued, { ok: false, kind: 'abandoned', attempts: 0 })
  assert.deepEqual(await after, { ok: true, attempts: 1 })
})

test('读会话：请求带 {session_id, after_seq}，只返回编号大于游标的信令', async () => {
  const calls = []
  const body = {
    session: { id: SID, state: 'connecting', end_reason: null, stun_urls: ['stun:stun.example.test:3478'] },
    signals: [
      { seq: 3, type: 'candidate', payload: { candidate: 'c3' } },
      { seq: 4, type: 'answer', payload: { sdp: 'v=0' } },
    ],
  }
  const client = new ScreenSignalingClient({ async post(path, request) { calls.push([path, request]); return body } })
  assert.deepEqual(await client.readSession(SID, 2), {
    session: { id: SID, state: 'connecting', endReason: null, stunUrls: ['stun:stun.example.test:3478'] },
    signals: [{ seq: 3, dir: null, type: 'candidate', payload: { candidate: 'c3' } }, { seq: 4, dir: null, type: 'answer', payload: { sdp: 'v=0' } }],
  })
  assert.deepEqual(calls, [[SESSION_PATH, { session_id: SID, after_seq: 2 }]])
})

test('读会话：结束的会话带结束原因；信令里带方向时原样保留', async () => {
  const client = new ScreenSignalingClient({
    async post() {
      return { session: { id: SID, state: 'ended', end_reason: 'viewer_closed' }, signals: [{ seq: 9, dir: 'web', type: 'end', payload: { reason: 'closed' } }] }
    },
  })
  const read = await client.readSession(SID, 0)
  assert.deepEqual(read.session, { id: SID, state: 'ended', endReason: 'viewer_closed', stunUrls: null })
  assert.deepEqual(read.signals, [{ seq: 9, dir: 'web', type: 'end', payload: { reason: 'closed' } }])
})

test('读会话：只保留下发来的 STUN 地址（不区分大小写），turn 地址和不是字符串的条目都丢弃', async () => {
  const client = new ScreenSignalingClient({
    async post() {
      return { session: { id: SID, state: 'pending', end_reason: null, stun_urls: ['stun:a.test:3478', 'turn:b.test:3478?transport=udp', 'STUNS:c.test:5348', 7, null] }, signals: [] }
    },
  })
  assert.deepEqual((await client.readSession(SID, 0)).session.stunUrls, ['stun:a.test:3478', 'STUNS:c.test:5348'])
})

test('读会话：响应形状不对按这一次读取失败处理，返回 null，不抛异常', async () => {
  const bad = [
    undefined, null, 'ok', 7, [],
    {},
    { session: null, signals: [] },
    { session: 'abc', signals: [] },
    { session: { id: SID }, signals: [] },
    { session: { id: SID, state: 'future-state' }, signals: [] },
    { session: { id: SID, state: 'connecting' } },
    { session: { id: SID, state: 'connecting' }, signals: 'x' },
    { session: { id: 5, state: 'connecting' }, signals: [] },
  ]
  for (const response of bad) {
    const client = new ScreenSignalingClient({ async post() { return response } })
    assert.equal(await client.readSession(SID, 0), null, JSON.stringify(response) ?? String(response))
  }
  const failing = new ScreenSignalingClient({ async post() { throw error('busy') } })
  assert.equal(await failing.readSession(SID, 0), null, '繁忙、网络错误都按这一次读取失败处理')
})

test('读会话：结束原因和 STUN 列表只是附带信息，类型不对时按没有处理，不让整次读取失败', async () => {
  const client = new ScreenSignalingClient({
    async post() { return { session: { id: SID, state: 'ended', end_reason: 5, stun_urls: 'stun:a.test:3478' }, signals: [] } },
  })
  assert.deepEqual(await client.readSession(SID, 0), { session: { id: SID, state: 'ended', endReason: null, stunUrls: null }, signals: [] })
})

test('读会话：形状不对的信令条目丢掉，不拖垮这一次读取', async () => {
  const client = new ScreenSignalingClient({
    async post() {
      return {
        session: { id: SID, state: 'connecting', end_reason: null },
        signals: [{ seq: '4', type: 'answer', payload: {} }, { seq: 5, type: 7, payload: {} }, { type: 'answer', payload: {} }, null,
          { seq: 6, type: 'start', payload: {} }],
      }
    },
  })
  const read = await client.readSession(SID, 0)
  assert.deepEqual(read.signals, [{ seq: 6, dir: null, type: 'start', payload: {} }])
  assert.deepEqual((await new ScreenSignalingClient({ async post() { return read } }).readSession(SID, 6)).signals, [])
})
