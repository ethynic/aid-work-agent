/** Synthetic encrypted DB + production operations: independent cursors, strict scopes and zero real UI. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createWecomGroupPollOperation } from '../src/operations/groups.js'
import { createWecomMessageSendOperation } from '../src/operations/messageSend.js'
import { groupMonitorFixture, changeGroupFixture } from './helpers/groupMonitorFixture.js'
import { decodeGroupCursor } from '../src/platform/localdb/groupCursor.js'
import type { OpContext } from '../src/operations/types.js'

const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const scope = { skip: process.platform !== 'win32' }

test('initial history is context only; separate tasks and groups cannot consume each other, even with late times', scope, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-poll-'))
  try {
    const f = await groupMonitorFixture(base)
    const op = createWecomGroupPollOperation(f.deps)
    const first = await op.execute({ target_name: '合成群甲' }, ctx())
    assert.equal(first.success, true, first.message)
    assert.equal((first.data.context_messages as unknown[]).length, 1)
    assert.deepEqual(first.data.new_messages, [])
    const other = await op.execute({ target_name: '合成群乙' }, ctx())
    const cursor = first.data.cursor as string
    changeGroupFixture(base, 'append', { text: '多行\n新的问题😀', mention_self: true, send_time: 1700000000 })
    const fresh = await op.execute({ target_name: '合成群甲', cursor }, ctx())
    assert.equal(fresh.success, true, fresh.message)
    const messages = fresh.data.new_messages as Array<{ text: string; mentions: Array<{ user_id: string }>; parse_trusted: boolean }>
    assert.equal(messages.length, 1)
    assert.equal(messages[0]!.text, '@FixtureSelf多行\n新的问题😀')
    assert.equal(messages[0]!.mentions[0]!.user_id, first.data.self_user_id)
    assert.equal(messages[0]!.parse_trusted, true)
    const replay = await op.execute({ target_name: '合成群甲', cursor }, ctx())
    assert.deepEqual(replay.data, fresh.data, 'another task using its own old cursor still sees all new messages')
    const empty = await op.execute({ target_name: '合成群乙', cursor: other.data.cursor as string }, ctx())
    assert.deepEqual(empty.data.new_messages, [])
    assert.deepEqual(readdirSync(join(base, 'work')), [])
    assert.deepEqual(f.calls, [], 'polling never invokes a UI driver')
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('pages advance only over returned insertion IDs, never skip a backlog at the sleep boundary', scope, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-pages-'))
  try {
    const f = await groupMonitorFixture(base)
    const op = createWecomGroupPollOperation(f.deps)
    const first = await op.execute({ target_name: '合成群甲' }, ctx())
    changeGroupFixture(base, 'append', { text: '积压', count: 55 })
    const page = await op.execute({ target_name: '合成群甲', cursor: first.data.cursor as string }, ctx())
    assert.equal(page.success, true, page.message)
    assert.equal((page.data.new_messages as unknown[]).length, 50)
    assert.equal(page.data.has_more, true)
    const rest = await op.execute({ target_name: '合成群甲', cursor: page.data.cursor as string }, ctx())
    assert.equal((rest.data.new_messages as unknown[]).length, 5)
    assert.equal(rest.data.has_more, false)
    const noNew = await op.execute({ target_name: '合成群甲', cursor: rest.data.cursor as string }, ctx())
    assert.equal(noNew.data.cursor, rest.data.cursor)
    assert.deepEqual(noNew.data.new_messages, [])
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('account, group-name reuse, generation and malformed cursors stop without any UI send or automatic rebase', scope, async () => {
  for (const change of ['account', 'target', 'source', 'name', 'anchor']) {
    const base = mkdtempSync(join(tmpdir(), 'wecom-cursor-'))
    try {
      const f = await groupMonitorFixture(base)
      const op = createWecomGroupPollOperation(f.deps)
      const first = await op.execute({ target_name: '合成群甲' }, ctx())
      let cursor = first.data.cursor as string
      if (change === 'account') cursor = cursor.replace('|9007199254740997|', '|100|')
      if (change === 'target') cursor = cursor.replace('R%3Afixture-a', 'R%3Afixture-b')
      if (change === 'source') changeGroupFixture(base, 'replace')
      if (change === 'name') changeGroupFixture(base, 'reuse-name')
      if (change === 'anchor') cursor = cursor.replace(/\|[0-9a-f]{64}\|1$/, '|' + '0'.repeat(64) + '|1')
      const result = await op.execute({ target_name: '合成群甲', cursor }, ctx())
      assert.equal(result.success, false, change)
      assert.equal(result.code, 'CURSOR_UNSUPPORTED', change)
      const send = await f.send.execute({ target_name: '合成群甲', group_cursor: cursor, text: '禁止发送' }, ctx())
      assert.equal(send.success, false, change)
      assert.deepEqual(f.calls, [], change + ' must fail before a search or write')
    } finally { rmSync(base, { recursive: true, force: true }) }
  }
  for (const value of ['v2|anything', 'gp1|%bad|v2|x', 'gp2|wrong']) assert.throws(() => decodeGroupCursor(value, '合成群甲'))
})

test('a scoped send writes the exact model text once to the original group and confirms using the local record', scope, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-scoped-send-'))
  try {
    const f = await groupMonitorFixture(base)
    const first = await createWecomGroupPollOperation(f.deps).execute({ target_name: '合成群甲' }, ctx())
    const result = await f.send.execute({ target_name: '合成群甲', group_cursor: first.data.cursor as string, text: '模型输出\nORCHID 😀' }, ctx())
    assert.equal(result.success, true, result.message)
    assert.equal(result.effect, 'applied')
    assert.equal(result.data.verification, 'local-record')
    assert.equal(f.calls.filter(call => call === 'message-send.ps1').length, 1)
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('a wrong current contact is checked first; contact-only search cannot become a group send', scope, async () => {
  const base = mkdtempSync(join(tmpdir(), 'wecom-contact-scope-'))
  try {
    const f = await groupMonitorFixture(base, true, '联系人')
    const first = await createWecomGroupPollOperation(f.deps).execute({ target_name: '合成群甲' }, ctx())
    let checks = 0
    let searches = 0
    const op = createWecomMessageSendOperation({ ...f.refDeps, localdb: f.deps, artifactDirFn: () => f.artifacts,
      receiptCrypto: { protect: async plain => Buffer.from(plain).toString('base64'), unprotect: async value => Buffer.from(value, 'base64').toString() },
      runDriverFn: async options => {
        if (options.script.endsWith('message-send.ps1')) {
          checks++
          assert.ok(options.args?.includes('group'), 'driver must receive expected group type')
          return { navigate_required: true, reason: 'current conversation is a contact, not the target group' }
        }
        if (options.script.endsWith('chat-search.ps1')) {
          searches++
          return { query: '合成群甲', items: [{ name: '合成群甲', subtitle: '', section: '联系人', x: 100, y: 100 }],
            best_index: 0, overlay: { x: 0, y: 0, w: 400, h: 500 }, jev: { used: false } }
        }
        throw new Error('must not select or send to contact')
      },
    })
    const result = await op.execute({ target_name: '合成群甲', group_cursor: first.data.cursor as string, text: '禁止串发' }, ctx())
    assert.equal(result.success, false)
    assert.equal(result.effect, 'none')
    assert.equal(checks, 1, 'first invocation only checks current target and performs no input')
    assert.equal(searches, 1, 'search happens only after the current-target mismatch')
  } finally { rmSync(base, { recursive: true, force: true }) }
})

test('unknown poll arguments are rejected before probing an account', async () => {
  let probes = 0
  const op = createWecomGroupPollOperation({ probeAccountFn: async () => { probes++; throw new Error('must not probe') } })
  for (const args of [{ target_name: '' }, { target_name: '合成群甲', limit: 200 }, { target_name: '合成群甲', cursor: '' }]) {
    assert.equal((await op.execute(args, ctx())).code, 'INVALID_ARGUMENT')
  }
  assert.equal(probes, 0)
})
