import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { once } from 'node:events'
import http from 'node:http'
import { test } from 'node:test'
import { SIGNAL_RETRY_DELAY_MS } from '../dist/screen/protocol.js'
import { ScreenSignalingClient, ScreenTransportError } from '../dist/screen/signaling.js'

// 设备侧两个画面接口的真实 HTTP 级测试。screen-signaling.test.mjs 用的是内存里的传输层；这里用 node:http
// 起一个假的服务端，按服务端的应答形状答复（src/local_tools/screen_stream.py、src/local_tools/api.py）：
// 错误体是 {"detail": {"error", "code", "debug"}}；读取会话时信令条目不带 dir，成功时带 success: true。
// 时间用真实时钟，所以只断言下界和顺序。

const SID = '11111111-1111-4111-8111-111111111111'
const SESSION_PATH = '/api/local-tools/runtime/screen/session'
const SIGNAL_PATH = '/api/local-tools/runtime/screen/signal'

const errorBody = (code, error) => ({ detail: { error, code, debug: '' } })
const BUSY = errorBody('SCREEN_STREAM_BUSY', '实时画面暂时繁忙，请稍后重试')
const ENDED = errorBody('SCREEN_SESSION_ENDED', '会话已结束')
const NOT_FOUND = errorBody('SCREEN_SESSION_NOT_FOUND', '会话不存在或无权访问')
const LIMIT = errorBody('SCREEN_SIGNAL_LIMIT', '信令条数超过上限')
const DISABLED = errorBody('SCREEN_STREAM_DISABLED', '实时画面功能未启用')

/** 传输适配层的最小版本：Host 接入时用现有的 ApiClient 实现这一层，分类照 signaling.ts 的头注释 */
function httpTransport(baseUrl) {
  return {
    async post(path, body) {
      let response
      try {
        response = await fetch(baseUrl + path, {
          method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
        })
      } catch {
        throw new ScreenTransportError('network')
      }
      if (response.ok) return await response.json()
      const detail = await response.json().then(value => value?.detail, () => null)
      const code = typeof detail?.code === 'string' ? detail.code : ''
      if (response.status === 503 && code === 'SCREEN_STREAM_BUSY') throw new ScreenTransportError('busy')
      if (response.status === 409 && code === 'SCREEN_SESSION_ENDED') throw new ScreenTransportError('ended')
      if (response.status === 404) throw new ScreenTransportError('not_found')
      throw new ScreenTransportError('rejected')
    },
  }
}

async function readJson(request) {
  const chunks = []
  for await (const chunk of request) chunks.push(chunk)
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

/**
 * 假服务端。reply(path, body, index) 给出第 index 次请求的应答：{ status, body }；
 * holdMs 表示先扣住请求这么久再应答；destroy 为 true 表示直接断开连接。
 * requests 按到达顺序记录 { path, body, at }；maxInFlight() 是同时进行的请求数的峰值。
 */
async function startServer(t, reply, options) {
  const requests = []
  let inFlight = 0
  let peak = 0
  const server = http.createServer(async (request, response) => {
    try {
      const body = await readJson(request)
      requests.push({ path: request.url, body, at: performance.now() })
      inFlight++
      peak = Math.max(peak, inFlight)
      try {
        const answer = await reply(request.url, body, requests.length - 1)
        if (answer.destroy) {
          request.socket.destroy()
          return
        }
        if (answer.holdMs) await new Promise(resolve => setTimeout(resolve, answer.holdMs))
        response.writeHead(answer.status ?? 200, { 'content-type': 'application/json' })
        response.end(JSON.stringify(answer.body ?? { success: true, seq: requests.length }))
      } finally {
        inFlight--
      }
    } catch (error) {
      response.writeHead(500).end(String(error))
    }
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => { server.closeAllConnections(); server.close() })
  const baseUrl = `http://127.0.0.1:${server.address().port}`
  return { requests, client: new ScreenSignalingClient(httpTransport(baseUrl), options), maxInFlight: () => peak }
}

const typesOf = requests => requests.map(request => request.body.type)

test('并发提交多条信令：服务端按调用顺序逐条收到，任一时刻只有一个请求在进行', async t => {
  const types = ['prepared', 'offer', 'candidate', 'candidate', 'status', 'end']
  const { client, requests, maxInFlight } = await startServer(t, () => ({ holdMs: 15 }))
  const results = await Promise.all(types.map(type => client.postSignal(SID, type, { type })))
  assert.deepEqual(requests.map(request => [request.path, request.body]),
    types.map(type => [SIGNAL_PATH, { session_id: SID, type, payload: { type } }]), '请求体和到达顺序')
  assert.equal(maxInFlight(), 1, '上一个请求应答之前不发下一个')
  assert.deepEqual(results, types.map(() => ({ ok: true, attempts: 1 })))
})

test('503 SCREEN_STREAM_BUSY：按缺省间隔重试，服务端恢复后成功，只多出重试的那几次请求', async t => {
  const { client, requests } = await startServer(t, (_path, _body, index) => (index < 2 ? { status: 503, body: BUSY } : { body: { success: true, seq: 1 } }))
  const result = await client.postSignal(SID, 'offer', { sdp: 'v=0' })
  assert.deepEqual(result, { ok: true, attempts: 3 })
  assert.equal(requests.length, 3)
  for (const [previous, next] of [[requests[0], requests[1]], [requests[1], requests[2]]]) {
    const gap = next.at - previous.at
    assert.ok(gap >= SIGNAL_RETRY_DELAY_MS && gap < SIGNAL_RETRY_DELAY_MS + 2_000, `两次尝试的间隔应约为 ${SIGNAL_RETRY_DELAY_MS} 毫秒，实际 ${gap.toFixed(0)}`)
  }
})

test('一直繁忙：重试到上限后放弃并如实报告繁忙，尝试次数等于上限', async t => {
  const { client, requests } = await startServer(t, () => ({ status: 503, body: BUSY }), { retryDelayMs: 5, maxAttempts: 4 })
  assert.deepEqual(await client.postSignal(SID, 'candidate', { candidate: 'c' }), { ok: false, kind: 'busy', attempts: 4 })
  assert.equal(requests.length, 4)
})

test('409 SCREEN_SESSION_ENDED、404 SCREEN_SESSION_NOT_FOUND：不重试', async t => {
  for (const [status, body, kind] of [[409, ENDED, 'ended'], [404, NOT_FOUND, 'not_found']]) {
    const { client, requests } = await startServer(t, () => ({ status, body }))
    assert.deepEqual(await client.postSignal(SID, 'candidate', { candidate: 'c' }), { ok: false, kind, attempts: 1 })
    assert.equal(requests.length, 1, kind)
  }
})

test('409 SCREEN_SIGNAL_LIMIT、422、功能未启用的 503：不重试，按被拒绝报告', async t => {
  const invalid = { detail: [{ loc: ['body', 'payload', 'reason'], msg: 'Input should be one of the seven reasons', type: 'literal_error' }] }
  for (const [status, body] of [[409, LIMIT], [422, invalid], [503, DISABLED]]) {
    const { client, requests } = await startServer(t, () => ({ status, body }))
    assert.deepEqual(await client.postSignal(SID, 'end', { reason: 'not-a-reason' }), { ok: false, kind: 'rejected', attempts: 1 })
    assert.equal(requests.length, 1)
  }
})

test('连接被断开：按网络错误重试，恢复后成功', async t => {
  const { client, requests } = await startServer(t, (_path, _body, index) => (index === 0 ? { destroy: true } : { body: { success: true, seq: 1 } }), { retryDelayMs: 5 })
  assert.deepEqual(await client.postSignal(SID, 'prepared', {}), { ok: true, attempts: 2 })
  assert.equal(requests.length, 2)
})

test('读取会话：请求带 {session_id, after_seq}；服务端按自己的形状只返回编号大于游标的网页信令', async t => {
  const stored = [
    { seq: 1, dir: 'web', type: 'start', payload: {} },
    { seq: 2, dir: 'rt', type: 'prepared', payload: {} },
    { seq: 3, dir: 'web', type: 'answer', payload: { sdp: 'v=0 answer' } },
    { seq: 4, dir: 'web', type: 'candidate', payload: { candidate: 'candidate:1 1 udp 1 1.2.3.4 5000 typ srflx', sdp_mid: '0', sdp_mline_index: 0 } },
  ]
  const { client, requests } = await startServer(t, (_path, body) => ({
    body: {
      success: true,
      session: { id: SID, state: 'connecting', end_reason: null, stun_urls: ['stun:stun.example.test:3478'] },
      // load_session 的形状：没有 dir，Runtime 发的信令（dir 为 rt）不返回
      signals: stored.filter(signal => signal.dir === 'web' && signal.seq > body.after_seq).map(({ seq, type, payload }) => ({ seq, type, payload })),
    },
  }))
  const first = await client.readSession(SID, 0)
  assert.deepEqual(first.session, { id: SID, state: 'connecting', endReason: null, stunUrls: ['stun:stun.example.test:3478'] })
  assert.deepEqual(first.signals.map(signal => [signal.seq, signal.dir, signal.type]), [[1, null, 'start'], [3, null, 'answer'], [4, null, 'candidate']])
  assert.deepEqual(first.signals[2].payload, stored[3].payload, 'sdp_mid、sdp_mline_index 原样带回')
  assert.deepEqual((await client.readSession(SID, 4)).signals, [], '游标走到头就没有新信令')
  assert.deepEqual((await client.readSession(SID, 1)).signals.map(signal => signal.seq), [3, 4])
  assert.deepEqual(requests.map(request => [request.path, request.body]),
    [[SESSION_PATH, { session_id: SID, after_seq: 0 }], [SESSION_PATH, { session_id: SID, after_seq: 4 }], [SESSION_PATH, { session_id: SID, after_seq: 1 }]])
})

test('读取会话遇到繁忙或连接被断开：按这一次读取失败处理（返回 null，不抛异常），下一次读取恢复', async t => {
  const ok = { body: { success: true, session: { id: SID, state: 'prepared', end_reason: null }, signals: [] } }
  const { client } = await startServer(t, (_path, _body, index) => (index === 0 ? { status: 503, body: BUSY } : index === 1 ? { destroy: true } : ok))
  assert.equal(await client.readSession(SID, 0), null, '繁忙')
  assert.equal(await client.readSession(SID, 0), null, '连接被断开')
  assert.deepEqual(await client.readSession(SID, 0), { session: { id: SID, state: 'prepared', endReason: null, stunUrls: null }, signals: [] })
})

test('放弃（abandon）：正在等重试的那条不再重试，排队的不发，随后的 end 在它之后紧接着发出', async t => {
  const { client, requests } = await startServer(t, (_path, body) => (body.type === 'candidate' ? { destroy: true } : { body: { success: true, seq: null } }), { retryDelayMs: 300 })
  const retrying = client.postSignal(SID, 'candidate', { candidate: 'c1' }) // 连接被断开，正在等重试
  const queued = client.postSignal(SID, 'status', { state: 'streaming' }) // 排在它后面
  await new Promise(resolve => setTimeout(resolve, 100)) // 等第一条到达服务端并被断开
  assert.deepEqual(typesOf(requests), ['candidate'])
  client.abandon()
  const end = client.postSignal(SID, 'end', { reason: 'runtime_stopped' })
  assert.deepEqual(await retrying, { ok: false, kind: 'abandoned', attempts: 1 })
  assert.deepEqual(await queued, { ok: false, kind: 'abandoned', attempts: 0 })
  assert.deepEqual(await end, { ok: true, attempts: 1 })
  assert.deepEqual(typesOf(requests), ['candidate', 'end'])
  assert.deepEqual(requests[1].body, { session_id: SID, type: 'end', payload: { reason: 'runtime_stopped' } })
  // 等重试的定时器到点之后才发现已经放弃，所以 end 不会早于这个间隔；它也不另开并发请求
  const gap = requests[1].at - requests[0].at
  assert.ok(gap >= 300, `end 应在重试间隔之后才发出，实际相隔 ${gap.toFixed(0)} 毫秒`)
})

// 用真实定时器在子进程里跑：一条信令发不出去、正在等重试的时候脚本结束，进程必须自己退出。
// 重试定时器漏了 unref 时，进程会被它拖住继续重试。
test('信令客户端的重试定时器不阻止 Host 进程退出', () => {
  const entry = new URL('../dist/screen/signaling.js', import.meta.url).href
  const script = `
    import { ScreenSignalingClient, ScreenTransportError } from ${JSON.stringify(entry)}
    const client = new ScreenSignalingClient({ async post() { throw new ScreenTransportError('network') } })
    client.postSignal('sid', 'offer', {})
    await new Promise(resolve => setImmediate(resolve))
  `
  const child = spawnSync(process.execPath, ['--input-type=module', '--eval', script], { timeout: 5_000, encoding: 'utf8' })
  assert.equal(child.signal, null, `子进程没有自行退出：${child.stderr}`)
  assert.equal(child.status, 0, child.stderr)
})
