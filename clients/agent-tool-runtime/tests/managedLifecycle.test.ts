import assert from 'node:assert/strict'
import test from 'node:test'
import type { RuntimeHost } from '@aid/local-tool-host-core'
import { attachManagementIpc, type ManagementIpc } from '../src/managed-entry.js'

const tick = () => new Promise<void>(resolve => setImmediate(resolve))
function channelFixture(dispose: () => Promise<void>) {
  let message!: (frame: unknown) => void
  let disconnect!: () => void
  const sent: Record<string, unknown>[] = []
  const delivered: (() => void)[] = []
  let closes = 0, disposals = 0, requests = 0, unsubscribes = 0
  const channel: ManagementIpc = {
    send(frame, completed) { sent.push(frame); if (completed) delivered.push(completed) },
    onMessage(callback) { message = callback }, onDisconnect(callback) { disconnect = callback },
    close() { closes++ },
  }
  const host = {
    instanceId: 'isolated-current-instance', observe() { return () => { unsubscribes++ } },
    async request() { requests++; return { request_id: 'read', method: 'getState', code: 0, error: '', result: { state: 'running' } } },
    async dispose() { disposals++; await dispose() },
  } as unknown as RuntimeHost
  attachManagementIpc(host, channel)
  return { message: (frame: unknown) => message(frame), disconnect: () => disconnect(), sent, delivered,
    counts: () => ({ closes, disposals, requests, unsubscribes }) }
}

test('private shutdown rejects malformed and wrong-instance frames, then closes only after proof delivery', async () => {
  const f = channelFixture(async () => {})
  for (const change of [{ instance_id: 'previous-instance' }, { request_id: '' }, { request_id: 'a'.repeat(201) }, { extra: true }]) {
    f.message({ kind: 'runtime_shutdown_request', request_id: 'close', instance_id: 'isolated-current-instance', ...change })
  }
  await tick(); assert.equal(f.counts().disposals, 0); assert.equal(f.sent.length, 0)
  f.message({ kind: 'runtime_shutdown_request', request_id: 'close', instance_id: 'isolated-current-instance' })
  f.message({ kind: 'runtime_shutdown_request', request_id: 'duplicate', instance_id: 'isolated-current-instance' })
  await tick()
  assert.deepEqual(f.sent, [{ kind: 'runtime_shutdown_response', request_id: 'close', instance_id: 'isolated-current-instance', method: 'runtime.shutdown', code: 0, error: '', result: { closed: true } }])
  assert.equal(f.counts().disposals, 1); assert.equal(f.counts().closes, 0)
  assert.equal(f.delivered.length, 1); f.delivered[0]!(); assert.equal(f.counts().closes, 1)
})

test('owned execution refusal sends no successful proof or close and allows later normal stop requests', async () => {
  let owned = true
  const f = channelFixture(async () => { if (owned) throw new Error('isolated owned execution') })
  f.message({ kind: 'runtime_shutdown_request', request_id: 'first', instance_id: 'isolated-current-instance' })
  await tick()
  assert.deepEqual(f.sent[0], { kind: 'runtime_shutdown_response', request_id: 'first', instance_id: 'isolated-current-instance', method: 'runtime.shutdown', code: 11, error: '当前进程仍有执行需要收尾', result: null })
  assert.equal(f.counts().closes, 0); assert.equal(f.delivered.length, 0)
  f.message({ request_id: 'normal-stop', method: 'stop', params: { request_key: 'explicit' } }); await tick(); assert.equal(f.counts().requests, 1)
  owned = false; f.message({ kind: 'runtime_shutdown_request', request_id: 'retry', instance_id: 'isolated-current-instance' }); await tick()
  assert.equal(f.sent.at(-1)?.code, 0); f.delivered[0]!(); assert.equal(f.counts().closes, 1)
})
