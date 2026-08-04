import assert from 'node:assert/strict'
import test from 'node:test'
import os from 'node:os'
import path from 'node:path'
import fs from 'node:fs'
import BetterSqliteDatabase from 'better-sqlite3'
import { runMigrations } from '../db/migrations.js'
import { ALLOWED_METHODS } from '../src/main/cdp/methodPolicy.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { makeFingerprint } from '../src/main/boss/ListSnapshotParser.js'
import { ActionStore } from '../src/main/actions/ActionStore.js'
import { PageActionExecutor, type ExecuteInput } from '../src/main/actions/PageActionExecutor.js'

/** 构造单 document 的 DOMSnapshot：每个 item 一个有布局的文本节点 */
function snapWith(items: Array<{ text: string; bounds: [number, number, number, number] }>): DomSnapshot {
  const strings: string[] = []
  const nodeValue: number[] = []
  const layoutNodeIndex: number[] = []
  const boundsArr: number[][] = []
  items.forEach((it, nodeIdx) => {
    let si = strings.findIndex((s) => s === it.text)
    if (si < 0) {
      strings.push(it.text)
      si = strings.length - 1
    }
    nodeValue.push(si)
    layoutNodeIndex.push(nodeIdx)
    boundsArr.push([...it.bounds])
  })
  return {
    strings,
    documents: [
      { nodes: { nodeValue, contentDocumentIndex: [] }, layout: { nodeIndex: layoutNodeIndex, bounds: boundsArr } },
    ],
  }
}

type SnapItem = { text: string; bounds: [number, number, number, number] }
const CANDIDATE: SnapItem = { text: '张三', bounds: [100, 200, 60, 24] }
const GREET_BTN: SnapItem = { text: '打招呼', bounds: [400, 400, 80, 32] }
const REJECT_BTN: SnapItem = { text: '不合适', bounds: [400, 400, 80, 32] }
const REASON_OPT: SnapItem = { text: '经验不匹配', bounds: [300, 500, 120, 32] }

/** CDP stub：记录所有方法调用，按队列返回 snapshot */
class StubGateway {
  calls: Array<{ method: string; params?: Record<string, unknown> }> = []
  snapshotQueue: DomSnapshot[] = []
  lastSnapshot?: DomSnapshot
  failNextDispatch = false
  failOnReleased = false

  async captureDomSnapshot(): Promise<unknown> {
    this.calls.push({ method: 'DOMSnapshot.captureSnapshot' })
    const s = this.snapshotQueue.shift() ?? this.lastSnapshot
    if (!s) throw new Error('no snapshot stubbed')
    this.lastSnapshot = s
    return s
  }
  async captureScreenshot(): Promise<string> {
    this.calls.push({ method: 'Page.captureScreenshot' })
    return Buffer.from('fake-png').toString('base64')
  }
  async dispatchMouse(opts: Record<string, unknown>): Promise<void> {
    if (this.failNextDispatch) {
      this.failNextDispatch = false
      throw new Error('cdp disconnected')
    }
    if (this.failOnReleased && opts.type === 'mouseReleased') {
      this.failOnReleased = false
      throw new Error('cdp disconnected')
    }
    this.calls.push({ method: 'Input.dispatchMouseEvent', params: opts })
  }
  inputCalls(): Array<{ method: string; params?: Record<string, unknown> }> {
    return this.calls.filter((c) => c.method.startsWith('Input.'))
  }
  assertWhitelist(): void {
    for (const c of this.calls) {
      assert.ok(ALLOWED_METHODS.has(c.method), `method must be whitelisted: ${c.method}`)
      assert.ok(!c.method.startsWith('Runtime.'), `Runtime.* forbidden: ${c.method}`)
    }
  }
}

function tempDb(): { db: BetterSqliteDatabase.Database; dbPath: string } {
  const dbPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'boss-exec-')), 't.db')
  const db = new BetterSqliteDatabase(dbPath)
  runMigrations(db)
  return { db, dbPath }
}

const KEY = 'test-fp-key'
const VIEWPORT = { width: 1000, height: 800 }

function makeInput(overrides: Partial<ExecuteInput> = {}): ExecuteInput {
  return {
    sessionId: 1,
    candidateId: null,
    candidateName: '张三',
    fingerprintKey: KEY,
    fingerprintContext: '',
    evalCandidateFingerprint: makeFingerprint(KEY, '', '张三'),
    conclusion: 'QUALIFIED',
    evalReason: 'all hard rules passed',
    viewport: VIEWPORT,
    ...overrides,
  }
}

function makeExecutor(db: BetterSqliteDatabase.Database, gw: StubGateway) {
  const store = new ActionStore(db)
  const saved: Array<{ tag: string; path: string }> = []
  const executor = new PageActionExecutor({
    store,
    gateway: gw,
    confirmWaitMs: 1,
    saveScreenshot: (_buf, tag) => {
      const p = `/tmp/shot-${tag}.png`
      saved.push({ tag, path: p })
      return p
    },
  })
  return { store, executor, saved }
}

test('GREET 全流程：PLANNED→SENT→CONFIRMED，前后截图存证，只发白名单方法', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [
    snapWith([CANDIDATE, GREET_BTN]), // 校验 + 规划
    snapWith([CANDIDATE, GREET_BTN]), // 点击前重新定位
    snapWith([CANDIDATE, { text: '继续沟通', bounds: [400, 400, 96, 32] } as SnapItem]), // 确认：打招呼消失
  ]
  const { store, executor, saved } = makeExecutor(db, gw)
  const outcome = await executor.execute(makeInput())

  assert.equal(outcome.status, 'CONFIRMED')
  const record = store.getById(outcome.recordId!)!
  assert.equal(record.status, 'CONFIRMED')
  assert.equal(record.uniqueKey, `${makeFingerprint(KEY, '', '张三')}|GREET`)
  assert.ok(record.sentAt && record.confirmedAt)
  assert.equal(record.beforeScreenshotPath, '/tmp/shot-before.png')
  assert.equal(record.afterScreenshotPath, '/tmp/shot-after.png')
  assert.deepEqual(saved.map((s) => s.tag), ['before', 'after'])

  // 恰好一次点击（pressed + released），坐标为按钮中心
  const inputs = gw.inputCalls()
  assert.equal(inputs.length, 2)
  assert.equal(inputs[0]!.params!.type, 'mousePressed')
  assert.equal(inputs[1]!.params!.type, 'mouseReleased')
  assert.equal(inputs[0]!.params!.x, 440)
  assert.equal(inputs[0]!.params!.y, 416)
  gw.assertWhitelist()
  db.close()
})

test('GREET 结果无法确认 → UNKNOWN；再次执行幂等跳过且不发任何 CDP', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [
    snapWith([CANDIDATE, GREET_BTN]),
    snapWith([CANDIDATE, GREET_BTN]),
    snapWith([CANDIDATE, GREET_BTN]), // 确认时按钮仍在 → UNKNOWN
  ]
  const { store, executor } = makeExecutor(db, gw)
  const first = await executor.execute(makeInput())
  assert.equal(first.status, 'UNKNOWN')
  assert.equal(store.getById(first.recordId!)!.status, 'UNKNOWN')

  const callsBefore = gw.calls.length
  const second = await executor.execute(makeInput())
  assert.equal(second.status, 'IDEMPOTENT_SKIP')
  assert.equal(gw.calls.length, callsBefore, 'UNKNOWN 重试不得产生新的 CDP 调用')
  assert.equal(gw.inputCalls().length, 2, '不应再次点击')
  gw.assertWhitelist()
  db.close()
})

test('UNCERTAIN → NO_ACTION，零 CDP 调用', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  const { executor } = makeExecutor(db, gw)
  const outcome = await executor.execute(makeInput({ conclusion: 'UNCERTAIN' }))
  assert.equal(outcome.status, 'NO_ACTION')
  assert.equal(gw.calls.length, 0)
  const count = db.prepare('SELECT COUNT(*) AS c FROM actions').get() as { c: number }
  assert.equal(count.c, 0, 'UNCERTAIN 不落 actions 表')
  db.close()
})

test('指纹不一致 → FAILED，不点击', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [snapWith([CANDIDATE, GREET_BTN])]
  const { executor } = makeExecutor(db, gw)
  const outcome = await executor.execute(
    makeInput({ evalCandidateFingerprint: 'fp-from-other-candidate' }),
  )
  assert.equal(outcome.status, 'FAILED')
  assert.match(outcome.error!, /指纹/)
  assert.equal(gw.inputCalls().length, 0)
  gw.assertWhitelist()
  db.close()
})

test('详情中找不到候选人 → FAILED，不点击', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [snapWith([{ text: '李四', bounds: [100, 200, 60, 24] } as SnapItem, GREET_BTN])]
  const { executor } = makeExecutor(db, gw)
  const outcome = await executor.execute(makeInput())
  assert.equal(outcome.status, 'FAILED')
  assert.match(outcome.error!, /未找到候选人/)
  assert.equal(gw.inputCalls().length, 0)
  db.close()
})

test('执行时按钮歧义 → FAILED（记录落库），不点击', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [
    snapWith([CANDIDATE, GREET_BTN]), // 规划时唯一
    snapWith([CANDIDATE, GREET_BTN, { text: '打招呼', bounds: [600, 400, 80, 32] } as SnapItem]), // 执行时变歧义
  ]
  const { store, executor } = makeExecutor(db, gw)
  const outcome = await executor.execute(makeInput())
  assert.equal(outcome.status, 'FAILED')
  assert.match(outcome.error!, /按钮定位失败/)
  assert.equal(store.getById(outcome.recordId!)!.status, 'FAILED')
  assert.equal(gw.inputCalls().length, 0)
  gw.assertWhitelist()
  db.close()
})

test('点击发送后 CDP 断开 → UNKNOWN（已发出不可知），不标 FAILED', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  // 点击后发一次 captureDomSnapshot 抛错：用 failNextDispatch 不行（dispatch 已成功），
  // 这里让确认阶段 snapshot 队列为空且 lastSnapshot 抛错不可控——改用空队列 + 清掉 lastSnapshot。
  gw.snapshotQueue = [snapWith([CANDIDATE, GREET_BTN]), snapWith([CANDIDATE, GREET_BTN])]
  const { store, executor } = makeExecutor(db, gw)
  // 偷换：确认阶段 captureDomSnapshot 抛错
  let n = 0
  const orig = gw.captureDomSnapshot.bind(gw)
  gw.captureDomSnapshot = async () => {
    n++
    if (n >= 3) throw new Error('cdp disconnected')
    return orig()
  }
  const outcome = await executor.execute(makeInput())
  assert.equal(outcome.status, 'UNKNOWN')
  assert.equal(store.getById(outcome.recordId!)!.status, 'UNKNOWN')
  assert.equal(gw.inputCalls().length, 2, '点击已发出')
  gw.assertWhitelist()
  db.close()
})

test('mousePressed 成功后 mouseReleased 失败 → UNKNOWN（输入可能已送达，禁止 FAILED 重试）', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [snapWith([CANDIDATE, GREET_BTN]), snapWith([CANDIDATE, GREET_BTN])]
  gw.failOnReleased = true
  const { store, executor } = makeExecutor(db, gw)
  const outcome = await executor.execute(makeInput())
  assert.equal(outcome.status, 'UNKNOWN', 'pressed 已发出后 released 失败必须 UNKNOWN，不能标 FAILED')
  assert.equal(store.getById(outcome.recordId!)!.status, 'UNKNOWN')
  assert.equal(gw.inputCalls().length, 1, '只有 mousePressed 送达')

  // 再次执行：UNKNOWN 幂等拦截，零新点击（防止重复打招呼）
  const second = await executor.execute(makeInput())
  assert.equal(second.status, 'IDEMPOTENT_SKIP')
  assert.equal(gw.inputCalls().length, 1, '不得重发点击')
  gw.assertWhitelist()
  db.close()
})

test('REJECT 全流程：点不合适 → 原因弹层选映射原因 → CONFIRMED', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [
    snapWith([CANDIDATE, REJECT_BTN]), // 校验 + 规划
    snapWith([CANDIDATE, REJECT_BTN]), // 点击前定位
    snapWith([CANDIDATE, REJECT_BTN, REASON_OPT]), // 原因弹层出现
    snapWith([CANDIDATE]), // 确认：按钮与原因都消失
  ]
  const { store, executor } = makeExecutor(db, gw)
  const outcome = await executor.execute(
    makeInput({ conclusion: 'REJECTED', evalReason: '经验不足3年' }),
  )
  assert.equal(outcome.status, 'CONFIRMED')
  assert.equal(store.getById(outcome.recordId!)!.status, 'CONFIRMED')

  const inputs = gw.inputCalls()
  assert.equal(inputs.length, 4, '两次点击（不合适 + 原因）')
  // 第二次点击坐标为原因选项中心 (300+60, 500+16)
  assert.equal(inputs[2]!.params!.x, 360)
  assert.equal(inputs[2]!.params!.y, 516)
  gw.assertWhitelist()
  db.close()
})

test('REJECT 原因弹层定位失败 → UNKNOWN，不重试', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [
    snapWith([CANDIDATE, REJECT_BTN]),
    snapWith([CANDIDATE, REJECT_BTN]),
    snapWith([CANDIDATE, REJECT_BTN]), // 弹层未出现（无原因选项）
  ]
  const { store, executor } = makeExecutor(db, gw)
  const outcome = await executor.execute(
    makeInput({ conclusion: 'REJECTED', evalReason: '经验不足3年' }),
  )
  assert.equal(outcome.status, 'UNKNOWN')
  assert.match(outcome.error!, /原因弹层/)
  assert.equal(store.getById(outcome.recordId!)!.status, 'UNKNOWN')
  assert.equal(gw.inputCalls().length, 2, '只点了不合适，没乱选原因')
  gw.assertWhitelist()
  db.close()
})

test('REJECT 理由无法映射 → NO_ACTION，不点击', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [snapWith([CANDIDATE, REJECT_BTN])]
  const { executor } = makeExecutor(db, gw)
  const outcome = await executor.execute(
    makeInput({ conclusion: 'REJECTED', evalReason: '无法归类的综合考量' }),
  )
  assert.equal(outcome.status, 'NO_ACTION')
  assert.equal(gw.inputCalls().length, 0)
  gw.assertWhitelist()
  db.close()
})

test('会话上限拦截：达到上限 → NO_ACTION，不点击', async () => {
  const { db } = tempDb()
  const gw = new StubGateway()
  gw.snapshotQueue = [snapWith([CANDIDATE, GREET_BTN])]
  const store = new ActionStore(db)
  // 预置一条本会话已发出动作
  const { record } = store.getOrInsertPlanned({
    candidateId: null,
    sessionId: 1,
    action: 'GREET',
    reason: 'r',
    uniqueKey: 'other-fp|GREET',
  })
  store.markSent(record.id)
  const executor = new PageActionExecutor({ store, gateway: gw, confirmWaitMs: 1 })
  const outcome = await executor.execute(makeInput({ limits: { perSession: 1 } }))
  assert.equal(outcome.status, 'NO_ACTION')
  assert.match(outcome.blockedReason!, /会话动作上限/)
  assert.equal(gw.inputCalls().length, 0)
  db.close()
})

test('重启恢复：崩溃残留 PLANNED 重新走完整前置校验，不重复插行，坐标全部来自 fresh snapshot', async () => {
  const { db, dbPath } = tempDb()
  // 第一次进程：只落了 PLANNED 就"崩溃"
  const store1 = new ActionStore(db)
  const fp = makeFingerprint(KEY, '', '张三')
  store1.getOrInsertPlanned({
    candidateId: null,
    sessionId: 1,
    action: 'GREET',
    reason: 'r',
    uniqueKey: `${fp}|GREET`,
  })
  db.close()

  // 第二次进程：重开同一 DB 文件，重建 store
  const db2 = new BetterSqliteDatabase(dbPath)
  const store2 = new ActionStore(db2)
  assert.equal(store2.listPlanned().length, 1, '恢复时应看到崩溃残留的 PLANNED')

  const gw = new StubGateway()
  const buttonSnap = () => snapWith([CANDIDATE, { text: '打招呼', bounds: [700, 600, 80, 32] }])
  gw.snapshotQueue = [buttonSnap(), buttonSnap(), snapWith([CANDIDATE])]
  const executor = new PageActionExecutor({ store: store2, gateway: gw, confirmWaitMs: 1 })
  const outcome = await executor.execute(makeInput())

  assert.equal(outcome.status, 'CONFIRMED')
  const rows = db2.prepare('SELECT COUNT(*) AS c FROM actions').all() as { c: number }[]
  assert.equal(rows[0]!.c, 1, '复用残留行，不重复插入')
  const inputs = gw.inputCalls()
  assert.equal(inputs.length, 2)
  // 坐标来自本次 fresh snapshot（按钮在 700,600），证明未复用旧坐标
  assert.equal(inputs[0]!.params!.x, 740)
  assert.equal(inputs[0]!.params!.y, 616)
  gw.assertWhitelist()
  db2.close()
})

test('SENT 记录（崩溃在发送后）恢复时不重发 → IDEMPOTENT_SKIP，零 CDP 调用', async () => {
  const { db } = tempDb()
  const store = new ActionStore(db)
  const fp = makeFingerprint(KEY, '', '张三')
  const { record } = store.getOrInsertPlanned({
    candidateId: null,
    sessionId: 1,
    action: 'GREET',
    reason: 'r',
    uniqueKey: `${fp}|GREET`,
  })
  store.markSent(record.id)

  const gw = new StubGateway()
  const executor = new PageActionExecutor({ store, gateway: gw, confirmWaitMs: 1 })
  const outcome = await executor.execute(makeInput())
  assert.equal(outcome.status, 'IDEMPOTENT_SKIP')
  assert.equal(gw.calls.length, 0, 'SENT 记录重发是双重打招呼风险，必须零 CDP 调用')
  db.close()
})
