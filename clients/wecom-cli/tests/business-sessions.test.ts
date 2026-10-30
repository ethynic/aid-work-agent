/** Real synthetic encrypted DBs, production query bridge, no UI or real messages. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createWecomReadSessionOperation } from '../src/operations/readSession.js'
import { createWecomReadSessionsOperation } from '../src/operations/readSessions.js'
import { createWecomMessageSendOperation } from '../src/operations/messageSend.js'
import { createWecomSendStatusOperation } from '../src/operations/sendStatus.js'
import { CodedOperationError } from '../src/operations/types.js'
import { groupMonitorFixture, changeGroupFixture } from './helpers/groupMonitorFixture.js'
import type { OpContext } from '../src/operations/types.js'

const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const windows = { skip: process.platform !== 'win32' }

test('one read API handles groups, stable session survives rename, independent cursors keep new records', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    const op = createWecomReadSessionOperation({ localdb: f.deps })
    const first = await op.execute({ session: '合成群甲' }, ctx())
    assert.equal(first.success, true, first.message)
    assert.equal(first.data.baseline, true)
    assert.deepEqual(first.data.new_messages, [])
    const locator = first.data.session as string
    changeGroupFixture(base, 'reuse-name')
    changeGroupFixture(base, 'append', { text: 'new in original group' })
    const next = await op.execute({ session: locator, cursor: first.data.cursor as string }, ctx())
    assert.equal(next.success, true, next.message)
    assert.equal(next.data.title, '已改名')
    assert.deepEqual((next.data.messages as { text: string }[]).map(row => row.text), ['new in original group'])
    assert.equal(next.data.session, locator)
    assert.deepEqual(f.calls, [], 'DB reads must never navigate UI')
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('batch returns successful contents and per-target failures; total page limit preserves backlog', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    const op = createWecomReadSessionsOperation(f.deps)
    const targets = ['合成群甲', '不存在的会话']
    const first = await op.execute({ sessions: targets }, ctx())
    assert.equal(first.success, true, first.message)
    assert.equal((first.data.failed as unknown[]).length, 1)
    changeGroupFixture(base, 'append', { text: 'new', count: 3 })
    const second = await op.execute({ sessions: targets, cursor: first.data.cursor as string, limit: 2 }, ctx())
    assert.equal(second.success, true, second.message)
    assert.equal(second.data.has_more, true)
    assert.equal((second.data.sessions as { messages: unknown[] }[]).reduce((n, row) => n + row.messages.length, 0), 2)
    const third = await op.execute({ sessions: targets, cursor: second.data.cursor as string, limit: 2 }, ctx())
    assert.equal(third.success, true, third.message)
    assert.equal((third.data.sessions as { messages: unknown[] }[]).reduce((n, row) => n + row.messages.length, 0), 1)
    assert.ok(Buffer.byteLength(JSON.stringify(second.data)) <= 16384)
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('session send avoids search when current target matches and returns canonical session', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    const read = await createWecomReadSessionOperation({ localdb: f.deps }).execute({ session: '合成群甲' }, ctx())
    assert.equal(read.success, true, read.message)
    const sent = await f.send.execute({ session: read.data.session as string, text: 'one synthetic reply' }, ctx())
    assert.equal(sent.success, true, sent.message)
    assert.equal(sent.data.session, read.data.session)
    assert.equal(sent.data.verification, 'local-record')
    assert.deepEqual(f.calls, ['message-send.ps1'])
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('internal resolution returns canonical metadata even when the latest body exceeds the read budget', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    changeGroupFixture(base, 'append', { text: 'x'.repeat(20000) })
    const op = createWecomReadSessionOperation({ localdb: f.deps })
    const read = await op.execute({ session: '合成群甲', limit: 1 }, ctx())
    assert.equal(read.code, 'CONTENT_UNAVAILABLE', 'this fixture must actually exceed the ordinary read budget')
    const resolved = await op.execute({ session: '合成群甲', limit: 1, resolve_only: true }, ctx())
    assert.equal(resolved.success, true, resolved.message)
    assert.match(resolved.data.session as string, /^ws1\./)
    assert.equal(resolved.data.title, '合成群甲')
    assert.equal(resolved.data.kind, 'group')
    assert.equal(resolved.data.resolve_only, true)
    assert.equal(resolved.data.cursor, undefined)
    assert.equal(resolved.data.messages, undefined)
    assert.deepEqual(f.calls, [], 'identity resolution cannot navigate or send')
    const invalid = await op.execute({ session: resolved.data.session as string, resolve_only: true, cursor: 'wb1.invalid' }, ctx())
    assert.equal(invalid.code, 'INVALID_ARGUMENT', 'resolution cannot consume or manufacture a message cursor')
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('cursor inherits its original subset and a temporarily missing target cannot block other sessions', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    const op = createWecomReadSessionsOperation(f.deps)
    const first = await op.execute({ sessions: ['合成群甲'] }, ctx())
    changeGroupFixture(base, 'append', { text: 'other group', conversation_id: 'R:fixture-b' })
    changeGroupFixture(base, 'append', { text: 'original group' })
    const next = await op.execute({ cursor: first.data.cursor as string }, ctx())
    assert.equal(next.success, true, next.message)
    assert.deepEqual((next.data.sessions as { messages: { text: string }[] }[]).flatMap(s => s.messages.map(m => m.text)), ['original group'])

    // An empty second group can disappear from the synchronized session table without deleting A's anchor.
    const another = await groupMonitorFixture(join(base, 'partial'))
    const batch = createWecomReadSessionsOperation(another.deps)
    const initial = await batch.execute({ sessions: ['合成群甲', '合成群乙'] }, ctx())
    changeGroupFixture(join(base, 'partial'), 'session-visible', { visible: false })
    changeGroupFixture(join(base, 'partial'), 'append', { text: 'A continues' })
    const partial = await batch.execute({ cursor: initial.data.cursor as string }, ctx())
    assert.equal(partial.success, true, partial.message)
    assert.equal((partial.data.failed as unknown[]).length, 1)
    assert.deepEqual((partial.data.sessions as { messages: { text: string }[] }[]).flatMap(s => s.messages.map(m => m.text)), ['A continues'])
    changeGroupFixture(join(base, 'partial'), 'session-visible', { visible: true })
    changeGroupFixture(join(base, 'partial'), 'append', { text: 'B recovered', conversation_id: 'R:fixture-b' })
    const recovered = await batch.execute({ cursor: partial.data.cursor as string }, ctx())
    assert.equal(recovered.success, true, recovered.message)
    assert.deepEqual((recovered.data.sessions as { messages: { text: string }[] }[]).flatMap(s => s.messages.map(m => m.text)), ['B recovered'])
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('unknown sender recovery cursor rereads the original record after it leaves recent context', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    const op = createWecomReadSessionOperation({ localdb: f.deps })
    const first = await op.execute({ session: '合成群甲' }, ctx())
    const pending = (first.data.pending_sessions as { session: string; unknown_messages: { msg_id: number; sender_id: string; recovery_cursor: string; baseline: boolean }[] }[])[0]!
    const original = pending.unknown_messages[0]!
    assert.equal(original.baseline, true)
    changeGroupFixture(base, 'append', { text: 'later context', count: 70 })
    changeGroupFixture(base, 'verify-wechat-sender')
    const recovered = await op.execute({ session: pending.session, cursor: original.recovery_cursor, limit: 1 }, ctx())
    assert.equal(recovered.success, true, recovered.message)
    const message = (recovered.data.messages as { msg_id: number; sender_id: string; sender_platform: string }[])[0]!
    assert.equal(message.msg_id, original.msg_id)
    assert.equal(message.sender_id, original.sender_id)
    assert.equal(message.sender_platform, 'wechat')
    assert.deepEqual(recovered.data.new_messages, [], 'refreshing old identity cannot turn startup history into a new customer message')
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('baseline byte retry never enters new_messages while later records in the same session remain new', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    changeGroupFixture(base, 'append', { text: 'old large context ' + 'x'.repeat(5500), count: 3 })
    const op = createWecomReadSessionOperation({ localdb: f.deps })
    const first = await op.execute({ session: '合成群甲', limit: 50 }, ctx())
    assert.equal(first.success, true, first.message)
    assert.equal(first.data.has_more, true)
    assert.deepEqual(first.data.new_messages, [])
    changeGroupFixture(base, 'append', { text: 'after start' })
    let cursor = first.data.cursor as string
    const fresh: string[] = []
    const seen = (first.data.messages as { msg_id: number }[]).map(m => m.msg_id)
    for (let page = 0; page < 5; page++) {
      const next = await op.execute({ session: first.data.session as string, cursor, limit: 50 }, ctx())
      assert.equal(next.success, true, next.message)
      fresh.push(...(next.data.new_messages as { text: string }[]).map(m => m.text))
      seen.push(...(next.data.messages as { msg_id: number }[]).map(m => m.msg_id))
      cursor = next.data.cursor as string
      if (!next.data.has_more) break
    }
    assert.deepEqual(fresh, ['after start'])
    assert.deepEqual([...seen].sort(), [1, 2, 3, 4, 5])
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('unknown session send keeps canonical target and original receipt for read-only reconciliation', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    const crypto = { protect: async (plain: string) => Buffer.from(plain).toString('base64'),
      unprotect: async (cipher: string) => Buffer.from(cipher, 'base64').toString() }
    let physicalCalls = 0
    const op = createWecomMessageSendOperation({ ...f.refDeps, localdb: f.deps, artifactDirFn: () => f.artifacts, receiptCrypto: crypto,
      runDriverFn: async options => {
        assert.ok(options.script.endsWith('message-send.ps1'))
        physicalCalls++
        changeGroupFixture(base, 'append', { text: 'recorded but ack lost', self: true })
        throw new CodedOperationError('EXECUTION_UNKNOWN', 'synthetic post-Enter uncertainty', 'unknown')
      },
    })
    const unknown = await op.execute({ session: '合成群甲', text: 'recorded but ack lost' }, ctx())
    assert.equal(unknown.effect, 'unknown')
    assert.equal(unknown.code, 'EXECUTION_UNKNOWN')
    assert.match(unknown.data.session as string, /^ws1\./)
    assert.equal(typeof unknown.data.receipt_id, 'string')
    const status = await createWecomSendStatusOperation({ localdb: f.deps, artifactDirFn: () => f.artifacts, receiptCrypto: crypto })
      .execute({ receipt_id: unknown.data.receipt_id as string }, ctx())
    assert.equal(status.success, true, status.message)
    assert.equal(status.data.send_status, 'confirmed')
    assert.equal(physicalCalls, 1)
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('a known empty group has a stable session and can receive its first confirmed record', windows, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-business-'))
  try {
    const f = await groupMonitorFixture(base)
    const read = await createWecomReadSessionOperation({ localdb: f.deps }).execute({ session: '合成群乙' }, ctx())
    assert.equal(read.success, true, read.message)
    assert.deepEqual(read.data.messages, [])
    const sent = await f.send.execute({ session: read.data.session as string, text: 'first synthetic message' }, ctx())
    assert.equal(sent.success, true, sent.message)
    assert.equal(sent.data.verification, 'local-record')
    assert.deepEqual(f.calls, ['message-send.ps1'])
  } finally { rmSync(base, { recursive: true, force: true }) }
})
