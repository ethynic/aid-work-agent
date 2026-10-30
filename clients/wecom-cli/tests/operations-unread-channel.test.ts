/**
 * P2 任务 3.1 unread_list 通道分派矩阵（对齐 readSession 范本；mock readDbFn/runDriverFn，
 * 绝不触达真实企微/驱动/本地库）。
 *
 * 覆盖：缺省 unset→UI+hint（不阻断）/ ui(compat)→UI+原因 / db、ui(transient)→本地库 /
 * channel='ui' 直接 UI / 白名单内 DB 失败兜底 UI（附 localdb_fallback_reason + 能力差异）/
 * 白名单外（身份/取消/登录态）不兜底 / 强制 local-db 保留具体错误 / focus 校验（空数组等
 * INVALID_ARGUMENT）/ name 透传本地库执行器（DB 分支不静默忽略 name）/ 负缓存上下文随身份
 * 变化失效（继承 readSession 语义）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createWecomUnreadListOperation } from '../src/operations/unreadList.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { LocalDbState } from '../src/platform/localdb/matrix.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import type { readLocaldbUnread } from '../src/platform/localdb/unread.js'

const CURRENT: CurrentAccount = { account: '10086', windowPid: 4242, dataDir: {} as CurrentAccount['dataDir'] }
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })

interface Harness {
  driverCalls(): number
  dbCalls: Array<{ focus?: string[]; forced?: boolean; name?: string }>
  /** writeStateFn 落盘的最新状态（观察负缓存清理） */
  written(): LocalDbState | null
  execute(args: Record<string, unknown>, state?: LocalDbState | null): Promise<{
    success: boolean
    code: string
    message: string
    data: Record<string, unknown>
  }>
}

function makeHarness(opts: {
  state?: LocalDbState | null
  readDbErr?: Error
  probeErr?: Error
  driverData?: unknown
} = {}): Harness {
  let driverCalls = 0
  const dbCalls: Array<{ focus?: string[]; forced?: boolean; name?: string }> = []
  let written: LocalDbState | null = null
  const readDb: typeof readLocaldbUnread = async (args) => {
    dbCalls.push({ ...(args.focus !== undefined ? { focus: args.focus } : {}),
      ...(args.name !== undefined ? { name: args.name } : {}), forced: args.forced })
    if (opts.readDbErr) throw opts.readDbErr
    return {
      message: '本地库待回复快照：待回复会话 1 个',
      data: { channel: 'local-db', judgment_basis: 'last_sender', conversations: [{ conversation_id: 'S:1_2', unreplied: true, last_time: '', last_text_preview: 'hi' }] },
    }
  }
  const op = createWecomUnreadListOperation({
    runDriverFn: async () => {
      driverCalls++
      if (opts.driverData !== undefined) return opts.driverData as Record<string, unknown>
      return { unread: [{ name: '微信客服', preview: 'p', unread_count: 7, x: 1, y: 2 }] }
    },
    localdb: {
      readStateFn: () => (opts.state === undefined ? null : written ?? opts.state),
      writeStateFn: (state) => { written = state; return true },
      probeAccountFn: async () => {
        if (opts.probeErr) throw opts.probeErr
        return CURRENT
      },
      logFn: () => true,
    },
    readDbFn: readDb,
  })
  return {
    driverCalls: () => driverCalls,
    dbCalls,
    written: () => written,
    execute: (args, state) => {
      if (state !== undefined) opts.state = state
      return op.execute(args, ctx()) as Promise<{ success: boolean; code: string; message: string; data: Record<string, unknown> }>
    },
  }
}

test('缺省 + unset：走 UI + doctor hint（不阻断），旧字段原样保留 + judgment_basis=badge', async () => {
  const h = makeHarness({ state: null })
  const r = await h.execute({})
  assert.equal(r.success, true)
  assert.equal(r.data.channel, 'ui')
  assert.equal(r.data.judgment_basis, 'badge')
  assert.equal(r.data.capability_difference, 'UI 基于角标快照，无法覆盖已读未回复')
  const unread = r.data.unread as Array<{ name: string; unread_count: number }>
  assert.equal(unread.length, 1)
  assert.equal(unread[0]!.name, '微信客服')
  assert.match(r.data.hint as string, /doctor --localdb/)
  assert.equal(h.dbCalls.length, 0)
  assert.equal(h.driverCalls(), 1)
})

test('channel=ui 显式：即使 db 模式也直接 UI；ui(compat)→UI+原因；ui(transient)→本地库', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  const explicitUi = makeHarness({ state: db })
  const r = await explicitUi.execute({ channel: 'ui' })
  assert.equal(r.data.channel, 'ui')
  assert.equal(explicitUi.dbCalls.length, 0)

  const compat: LocalDbState = { mode: 'ui', kind: 'compat', reason: 'x', checked_at: 'e' }
  const compatOp = makeHarness({ state: compat })
  const rc = await compatOp.execute({})
  assert.equal(rc.data.channel, 'ui')
  assert.equal(rc.data.localdb_fallback_reason, 'mode_ui_compat')
  assert.match(rc.message, /UI 兼容模式/)
  assert.equal(compatOp.dbCalls.length, 0)

  const transient: LocalDbState = { mode: 'ui', kind: 'transient', reason: 'x', checked_at: 'e' }
  const transientOp = makeHarness({ state: transient })
  const rt = await transientOp.execute({})
  assert.equal(rt.data.channel, 'local-db')
  assert.equal(transientOp.dbCalls.length, 1)
  assert.equal(transientOp.driverCalls(), 0)
})

test('db 模式：走本地库执行器；focus/forced 参数透传；UI 驱动不被调用', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  const h = makeHarness({ state: db })
  const r = await h.execute({ focus: ['陆伟'] })
  assert.equal(r.data.channel, 'local-db')
  assert.deepEqual(h.dbCalls, [{ focus: ['陆伟'], forced: false }])
  assert.equal(h.driverCalls(), 0)
})

test('db 模式 name 透传：readDbFn 收到原始 name（DB 分支不静默忽略 name 过滤）', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  const h = makeHarness({ state: db })
  const r = await h.execute({ name: 'Peer', focus: ['待回复群'] })
  assert.equal(r.data.channel, 'local-db')
  assert.deepEqual(h.dbCalls, [{ focus: ['待回复群'], name: 'Peer', forced: false }])
  assert.equal(h.driverCalls(), 0)
})

test('白名单内 DB 失败兜底 UI：附原因 + 能力差异 + hint；身份/取消类失败不兜底', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  for (const code of ['VERSION_UNSUPPORTED', 'KEY_SCAN_FAILED', 'SNAPSHOT_UNSTABLE', 'LOCALDB_UNAVAILABLE'] as const) {
    const h = makeHarness({ state: db, readDbErr: new CodedOperationError(code, 'synthetic') })
    const r = await h.execute({})
    assert.equal(r.success, true, code)
    assert.equal(r.data.channel, 'ui')
    assert.equal(r.data.localdb_fallback_reason, code)
    assert.equal(r.data.judgment_basis, 'badge')
    assert.match(r.message, /已走 UI 兜底/)
  }
  for (const code of ['IDENTITY_UNCONFIRMED', 'CURSOR_UNSUPPORTED', 'TARGET_NOT_FOUND'] as const) {
    const h = makeHarness({ state: db, readDbErr: new CodedOperationError(code, 'synthetic') })
    const r = await h.execute({})
    assert.equal(r.success, false, code)
    assert.equal(r.code, code, '白名单外失败不得兜底 UI')
    assert.equal(h.driverCalls(), 0, code)
  }
  const cancelled = makeHarness({ state: db, readDbErr: new CancelledError() })
  const rc = await cancelled.execute({})
  assert.equal(rc.code, 'CANCELLED')
  assert.equal(cancelled.driverCalls(), 0)
})

test('强制 local-db：失败保留具体错误不兜底；forced 标志透传执行器', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  const h = makeHarness({ state: db, readDbErr: new CodedOperationError('LOCALDB_UNAVAILABLE', 'synthetic') })
  const r = await h.execute({ channel: 'local-db' })
  assert.equal(r.success, false)
  assert.equal(r.code, 'LOCALDB_UNAVAILABLE')
  assert.equal(h.driverCalls(), 0)
  assert.deepEqual(h.dbCalls, [{ forced: true }])
})

test('focus 校验：空数组/非数组/空字符串项/非法 channel → INVALID_ARGUMENT，不触达任何驱动', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  for (const bad of [{ focus: [] }, { focus: 'x' }, { focus: ['  '] }, { focus: [42] }, { channel: 'db' }]) {
    const h = makeHarness({ state: db })
    const r = await h.execute(bad)
    assert.equal(r.code, 'INVALID_ARGUMENT', JSON.stringify(bad))
    assert.equal(h.dbCalls.length + h.driverCalls(), 0, JSON.stringify(bad))
  }
})

test('UI 通道 focus：忽略并注明（focus_ignored），不参与解析', async () => {
  const h = makeHarness({ state: null })
  const r = await h.execute({ focus: ['陆伟'] })
  assert.equal(r.data.focus_ignored, true)
  assert.match(r.message, /UI 通道不支持 focus 解析，已忽略/)
})

test('登录态失败（NOT_LOGGED_IN）不兜底；身份级失败使旧扫描负缓存失效（继承 readSession）', async () => {
  const withCache: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e', scan_failure: { context: 'x', until: 9999999999999 } }
  const h = makeHarness({ state: withCache, probeErr: new CodedOperationError('NOT_LOGGED_IN', 'synthetic') })
  const r = await h.execute({})
  assert.equal(r.code, 'NOT_LOGGED_IN')
  assert.equal(h.driverCalls(), 0)
  assert.equal(h.written()?.scan_failure, undefined, '已观察到登录/身份变化，旧扫描失败上下文不再有效')
  // 无 scan_failure 的普通状态不触发额外写状态
  const plain: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  const h2 = makeHarness({ state: plain, probeErr: new CodedOperationError('NOT_LOGGED_IN', 'synthetic') })
  await h2.execute({})
  assert.equal(h2.written(), null)
})
