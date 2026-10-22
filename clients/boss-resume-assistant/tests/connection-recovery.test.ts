import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { CdpSocket, type CdpWebSocketLike } from '../src/main/cdp/CdpSocket.js'
import { CdpGateway } from '../src/main/cdp/CdpGateway.js'
import { CdpConnectionError } from '../src/main/cdp/errors.js'
import { withLocalRecovery } from '../src/main/boss/localOperationRecovery.js'
import { failResult, okResult, type BossOperation, type OpContext } from '../src/main/operations/types.js'

test('a silent WebSocket handshake times out and releases the unopened connection', async () => {
  class SilentSocket extends EventEmitter {
    readyState = 0; CLOSED = 3; terminated = false
    send() { throw Error('no command before handshake') }
    close() { this.readyState = 3 }
    terminate() { this.terminated = true; this.readyState = 3 }
  }
  const socket = new SilentSocket()
  const client = new CdpSocket({ timeoutMs: 15, socketFactory: () => socket as CdpWebSocketLike })
  await assert.rejects(client.connect('ws://fake.invalid'), CdpConnectionError)
  assert.equal(socket.terminated, true)
  assert.equal(client.isConnected, false)
  assert.equal(socket.listenerCount('open'), 0)
})

test('cancelling a handshake does not wait for its timeout or leave an open listener', async () => {
  const socket = new EventEmitter() as EventEmitter & CdpWebSocketLike
  Object.assign(socket, { readyState: 0, CLOSED: 3, send() {}, close() {}, terminate() {} })
  const controller = new AbortController()
  const client = new CdpSocket({ timeoutMs: 10000, socketFactory: () => socket })
  const pending = client.connect('ws://fake.invalid', controller.signal)
  controller.abort()
  await assert.rejects(pending, /cancelled/)
  assert.equal(client.isConnected, false)
  assert.equal(socket.listenerCount('open'), 0)
})

test('version HTTP has a local abort deadline before any websocket is created', async () => {
  const gateway = new CdpGateway({ timeoutMs: 10, fetchImpl: (async (_url, options) => {
    await new Promise((_, reject) => options!.signal!.addEventListener('abort', () => reject(new Error('local deadline'))))
    throw Error('unreachable')
  }) as typeof fetch })
  const keepAlive = setTimeout(() => {}, 1000)
  try { await assert.rejects(gateway.connect('http://127.0.0.1:9222'), CdpConnectionError) }
  finally { clearTimeout(keepAlive); await gateway.close() }
})

test('readonly transport recovery resumes the same retained batch without vision or re-executing the operation', async () => {
  let execute = 0, resume = 0, paid = 0
  const sleeps: number[] = []
  const payload = { candidate_name: 'already-read', images: [{ base64: 'fixture' }] }
  const operation: BossOperation<{}> = { name: 'boss_resume_batch', async execute(_args, ctx) {
    execute++
    ctx.recoveryContinuation!.resume = async restored => {
      assert.equal(restored.recoverBrowserTarget, true)
      resume++
      return resume < 3 ? failResult('same', 'EXECUTION_UNKNOWN', 'CDP request timed out: Input.dispatchMouseEvent', 'none', { resumes: [payload] })
        : okResult('same', 'done', 'none', { resumes: [payload, { candidate_name: 'next' }] })
    }
    return failResult('same', 'EXECUTION_UNKNOWN', 'CDP request timed out: Input.dispatchMouseEvent', 'none', { resumes: [payload] })
  } }
  const result = await withLocalRecovery(operation, { env: {}, sleep: async ms => { sleeps.push(ms) },
    inspect: { name: 'boss_overlay_inspect', async execute() { paid++; throw Error('vision must not gate transport') } } }).execute({},
      { signal: new AbortController().signal, progress() {} })
  assert.equal(result.success, true)
  assert.equal(execute, 1)
  assert.equal(resume, 3)
  assert.equal(paid, 0)
  assert.deepEqual(sleeps, [1000, 2000, 5000])
  assert.equal((result.data.resumes as unknown[])[0], payload)
})

test('stopping during recovery prevents the next reconnect and unknown external writes never enter it', async () => {
  for (const name of ['boss_resume_batch', 'boss_send_current']) {
    const controller = new AbortController()
    let resumed = 0, waited = 0
    const op: BossOperation<{}> = { name, async execute(_args, ctx: OpContext) {
      ctx.recoveryContinuation!.resume = async () => { resumed++; return okResult('x', '', 'none') }
      return failResult('x', 'EXECUTION_UNKNOWN', 'CDP request timed out: Input.dispatchMouseEvent', name === 'boss_send_current' ? 'unknown' : 'none')
    } }
    await withLocalRecovery(op, { env: {}, sleep: async () => { waited++; controller.abort() } }).execute({},
      { signal: controller.signal, progress() {} })
    assert.equal(resumed, 0)
    assert.equal(waited, name === 'boss_send_current' ? 0 : 1)
  }
})
