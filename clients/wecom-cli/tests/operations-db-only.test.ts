/** 读取入口只调用本地库；doctor 状态与本地库异常不能触发 OCR/UI。 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createWecomReadSessionOperation } from '../src/operations/readSession.js'
import { createWecomUnreadListOperation } from '../src/operations/unreadList.js'
import { createWecomWatchPollOperation } from '../src/operations/watchPoll.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import type { LocalDbState } from '../src/platform/localdb/matrix.js'

const current: CurrentAccount = { account: '10086', windowPid: 4242, dataDir: {} as CurrentAccount['dataDir'] }
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })
const states: Array<LocalDbState | null> = [null,
  { mode: 'unset', checked_at: 'e' },
  { mode: 'ui', kind: 'compat', checked_at: 'e' },
  { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }]

test('按名称读会话：doctor 未设/兼容模式也只调用本地库；真实版本准入由本地库执行器负责', async () => {
  for (const state of states) {
    const calls: Array<{ name: string; forced?: boolean }> = []
    const op = createWecomReadSessionOperation({
      localdb: { readStateFn: () => state, probeAccountFn: async () => current },
      readDbFn: async args => {
        calls.push({ name: args.name, forced: args.forced })
        return { message: 'db', data: { channel: 'local-db', messages: [] } }
      },
    })
    const result = await op.execute({ target_name: '客服群' }, ctx())
    assert.equal(result.success, true)
    assert.deepEqual(calls, [{ name: '客服群', forced: true }])
    const unsupported = await op.execute({ target_name: '客服群', channel: 'ui' }, ctx())
    assert.equal(unsupported.code, 'VERSION_UNSUPPORTED')
    assert.equal(calls.length, 1, '显式 UI 不读取任何消息')
  }
})

test('按名称读会话：本地库版本、密钥或快照失败原码返回，不降级 OCR', async () => {
  for (const code of ['VERSION_UNSUPPORTED', 'KEY_SCAN_FAILED', 'SNAPSHOT_UNSTABLE', 'LOCALDB_UNAVAILABLE'] as const) {
    const op = createWecomReadSessionOperation({
      localdb: { readStateFn: () => null, probeAccountFn: async () => current },
      readDbFn: async () => { throw new CodedOperationError(code, 'synthetic') },
    })
    const result = await op.execute({ target_name: '客服群' }, ctx())
    assert.equal(result.success, false)
    assert.equal(result.code, code)
    assert.equal(result.data.localdb_fallback_reason, undefined)
  }
})

test('旧 target_ref 仍须绑定当前账号，不能借版本失败绕过身份检查', async () => {
  let calls = 0
  const op = createWecomReadSessionOperation({
    localdb: { readStateFn: () => null, probeAccountFn: async () => current },
    verifyRefFn: ref => ({ name: '客服群', type: 'group', subtitle: '', account: ref === 'bound' ? 'other-account' : undefined }),
    readDbFn: async () => { calls++; return { message: 'db', data: {} } },
  })
  assert.equal((await op.execute({ target_ref: 'bound' }, ctx())).code, 'TARGET_REF_STALE')
  assert.equal((await op.execute({ target_ref: 'legacy' }, ctx())).code, 'TARGET_REF_STALE')
  assert.equal(calls, 0)
})

test('待回复快照：默认本地库；失败原码返回，显式 UI 拒绝', async () => {
  for (const state of states) {
    const calls: Array<{ forced?: boolean; name?: string; focus?: string[] }> = []
    const op = createWecomUnreadListOperation({
      localdb: { readStateFn: () => state, probeAccountFn: async () => current },
      readDbFn: async args => {
        calls.push(args)
        throw new CodedOperationError('VERSION_UNSUPPORTED', 'synthetic')
      },
    })
    const result = await op.execute({ name: '客服', focus: ['客服群'] }, ctx())
    assert.equal(result.code, 'VERSION_UNSUPPORTED')
    assert.deepEqual(calls, [{ name: '客服', focus: ['客服群'], forced: true }])
    assert.equal((await op.execute({ channel: 'ui' }, ctx())).code, 'VERSION_UNSUPPORTED')
    assert.equal(calls.length, 1)
  }
})

test('监控轮：默认本地库且失败保留水位；显式 UI 拒绝', async () => {
  for (const state of states) {
    const calls: Array<{ forced?: boolean; focus?: string[] }> = []
    const op = createWecomWatchPollOperation({
      localdb: { readStateFn: () => state, probeAccountFn: async () => current, logFn: () => true },
      watchDbFn: async args => {
        calls.push(args)
        throw new CodedOperationError('SNAPSHOT_UNSTABLE', 'synthetic')
      },
    })
    const result = await op.execute({ focus: ['客服群'] }, ctx())
    assert.equal(result.code, 'SNAPSHOT_UNSTABLE')
    assert.equal(result.data.db_unavailable, true)
    assert.equal(result.data.channel, 'local-db')
    assert.deepEqual(calls, [{ focus: ['客服群'], forced: true }])
    assert.equal((await op.execute({ channel: 'ui' }, ctx())).code, 'VERSION_UNSUPPORTED')
    assert.equal(calls.length, 1)
  }
})

test('待回复与监控的正常结果保留 DB 事件/目标过滤，绝不产生旧角标或文本水位字段', async () => {
  const unread = createWecomUnreadListOperation({
    localdb: { probeAccountFn: async () => current },
    readDbFn: async args => {
      assert.deepEqual(args, { name: '客服', focus: ['客服群'], forced: true })
      return { message: 'db', data: { channel: 'local-db', judgment_basis: 'last_sender', conversations: [{ conversation_id: 'R:1', unreplied: true }] } }
    },
  })
  const snapshot = await unread.execute({ name: '客服', focus: ['客服群'] }, ctx())
  assert.equal(snapshot.success, true)
  assert.equal(snapshot.data.judgment_basis, 'last_sender')
  assert.equal(snapshot.data.unread, undefined)

  const watch = createWecomWatchPollOperation({
    localdb: { probeAccountFn: async () => current },
    watchDbFn: async args => {
      assert.deepEqual(args, { focus: ['客服群'], forced: true })
      return { message: 'db', data: { channel: 'local-db', new_messages: [{ conversation_id: 'R:1', messages: [{ msg_id: 2, text: '新消息' }] }], last_sender: { 'R:1': 'peer' } } }
    },
  })
  const round = await watch.execute({ focus: ['客服群'] }, ctx())
  assert.equal(round.success, true)
  assert.equal((round.data.new_messages as Array<{ conversation_id: string }>)[0]!.conversation_id, 'R:1')
  assert.equal(round.data.events, undefined)
})

test('读取参数、登录身份和取消仍各自失败，不能伪装 DB 正常空轮', async () => {
  let unreadCalls = 0
  const unread = createWecomUnreadListOperation({
    localdb: { probeAccountFn: async () => { throw new CodedOperationError('NOT_LOGGED_IN', 'synthetic') } },
    readDbFn: async () => { unreadCalls++; return { message: 'db', data: {} } },
  })
  assert.equal((await unread.execute({ focus: [] }, ctx())).code, 'INVALID_ARGUMENT')
  assert.equal((await unread.execute({ focus: ['  '] }, ctx())).code, 'INVALID_ARGUMENT')
  assert.equal((await unread.execute({}, ctx())).code, 'NOT_LOGGED_IN')
  assert.equal(unreadCalls, 0)

  const watch = createWecomWatchPollOperation({
    localdb: { probeAccountFn: async () => current },
    watchDbFn: async () => { throw new CancelledError() },
  })
  assert.equal((await watch.execute({ focus: [] }, ctx())).code, 'INVALID_ARGUMENT')
  const cancelled = await watch.execute({}, ctx())
  assert.equal(cancelled.code, 'CANCELLED')
  assert.equal(cancelled.data.db_unavailable, undefined)

  const read = createWecomReadSessionOperation({ localdb: { probeAccountFn: async () => current },
    readDbFn: async () => { throw new Error('unreachable') } })
  assert.equal((await read.execute({ target_name: '客服群', max_pages: 2 }, ctx())).code, 'INVALID_ARGUMENT')
  assert.equal((await read.execute({ target_name: '' }, ctx())).code, 'INVALID_ARGUMENT')
})

test('登录身份变化清除旧扫描失败缓存，不因旧缓存阻断下一次本地库读取', async () => {
  const cached: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e',
    scan_failure: { context: 'old-account', until: 9999999999999 } }
  let written: LocalDbState | null = null
  const op = createWecomUnreadListOperation({
    localdb: { readStateFn: () => written ?? cached,
      writeStateFn: value => { written = value; return true },
      probeAccountFn: async () => { throw new CodedOperationError('NOT_LOGGED_IN', 'synthetic') } },
    readDbFn: async () => { throw new Error('unreachable') },
  })
  assert.equal((await op.execute({}, ctx())).code, 'NOT_LOGGED_IN')
  assert.equal((written as LocalDbState | null)?.scan_failure, undefined)
})
