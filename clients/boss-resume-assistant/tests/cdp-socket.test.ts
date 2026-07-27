import assert from 'node:assert/strict'
import test from 'node:test'
import { EventEmitter } from 'node:events'
import { CdpSocket } from '../src/main/cdp/CdpSocket.js'
import type { CdpWebSocketLike } from '../src/main/cdp/CdpSocket.js'
import { ForbiddenCdpMethodError } from '../src/main/cdp/methodPolicy.js'

/** Mock WebSocket：queueMicrotask 触发 open，记录 send 的 payload，可手动 emit message */
class FakeSocket extends EventEmitter {
  static CLOSED = 3
  readyState = 1
  CLOSED = 3
  sent: string[] = []
  constructor() {
    super()
    queueMicrotask(() => this.emit('open'))
  }
  send(payload: string): void {
    this.sent.push(payload)
  }
  close(): void {
    this.readyState = 3
    queueMicrotask(() => this.emit('close'))
  }
  terminate(): void {
    this.readyState = 3
  }
  /** 模拟 CDP 响应（有 id） */
  reply(msg: Record<string, unknown>): void {
    this.emit('message', Buffer.from(JSON.stringify(msg)))
  }
}

function makeClient(): { client: CdpSocket; socket: FakeSocket } {
  const socket = new FakeSocket()
  const client = new CdpSocket({ socketFactory: () => socket as unknown as CdpWebSocketLike, timeoutMs: 500 })
  return { client, socket }
}

test('Runtime.* 在发包前被拒绝（不发任何包）', async () => {
  const { client, socket } = makeClient()
  await client.connect('ws://test')
  await assert.rejects(client.send('Runtime.enable'), ForbiddenCdpMethodError)
  assert.equal(socket.sent.length, 0)
  await client.close()
})

test('脚本注入在发包前被拒绝', async () => {
  const { client, socket } = makeClient()
  await client.connect('ws://test')
  await assert.rejects(client.send('Page.addScriptToEvaluateOnNewDocument', { source: 'x' }), ForbiddenCdpMethodError)
  assert.equal(socket.sent.length, 0)
  await client.close()
})

test('请求 id 自增且 session 路由正确', async () => {
  const { client, socket } = makeClient()
  await client.connect('ws://test')
  const pending = client.send('Page.captureScreenshot', { format: 'png' }, 'sess-a')
  // 检查发出的包
  const sent = JSON.parse(socket.sent[0]!)
  assert.equal(sent.id, 1)
  assert.equal(sent.method, 'Page.captureScreenshot')
  assert.equal(sent.sessionId, 'sess-a')
  // 回复
  socket.reply({ id: 1, result: { data: 'base64...' }, sessionId: 'sess-a' })
  const result = await pending
  assert.deepEqual(result, { data: 'base64...' })
  await client.close()
})

test('响应 sessionId 不匹配被拒绝', async () => {
  const { client, socket } = makeClient()
  await client.connect('ws://test')
  const pending = client.send('Page.enable', {}, 'sess-a')
  socket.reply({ id: 1, result: {}, sessionId: 'sess-wrong' })
  await assert.rejects(pending, /session mismatch/)
  await client.close()
})

test('CDP error 响应被 reject', async () => {
  const { client, socket } = makeClient()
  await client.connect('ws://test')
  const pending = client.send('Page.enable')
  socket.reply({ id: 1, error: { code: -32000, message: 'boom' } })
  await assert.rejects(pending, /CDP error -32000: boom/)
  await client.close()
})

test('超时 reject', async () => {
  const { client } = makeClient()
  await client.connect('ws://test')
  await assert.rejects(client.send('Page.enable'), /timed out/)
  await client.close()
})

test('断线后所有 pending 被 reject', async () => {
  const { client, socket } = makeClient()
  await client.connect('ws://test')
  const pending = client.send('Page.enable')
  socket.emit('close')
  await assert.rejects(pending, /disconnected/)
  await client.close()
})

test('事件订阅：有 method 无 id 的消息作为事件分发', async () => {
  const { client, socket } = makeClient()
  await client.connect('ws://test')
  const events: string[] = []
  const unsub = client.onCdpEvent('Network.responseReceived', () => events.push('hit'))
  socket.reply({ method: 'Network.responseReceived', params: { x: 1 }, sessionId: 's' })
  socket.reply({ method: 'Page.frameNavigated', params: {} }) // 其他事件不触发
  await new Promise((r) => setTimeout(r, 10))
  assert.deepEqual(events, ['hit'])
  unsub()
  await client.close()
})

test('forbidden 事件被 emit 供上层暂停', async () => {
  const { client } = makeClient()
  await client.connect('ws://test')
  const forbidden: string[] = []
  client.on('forbidden', (m: string) => forbidden.push(m))
  await assert.rejects(client.send('Debugger.enable'), ForbiddenCdpMethodError)
  assert.deepEqual(forbidden, ['Debugger.enable'])
  await client.close()
})
