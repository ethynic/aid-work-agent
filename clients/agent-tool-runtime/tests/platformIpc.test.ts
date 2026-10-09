import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createRuntimePlatformIpc } from '../src/managed-entry.js'

function channel() {
  const listeners: ((value: unknown) => void)[] = []; const disconnects: (() => void)[] = []; const sent: Record<string, unknown>[] = []
  return { sent, send(value: Record<string, unknown>) { sent.push(value) }, onMessage(fn: (value: unknown) => void) { listeners.push(fn) }, onDisconnect(fn: () => void) { disconnects.push(fn) },
    reply(value: unknown) { for (const fn of listeners) fn(value) }, disconnect() { for (const fn of disconnects) fn() } }
}
test('private selection response is correlated outside the H1 request id domain', async () => {
  const ipc = channel(); const platform = createRuntimePlatformIpc(ipc)
  const pending = platform.takeSelectedPackage({ selection_ref: 'opaque', request_key: 'same-intent', instance_id: 'instance' })
  const frame = ipc.sent[0]!
  assert.equal(frame.kind, 'runtime_platform_request'); assert.equal(frame.method, 'takeSelectedPackage')
  ipc.reply({ request_id: frame.id, code: 0, error: '', result: {} })
  ipc.reply({ kind: 'runtime_platform_response', id: frame.id, code: 0, error: '', result: { staged_path: 'C:/trusted/stage.zip', size: 7, sha256: 'a'.repeat(64) } })
  assert.equal((await pending).size, 7)
})
test('selection expiry, malformed snapshots, bounded timeout and disconnect fail without replay', async () => {
  for (const mode of ['expired', 'malformed', 'timeout', 'disconnect']) {
    const ipc = channel(); const platform = createRuntimePlatformIpc(ipc, 5)
    const pending = platform.takeSelectedPackage({ selection_ref: 'opaque', request_key: 'key', instance_id: 'instance' })
    if (mode === 'expired') ipc.reply({ kind: 'runtime_platform_response', id: ipc.sent[0]!.id, code: 7, error: '选择已失效', result: null })
    if (mode === 'malformed') ipc.reply({ kind: 'runtime_platform_response', id: ipc.sent[0]!.id, code: 0, error: '', result: { staged_path: 'x', size: -1, sha256: 'a'.repeat(64) } })
    if (mode === 'disconnect') ipc.disconnect()
    await assert.rejects(pending, error => error instanceof Error && [7, 11].includes((error as Error & { code: number }).code))
    assert.equal(ipc.sent.length, 1)
  }
})
