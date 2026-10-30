/**
 * P2 任务 3.2 watch_poll 通道分派矩阵（对齐 operations-unread-channel.test.ts 范本；
 * mock watchDbFn/runDriverFn，绝不触达真实企微/驱动/本地库）。
 *
 * 覆盖：缺省 unset→UI+hint（不阻断）/ ui(compat)→UI+原因 / db、ui(transient)→本地库 /
 * channel='ui' 直接 UI（既有事件字段零改动 + focus_ignored）/ 白名单内 DB 失败【不落 UI】
 * 返回结构化 db_unavailable 失败（水位保留）/ 取消与 IDENTITY_UNCONFIRMED 不吞 / 强制
 * local-db 同样结构化不落 UI / focus 校验（空数组等 INVALID_ARGUMENT）。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createWecomWatchPollOperation, type LocaldbWatchEvent } from '../src/operations/watchPoll.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { LocalDbState } from '../src/platform/localdb/matrix.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import type { runLocaldbWatch, LocaldbWatchDeps } from '../src/platform/localdb/watch.js'

const CURRENT: CurrentAccount = { account: '10086', windowPid: 4242, dataDir: {} as CurrentAccount['dataDir'] }
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })

interface Harness {
  driverCalls(): number
  dbCalls: Array<{ focus?: string[]; forced?: boolean }>
  execute(args: Record<string, unknown>, state?: LocalDbState | null): Promise<{
    success: boolean
    code: string
    message: string
    data: Record<string, unknown>
  }>
}

function makeHarness(opts: {
  state?: LocalDbState | null
  watchDbErr?: Error
  probeErr?: Error
  driverData?: unknown
  watchDbData?: Record<string, unknown>
} = {}): Harness {
  let driverCalls = 0
  const dbCalls: Array<{ focus?: string[]; forced?: boolean }> = []
  const watchDb: typeof runLocaldbWatch = async (args) => {
    dbCalls.push({ ...(args.focus !== undefined ? { focus: args.focus } : {}), forced: args.forced })
    if (opts.watchDbErr) throw opts.watchDbErr
    return {
      message: '本地库 watch 轮：新消息会话 0 个',
      data: opts.watchDbData ?? { channel: 'local-db', new_messages: [], last_sender: {}, resume_note: 'n' },
    }
  }
  const localdb: LocaldbWatchDeps = {
    readStateFn: () => (opts.state === undefined ? null : opts.state),
    probeAccountFn: async () => {
      if (opts.probeErr) throw opts.probeErr
      return CURRENT
    },
    logFn: () => true,
  }
  const op = createWecomWatchPollOperation({
    runDriverFn: async () => {
      driverCalls++
      if (opts.driverData !== undefined) return opts.driverData as Record<string, unknown>
      return { unread: [{ name: '微信客服', preview: 'p', unread_count: 7, x: 1, y: 2 }] }
    },
    statePathFn: () => join2('watch-state.json'),
    artifactDirFn: () => join2('artifacts'),
    loadStateFn: () => ({ sessions: {} }),
    saveStateFn: () => {},
    localdb,
    watchDbFn: watchDb,
  })
  return {
    driverCalls: () => driverCalls,
    dbCalls,
    execute: (args, state) => {
      if (state !== undefined) opts.state = state
      return op.execute(args, ctx()) as Promise<{ success: boolean; code: string; message: string; data: Record<string, unknown> }>
    },
  }
}

/** 测试用临时根（真实路径仅用于 statePath/artifactDir 形参，UI 链路 mock 不触盘写） */
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join as join2 } from 'node:path'
const tmpRoot = mkdtempSync(join2(tmpdir(), 'wecom-watch-channel-'))
process.on('exit', () => {
  try { rmSync(tmpRoot, { recursive: true, force: true }) } catch { /* ignore */ }
})

test('缺省 + unset：走 UI + doctor hint（不阻断），既有 events 字段原样保留', async () => {
  const h = makeHarness({ state: null })
  const r = await h.execute({})
  assert.equal(r.success, true)
  assert.equal(r.data.channel, 'ui')
  const events = r.data.events as Array<{ type: string }>
  assert.equal(events.length, 1)
  assert.equal(events[0]!.type, 'tick')
  assert.match(r.data.hint as string, /doctor --localdb/)
  assert.equal(h.dbCalls.length, 0)
  assert.equal(h.driverCalls(), 2, 'UI 轮 = unread 快照 1 次 + 候选 read-session 读取 1 次')
})

test('channel=ui 显式：db 模式也直接 UI（既有行为零改动）；ui(compat)→UI+原因；ui(transient)→本地库', async () => {
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
  assert.equal(compatOp.dbCalls.length, 0)

  const transient: LocalDbState = { mode: 'ui', kind: 'transient', reason: 'x', checked_at: 'e' }
  const transientOp = makeHarness({ state: transient })
  const rt = await transientOp.execute({})
  assert.equal(rt.data.channel, 'local-db')
  assert.equal(transientOp.dbCalls.length, 1)
  assert.equal(transientOp.driverCalls(), 0)
})

test('db 模式：走本地库执行器；focus/forced 透传；UI 驱动不被调用；事件形态=冻结点 4', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  const event: LocaldbWatchEvent = {
    channel: 'local-db',
    new_messages: [{ conversation_id: 'S:1_2', messages: [{ side: 'peer', msg_id: 9, text: 'hi', time: '', parse_trusted: true }] }],
    last_sender: { 'S:1_2': 'peer' },
  }
  const h = makeHarness({ state: db, watchDbData: { ...event, resume_note: 'n' } })
  const r = await h.execute({ focus: ['陆伟'] })
  assert.equal(r.success, true)
  assert.equal(r.data.channel, 'local-db')
  const newMessages = r.data.new_messages as Array<{ conversation_id: string }>
  assert.equal(newMessages[0]!.conversation_id, 'S:1_2')
  assert.deepEqual(h.dbCalls, [{ focus: ['陆伟'], forced: false }])
  assert.equal(h.driverCalls(), 0)
})

test('DB 失败【不落 UI】：白名单内错误 → 结构化失败（ok:false + db_unavailable:true），水位保留不兜底', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  for (const code of ['VERSION_UNSUPPORTED', 'KEY_SCAN_FAILED', 'SNAPSHOT_UNSTABLE', 'LOCALDB_UNAVAILABLE'] as const) {
    const h = makeHarness({ state: db, watchDbErr: new CodedOperationError(code, 'synthetic') })
    const r = await h.execute({})
    assert.equal(r.success, false, code)
    assert.equal(r.code, code, code)
    assert.equal(r.data.db_unavailable, true, `${code} 必须带 db_unavailable 标记（不自动落 UI）`)
    assert.equal(r.data.channel, 'local-db')
    assert.equal(h.driverCalls(), 0, `${code} 不得触发 UI 驱动`)
    assert.match(r.message, /不落 UI|水位已保留/)
  }
})

test('取消与身份失败不吞：CANCELLED 透传；IDENTITY_UNCONFIRMED 结构化失败（同样不落 UI）', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  const cancelled = makeHarness({ state: db, watchDbErr: new CancelledError() })
  const rc = await cancelled.execute({})
  assert.equal(rc.code, 'CANCELLED')
  assert.equal(cancelled.driverCalls(), 0)

  const identity = makeHarness({ state: db, watchDbErr: new CodedOperationError('IDENTITY_UNCONFIRMED', 'synthetic') })
  const ri = await identity.execute({})
  assert.equal(ri.code, 'IDENTITY_UNCONFIRMED')
  assert.equal(identity.driverCalls(), 0)

  const probeFail = makeHarness({ state: db, probeErr: new CodedOperationError('NOT_LOGGED_IN', 'synthetic') })
  const rp = await probeFail.execute({})
  assert.equal(rp.code, 'NOT_LOGGED_IN')
  assert.equal(probeFail.driverCalls(), 0)
})

test('强制 local-db：失败同样结构化 db_unavailable（强制模式不落 UI）', async () => {
  const db: LocalDbState = { mode: 'db', version: '5.0.11.6029', checked_at: 'e' }
  const h = makeHarness({ state: db, watchDbErr: new CodedOperationError('LOCALDB_UNAVAILABLE', 'synthetic') })
  const r = await h.execute({ channel: 'local-db' })
  assert.equal(r.success, false)
  assert.equal(r.code, 'LOCALDB_UNAVAILABLE')
  assert.equal(r.data.db_unavailable, true)
  assert.deepEqual(h.dbCalls, [{ forced: true }])
  assert.equal(h.driverCalls(), 0)
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
  assert.equal(h.dbCalls.length, 0)
})
