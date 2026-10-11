import assert from 'node:assert/strict'
import test from 'node:test'
import { EventEmitter } from 'node:events'
import { CdpGateway, type CdpTargetInfo } from '../src/main/cdp/CdpGateway.js'
import type { CdpWebSocketLike } from '../src/main/cdp/CdpSocket.js'

class MockSocket extends EventEmitter {
  static CLOSED = 3
  readyState = 1
  CLOSED = 3
  sent: Record<string, unknown>[] = []
  constructor(private readonly targetInfos: CdpTargetInfo[] = []) {
    super()
    // 用宏任务延迟 open，确保 connect 在 await fetch 之后注册的 once('open') 能收到
    setTimeout(() => this.emit('open'), 0)
  }
  send(payload: string): void {
    const msg = JSON.parse(payload)
    this.sent.push(msg)
    // 使用真实 CDP 协议边界：列出目标、建立 session，其余返回空 result。
    if (msg.method === 'Target.getTargets') {
      queueMicrotask(() =>
        this.emit('message', Buffer.from(JSON.stringify({ id: msg.id, result: { targetInfos: this.targetInfos } }))),
      )
    } else if (msg.method === 'Target.attachToTarget') {
      queueMicrotask(() =>
        this.emit('message', Buffer.from(JSON.stringify({ id: msg.id, result: { sessionId: 'sess-1' } }))),
      )
    } else {
      queueMicrotask(() => this.emit('message', Buffer.from(JSON.stringify({ id: msg.id, result: {}, sessionId: 'sess-1' }))))
    }
  }
  close(): void {
    this.readyState = 3
    queueMicrotask(() => this.emit('close'))
  }
  terminate(): void {
    this.readyState = 3
  }
}

function makeGateway(targetInfos: CdpTargetInfo[] = []): { gw: CdpGateway; socket: MockSocket } {
  const socket = new MockSocket(targetInfos)
  const fetchImpl = (async (url: string) => {
    if (url.endsWith('/json/version')) {
      return {
        json: async () => ({ webSocketDebuggerUrl: 'ws://127.0.0.1:9222/devtools/browser/fake' }),
      } as Response
    }
    if (url.endsWith('/json/list')) {
      return {
        json: async () => [
          { id: 't1', type: 'page', url: 'https://www.zhipin.com/web/chat/recommend', title: 'BOSS', targetId: 'T1' },
        ],
      } as Response
    }
    throw new Error(`unexpected fetch ${url}`)
  }) as typeof fetch

  const gw = new CdpGateway({
    socketFactory: () => socket as unknown as CdpWebSocketLike,
    fetchImpl,
    timeoutMs: 500,
  })
  return { gw, socket }
}

test('connect 从 /json/version 拿 wsDebuggerUrl 并连 socket', async () => {
  const { gw, socket } = makeGateway()
  await gw.connect('http://127.0.0.1:9222')
  assert.equal(gw.isConnected, true)
  assert.equal(socket.sent.length, 0) // connect 不发 CDP
  await gw.close()
})

test('attachToTarget 建立 session，后续方法带 sessionId', async () => {
  const { gw, socket } = makeGateway()
  await gw.connect('http://127.0.0.1:9222')
  const attach = await gw.attachToTarget('T1')
  assert.equal(attach.sessionId, 'sess-1')
  assert.equal(gw.getSessionId(), 'sess-1')

  await gw.pageEnable()
  const lastSent = socket.sent.at(-1) as { method: string; sessionId?: string } | undefined
  assert.equal(lastSent?.method, 'Page.enable')
  assert.equal(lastSent?.sessionId, 'sess-1')
  await gw.close()
})

test('attachToRecommendPage 跳过非 page 和非 BOSS 目标并建立 session', async () => {
  const { gw, socket } = makeGateway([
    { targetId: 'worker', type: 'worker', url: 'https://www.zhipin.com/worker', title: 'worker' },
    { targetId: 'other', type: 'page', url: 'https://example.com', title: 'other' },
    { targetId: 'boss', type: 'page', url: 'https://www.zhipin.com/web/chat/recommend', title: 'BOSS' },
  ])
  await gw.connect('http://127.0.0.1:9222')
  try {
    const attached = await gw.attachToRecommendPage()
    assert.deepEqual(attached, { targetId: 'boss', sessionId: 'sess-1' })
    assert.equal(gw.getSessionId(), 'sess-1')
    assert.deepEqual(socket.sent.map(({ method, params, sessionId }) => ({ method, params, sessionId })), [
      { method: 'Target.getTargets', params: {}, sessionId: undefined },
      { method: 'Target.attachToTarget', params: { targetId: 'boss', flatten: true }, sessionId: undefined },
    ])
  } finally {
    await gw.close()
  }
})

test('attachToRecommendPage 无 BOSS page 时拒绝且不 attach', async () => {
  const { gw, socket } = makeGateway([
    { targetId: 'worker', type: 'worker', url: 'https://www.zhipin.com/worker', title: 'worker' },
    { targetId: 'other', type: 'page', url: 'https://example.com', title: 'other' },
  ])
  await gw.connect('http://127.0.0.1:9222')
  try {
    await assert.rejects(gw.attachToRecommendPage(), /no BOSS page target found/)
    assert.equal(gw.getSessionId(), undefined)
    assert.deepEqual(socket.sent.map(({ method }) => method), ['Target.getTargets'])
  } finally {
    await gw.close()
  }
})

test('captureScreenshot 传 format 与 captureBeyondViewport=false', async () => {
  const { gw, socket } = makeGateway()
  await gw.connect('http://127.0.0.1:9222')
  await gw.attachToTarget('T1')
  // 截图 mock 要返回 data
  socket.send = function (payload: string) {
    const msg = JSON.parse(payload)
    this.sent.push(msg)
    queueMicrotask(() =>
      this.emit('message', Buffer.from(JSON.stringify({ id: msg.id, result: { data: 'b64' }, sessionId: 'sess-1' }))),
    )
  }
  await gw.captureScreenshot({ format: 'png' })
  const shot = socket.sent.at(-1) as { method: string; params: { format?: string; captureBeyondViewport?: boolean } } | undefined
  assert.equal(shot?.method, 'Page.captureScreenshot')
  assert.equal(shot?.params.format, 'png')
  assert.equal(shot?.params.captureBeyondViewport, false)
  await gw.close()
})

test('DOMRects 只读采集选项显式开启，后续普通快照恢复默认 false', async () => {
  const { gw, socket } = makeGateway()
  await gw.connect('http://127.0.0.1:9222')
  await gw.attachToTarget('T1')
  try {
    await gw.captureDomSnapshot()
    await gw.captureDomSnapshot({ includeDOMRects: true })
    await gw.captureDomSnapshot()
    assert.deepEqual(socket.sent.filter((s) => s.method === 'DOMSnapshot.captureSnapshot'),
      [false, true, false].map((includeDOMRects, i) => ({ id: i + 2,
        method: 'DOMSnapshot.captureSnapshot',
        params: { computedStyles: [], includePaintOrder: false, includeDOMRects }, sessionId: 'sess-1' })))
  } finally { await gw.close() }
})
