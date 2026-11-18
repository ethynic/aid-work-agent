import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { createWeixinChatLatestOperation } from '../src/operations/chatLatest.js'
import { CodedOperationError } from '../src/operations/types.js'
import { unprotectText } from '../src/security/dpapi.js'

const row = (id: string, text: string) => ({
  runtime_id: id, kind: 'text', text, sender: 'peer', sender_name: null, clipped: false,
})

test('a fresh local operation decrypts the same copy, reports no new rows, and preserves it if encryption fails', { skip: process.platform !== 'win32' }, async () => {
  const stateDir = await mkdtemp(join(tmpdir(), 'weixin-latest-dpapi-'))
  let newMessage = false
  const driver = async (options: { stdin?: string }) => {
    const args = JSON.parse(options.stdin!)
    const base = { title: 'fixture', session_identity: '1:2:3', process_started_at: '123' }
    if (args.mode === 'identity') return base
    return {
      ...base, pages_scanned: 1, anchor_end: args.has_cache ? 0 : -1, empty_proven: false,
      rows: [row('a', 'exact private text'), ...(newMessage ? [row('b', 'new private text')] : [])], complete_history: true,
      anchor_context: { minute: '2026-11-18T10:00', offset: newMessage ? 2 : 1 },
    }
  }
  try {
    const first = await createWeixinChatLatestOperation({ stateDir, runDriverFn: driver, localOutput: true })
      .execute({ target_name: 'fixture' }, ctx)
    assert.equal(first.data.new_count, 1)
    const files = await readdir(stateDir)
    const path = join(stateDir, files[0]!)
    const encrypted = await readFile(path, 'utf8')
    const copy = JSON.parse(await unprotectText(encrypted))
    assert.equal(copy.messages[0].text, 'exact private text')
    assert.equal(copy.anchor[0].runtime_id, 'a')
    assert.deepEqual(copy.anchor_context, { minute: '2026-11-18T10:00', offset: 1 })
    const second = await createWeixinChatLatestOperation({ stateDir, runDriverFn: driver })
      .execute({ target_name: 'fixture' }, ctx)
    assert.deepEqual(second.data, { status: 'unchanged', pages_scanned: 1, new_count: 0 })
    assert.equal(await readFile(path, 'utf8'), encrypted)
    newMessage = true
    const failure = await createWeixinChatLatestOperation({
      stateDir, runDriverFn: driver,
      dpapiRunFn: async (command, value) => {
        if (command.includes('::Unprotect')) return JSON.stringify(copy)
        throw new Error('simulated protect failure: private text')
      },
    }).execute({ target_name: 'fixture' }, ctx)
    assert.equal(failure.success, false)
    assert.doesNotMatch(JSON.stringify(failure), /private text/)
    assert.equal(await readFile(path, 'utf8'), encrypted)
    assert.deepEqual(await readdir(stateDir), files, 'failed persistence must leave no partial or plaintext files')
  } finally { await rm(stateDir, { recursive: true, force: true }) }
})

test('empty snapshots require positive empty evidence and an aborted save leaves no copy', async () => {
  const stateDir = await mkdtemp(join(tmpdir(), 'weixin-latest-empty-'))
  const ac = new AbortController()
  let proven = false
  const driver = async (options: { stdin?: string }) => {
    const args = JSON.parse(options.stdin!)
    const base = { title: 'fixture', session_identity: '1:2:3', process_started_at: '123' }
    return args.mode === 'identity' ? base : { ...base, rows: [], pages_scanned: 1, anchor_end: -1, empty_proven: proven, complete_history: proven }
  }
  try {
    const op = createWeixinChatLatestOperation({
      stateDir, runDriverFn: driver,
      dpapiRunFn: async () => { ac.abort(); return 'ciphertext' },
    })
    assert.equal((await op.execute({ target_name: 'fixture' }, ctx)).code, 'CONTENT_UNAVAILABLE')
    assert.deepEqual(await readdir(stateDir), [])
    proven = true
    const result = await op.execute({ target_name: 'fixture' }, { ...ctx, signal: ac.signal })
    assert.equal(result.code, 'CANCELLED')
    assert.deepEqual(await readdir(stateDir), [])
  } finally { await rm(stateDir, { recursive: true, force: true }) }
})
const ctx = { signal: new AbortController().signal, progress() {} }

test('latest persists an encrypted identity-bound copy and exposes bodies only through the local constructor', async () => {
  const stateDir = await mkdtemp(join(tmpdir(), 'weixin-latest-'))
  let identity = '100:200:1.2'
  let processStart = '123456'
  let round = 0
  let fail = false
  const driver = async (options: { stdin?: string; useVisionProxy?: boolean }) => {
    assert.equal(options.useVisionProxy, false)
    const args = JSON.parse(options.stdin!)
    const base = { title: 'private-name', session_identity: identity, process_started_at: processStart }
    if (args.mode === 'identity') return base
    assert.equal(args.session_identity, identity)
    if (args.has_cache) {
      assert.equal(args.complete_history, true)
      assert.deepEqual(args.anchor_context, { minute: '2026-11-18T10:00', offset: args.anchor.length })
    }
    if (fail) throw new CodedOperationError('CONTENT_UNAVAILABLE', 'private-name private-body C:/private-path')
    round++
    const rows = [row('a', 'private-body'), ...(round > 1 ? [row('b', 'new-body')] : [])]
    assert.equal(args.anchor.length, round > 1 && identity.endsWith('1.2') ? 1 : 0)
    return { ...base, pages_scanned: 1, rows, anchor_end: args.anchor.length ? 0 : -1, empty_proven: false, complete_history: true,
      anchor_context: { minute: '2026-11-18T10:00', offset: rows.length } }
  }
  const dpapi = async (command: string, input: string) => command.includes('::Unprotect')
    ? Buffer.from(input, 'base64').toString('utf8') : Buffer.from(input).toString('base64')
  try {
    const local = createWeixinChatLatestOperation({ stateDir, runDriverFn: driver, dpapiRunFn: dpapi, localOutput: true })
    const first = await local.execute({ target_name: 'private-name' }, ctx)
    assert.equal(first.success, true)
    assert.equal(first.data.status, 'initialized')
    assert.equal(first.data.new_count, 1)
    assert.deepEqual(first.data.messages, [{ side: 'peer', kind: 'text', text: 'private-body', time: '', sender_name: null }])
    const files = await readdir(stateDir)
    assert.equal(files.length, 1)
    const saved = await readFile(join(stateDir, files[0]!), 'utf8')
    assert.ok(!saved.includes('private-body'))

    const remote = createWeixinChatLatestOperation({ stateDir, runDriverFn: driver, dpapiRunFn: dpapi })
    const result = await remote.execute({ target_name: 'private-name', include_messages: true } as never, ctx)
    assert.equal(result.code, 'INVALID_ARGUMENT')
    const updated = await remote.execute({ target_name: 'private-name' }, ctx)
    assert.deepEqual(updated.data, { status: 'updated', pages_scanned: 1, new_count: 1 })
    assert.doesNotMatch(JSON.stringify(updated), /private-name|private-body|new-body|\.dpapi/)
    const current = await readFile(join(stateDir, files[0]!), 'utf8')
    fail = true
    const failed = await remote.execute({ target_name: 'private-name' }, ctx)
    assert.equal(failed.code, 'CONTENT_UNAVAILABLE')
    assert.doesNotMatch(JSON.stringify(failed), /private-name|private-body|private-path/)
    assert.equal(await readFile(join(stateDir, files[0]!), 'utf8'), current)

    fail = false
    identity = '100:200:1.3'
    const rebound = await remote.execute({ target_name: 'private-name' }, ctx)
    assert.equal(rebound.data.status, 'initialized')
    assert.equal((await readdir(stateDir)).length, 2)
    processStart = '654321'
    const restarted = await remote.execute({ target_name: 'private-name' }, ctx)
    assert.equal(restarted.data.status, 'initialized')
    assert.equal((await readdir(stateDir)).length, 3, 'a reused PID/HWND/selection ID must never reuse another process copy')
  } finally { await rm(stateDir, { recursive: true, force: true }) }
})
