import assert from 'node:assert/strict'
import test from 'node:test'
import BetterSqliteDatabase from 'better-sqlite3'
import type { Database as BetterSqliteDatabaseType } from 'better-sqlite3'
import { runMigrations } from '../db/migrations.js'
import type { DomSnapshot } from '../src/main/boss/domSnapshot.js'
import { makeFingerprint } from '../src/main/boss/ListSnapshotParser.js'
import { encodePng } from '../src/main/image/LongScreenshotStitcher.js'
import type { OcrProvider } from '../src/main/ocr/OcrProvider.js'
import { ReviewStore } from '../src/main/storage/reviewStore.js'
import {
  ScreeningSession,
  type SessionJobConfig,
  type SessionEvent,
  type ScreeningSessionDeps,
} from '../src/main/workflow/ScreeningSession.js'

// ===== 测试工具 =====

/** 单 document snapshot：每个 item 一个有布局的文本节点 */
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

const NAME = { text: '张三', bounds: [100, 200, 60, 24] as [number, number, number, number] }
const NAME2 = { text: '李四', bounds: [100, 300, 60, 24] as [number, number, number, number] }
const GREET_BTN = { text: '打招呼', bounds: [400, 400, 80, 32] as [number, number, number, number] }
// 列表卡片必须有年龄行「\d+岁」（姓名下方同列），与真实推荐页结构一致（candidateEnumerator 结构信号要求）
const CARD1_INFO = { text: '31岁', bounds: [100, 240, 60, 24] as [number, number, number, number] }
const CARD2_INFO = { text: '38岁', bounds: [100, 340, 60, 24] as [number, number, number, number] }

const LIST = snapWith([NAME, CARD1_INFO])
const LIST_TWO = snapWith([NAME, CARD1_INFO, NAME2, CARD2_INFO])
const DETAIL_BTN = snapWith([NAME, GREET_BTN])
const DETAIL_NOBTN = snapWith([NAME])
const DETAIL2_BTN = snapWith([NAME2, GREET_BTN])
const DETAIL2_NOBTN = snapWith([NAME2])

/** 100x100 纯黑 PNG（截图 stub 用，尺寸一致 → MSE=0 → 稳定/到底/拼接全部顺利通过） */
const BLACK_PNG_BASE64 = encodePng(100, 100, Buffer.alloc(100 * 100 * 4)).toString('base64')

class StubGateway {
  snapshotQueue: DomSnapshot[] = []
  lastSnapshot?: DomSnapshot
  mouseCalls: Array<{ type: string; x: number; y: number }> = []
  keyCalls: string[] = []

  async captureDomSnapshot(): Promise<unknown> {
    const s = this.snapshotQueue.shift() ?? this.lastSnapshot
    if (!s) throw new Error('no snapshot stubbed')
    this.lastSnapshot = s
    return s
  }
  async captureScreenshot(): Promise<string> {
    return BLACK_PNG_BASE64
  }
  async dispatchMouse(opts: { type: string; x: number; y: number }): Promise<void> {
    this.mouseCalls.push(opts)
  }
  async dispatchKey(opts: { type: string }): Promise<void> {
    this.keyCalls.push(opts.type)
  }
}

const stubOcrProvider: OcrProvider = {
  name: 'stub-ocr',
  isAvailable: async () => true,
  recognize: async () => ({
    blocks: [{ text: '张三 Vue 开发经验五年', confidence: 0.9, box: [0, 0, 100, 20], pageIndex: 0 }],
  }),
}

const JOB: SessionJobConfig = {
  id: 1,
  name: '测试岗位',
  hardRules: JSON.stringify({ requiredSkills: ['Vue'] }),
  knowledgeVersion: 'v1',
  ruleVersion: 'v1',
  actionLimitSession: null,
  actionLimitDay: null,
}

function freshDb() {
  const db = new BetterSqliteDatabase(':memory:')
  runMigrations(db)
  return db
}

function makeDeps(
  db: BetterSqliteDatabaseType,
  gateway: StubGateway,
  events: SessionEvent[],
  overrides: Partial<ScreeningSessionDeps> = {},
): ScreeningSessionDeps {
  return {
    db,
    connect: async () => gateway,
    ocrProvider: stubOcrProvider,
    viewport: { width: 1280, height: 800 },
    fingerprintKey: 'test-key',
    saveCapture: (kind) => `/tmp/captures/${kind}.png`,
    emit: (e) => events.push(e),
    settleMs: 0,
    maxListScrollRounds: 1,
    ...overrides,
  }
}

async function waitFor(cond: () => boolean, timeoutMs = 20000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (!cond()) {
    if (Date.now() > deadline) throw new Error('waitFor timeout')
    await new Promise((r) => setTimeout(r, 25))
  }
}

async function startAndWait(session: ScreeningSession, states: string[]): Promise<void> {
  session.start(JOB)
  await waitFor(() => states.includes(session.getStatus().state))
}

// ===== 用例 =====

test('完整流程：QUALIFIED 候选人走全状态机并 GREET CONFIRMED，每步落库', async () => {
  const db = freshDb()
  const gateway = new StubGateway()
  const events: SessionEvent[] = []
  // 快照序列：列表 → 定位 → 详情验证 → 执行器 fresh → 重定位 → 确认(按钮消失)
  gateway.snapshotQueue = [LIST, LIST, DETAIL_BTN, DETAIL_BTN, DETAIL_BTN, DETAIL_NOBTN]
  const session = new ScreeningSession(makeDeps(db, gateway, events))

  session.markChromeLaunched()
  const confirmed = await session.confirmLoginReady()
  assert.equal(confirmed.state, 'CONNECTING_CDP')
  assert.equal(confirmed.connected, true)

  await startAndWait(session, ['COMPLETED'])

  // 状态机事件覆盖关键状态
  const stateSeq = events.filter((e) => e.type === 'state').map((e) => e.state)
  for (const s of ['READING_LIST', 'OPENING_DETAIL', 'CAPTURING_DETAIL', 'OCR_AND_NORMALIZE', 'SCREENING', 'EXECUTING_ACTION', 'CLOSING_DETAIL', 'CHECKPOINT']) {
    assert.ok(stateSeq.includes(s as never), `缺少状态 ${s}`)
  }

  // 落库：resume_views OCR_NORMALIZED / captures long / evaluations QUALIFIED / actions GREET CONFIRMED
  const view = db.prepare('SELECT source FROM resume_views').get() as { source: string }
  assert.equal(view.source, 'OCR_NORMALIZED')
  const capture = db.prepare("SELECT kind, integrity_status FROM captures WHERE kind = 'long'").get() as {
    kind: string
    integrity_status: string
  }
  assert.equal(capture.integrity_status, 'CONTINUOUS')
  const evaluation = db.prepare('SELECT conclusion FROM evaluations').get() as { conclusion: string }
  assert.equal(evaluation.conclusion, 'QUALIFIED')
  const action = db.prepare("SELECT action, status FROM actions WHERE action = 'GREET'").get() as {
    action: string
    status: string
  }
  assert.equal(action.status, 'CONFIRMED')

  // 详情已关闭（Escape rawKeyDown+keyUp）
  assert.ok(gateway.keyCalls.includes('rawKeyDown'))
  assert.ok(gateway.keyCalls.includes('keyUp'))
  db.close()
})

test('指纹去重：已评估候选人不再打开详情（恢复不重处理）', async () => {
  const db = freshDb()
  // 预置：候选人 + 评估（模拟上次运行/崩溃前的结果）
  const fp = makeFingerprint('test-key', 'job:1', '张三')
  db.prepare("INSERT INTO candidates (job_id, fingerprint, list_summary) VALUES (1, ?, '{\"name\":\"张三\"}')").run(fp)
  db.prepare("INSERT INTO evaluations (candidate_id, conclusion, reason) VALUES (1, 'QUALIFIED', '已通过')").run()

  const gateway = new StubGateway()
  gateway.snapshotQueue = [LIST]
  const session = new ScreeningSession(makeDeps(db, gateway, []))
  session.markChromeLaunched()
  await session.confirmLoginReady()
  await startAndWait(session, ['COMPLETED'])

  // 没有任何点击（连卡片打开都没有），评估不重复
  assert.equal(gateway.mouseCalls.filter((m) => m.type === 'mousePressed').length, 0)
  // 列表滚动必须用 CDP mouseWheel（deltaY 仅对 mouseWheel 生效；mouseMoved+delta 不滚动）
  assert.ok(gateway.mouseCalls.some((m) => m.type === 'mouseWheel'), '无新候选人时应通过 mouseWheel 滚动列表')
  const count = db.prepare('SELECT COUNT(*) AS c FROM evaluations').get() as { c: number }
  assert.equal(count.c, 1)
  db.close()
})

test('UNCERTAIN → WAITING_REVIEW 进复核队列，不执行动作', async () => {
  const db = freshDb()
  const gateway = new StubGateway()
  // city 规则无法判定（fields 为空）+ 无 LLM → UNCERTAIN
  const job: SessionJobConfig = { ...JOB, hardRules: JSON.stringify({ city: ['北京'] }) }
  gateway.snapshotQueue = [LIST, LIST, DETAIL_NOBTN]
  const session = new ScreeningSession(makeDeps(db, gateway, []))

  session.markChromeLaunched()
  await session.confirmLoginReady()
  session.start(job)
  await waitFor(() => session.getStatus().state === 'COMPLETED')

  const evaluation = db.prepare('SELECT conclusion FROM evaluations').get() as { conclusion: string }
  assert.equal(evaluation.conclusion, 'UNCERTAIN')
  assert.equal((db.prepare('SELECT COUNT(*) AS c FROM actions').get() as { c: number }).c, 0)
  assert.equal(new ReviewStore(db).list().length, 1)
  db.close()
})

test('付费简历：详情命中解锁弹层标记 → 落库 PAYWALL_LOCKED 后跳过，不截图/OCR/筛选，且后续运行不重试', async () => {
  const db = freshDb()
  const gateway = new StubGateway()
  // 详情快照含「直豆」解锁标记（真机语料 2026-08-04）
  const DETAIL_PAYWALL = snapWith([NAME, { text: '直豆', bounds: [300, 300, 60, 24] }])
  gateway.snapshotQueue = [LIST, LIST, DETAIL_PAYWALL]
  const session = new ScreeningSession(makeDeps(db, gateway, []))

  session.markChromeLaunched()
  await session.confirmLoginReady()
  await startAndWait(session, ['COMPLETED'])

  // 落库 PAYWALL_LOCKED 标记行，无评估、无动作
  const rv = db.prepare("SELECT source FROM resume_views").get() as { source: string }
  assert.equal(rv.source, 'PAYWALL_LOCKED')
  assert.equal((db.prepare('SELECT COUNT(*) AS c FROM evaluations').get() as { c: number }).c, 0)
  assert.equal((db.prepare('SELECT COUNT(*) AS c FROM actions').get() as { c: number }).c, 0)
  // 详情已用 Escape 关闭
  assert.ok(gateway.keyCalls.length >= 1)
  db.close()
})


test('DOMSnapshot 无法唯一定位 → PAUSED，会话行同步 PAUSED', async () => {
  const db = freshDb()
  const gateway = new StubGateway()
  // 两个同名节点：枚举去重为一个名字，但 locate 发现 2 个可见匹配 → UNLOCATABLE
  const dup = snapWith([
    { text: '张三', bounds: [100, 200, 60, 24] },
    { text: '31岁', bounds: [100, 240, 60, 24] },
    { text: '张三', bounds: [100, 400, 60, 24] },
    { text: '32岁', bounds: [100, 440, 60, 24] },
  ])
  gateway.snapshotQueue = [dup, dup]
  const session = new ScreeningSession(makeDeps(db, gateway, []))

  session.markChromeLaunched()
  await session.confirmLoginReady()
  await startAndWait(session, ['PAUSED'])

  const status = session.getStatus()
  assert.equal(status.state, 'PAUSED')
  assert.match(status.pauseReason ?? '', /无法唯一定位/)
  const row = db.prepare('SELECT status FROM sessions').get() as { status: string }
  assert.equal(row.status, 'PAUSED')
  db.close()
})

test('OCR 未配置 → PAUSED（fail-loud，不静默降级）', async () => {
  const db = freshDb()
  const gateway = new StubGateway()
  gateway.snapshotQueue = [LIST, LIST, DETAIL_NOBTN]
  const session = new ScreeningSession(makeDeps(db, gateway, [], { ocrProvider: null }))

  session.markChromeLaunched()
  await session.confirmLoginReady()
  await startAndWait(session, ['PAUSED'])
  assert.match(session.getStatus().pauseReason ?? '', /OCR/)
  db.close()
})

test('暂停/恢复：候选人边界生效，恢复后指纹对齐继续处理剩余候选人', async () => {
  const db = freshDb()
  const gateway = new StubGateway()
  const events: SessionEvent[] = []
  gateway.snapshotQueue = [
    LIST_TWO, // READING_LIST
    LIST_TWO, // OPENING 张三
    DETAIL_BTN,
    DETAIL_BTN,
    DETAIL_BTN,
    DETAIL_NOBTN, // 张三 CONFIRMED
    LIST_TWO, // resume 后 READING_LIST
    LIST_TWO, // OPENING 李四
    DETAIL2_BTN,
    DETAIL2_BTN,
    DETAIL2_BTN,
    DETAIL2_NOBTN, // 李四 CONFIRMED
    LIST_TWO, // 复查列表（全部已评估）
  ]
  const session = new ScreeningSession(makeDeps(db, gateway, events))

  session.markChromeLaunched()
  await session.confirmLoginReady()
  session.start(JOB)

  // 张三出结论后立即请求暂停（边界生效：张三动作仍会执行完）
  await waitFor(() => events.some((e) => e.type === 'candidate' && e.candidateName === '张三'))
  session.pause()
  await waitFor(() => session.getStatus().state === 'PAUSED')
  assert.equal((db.prepare('SELECT COUNT(*) AS c FROM evaluations').get() as { c: number }).c, 1)

  session.resume()
  await waitFor(() => session.getStatus().state === 'COMPLETED')
  assert.equal((db.prepare('SELECT COUNT(*) AS c FROM evaluations').get() as { c: number }).c, 2)
  // 两个候选人都有 CONFIRMED 动作
  assert.equal(
    (db.prepare("SELECT COUNT(*) AS c FROM actions WHERE status = 'CONFIRMED'").get() as { c: number }).c,
    2,
  )
  db.close()
})

test('登录等待期 Chrome 退出：可复位回 IDLE 重新启动（不留死局）', async () => {
  const db = freshDb()
  const gateway = new StubGateway()
  const session = new ScreeningSession(makeDeps(db, gateway, []))
  session.markChromeLaunched()
  assert.equal(session.getStatus().state, 'WAITING_MANUAL_LOGIN')
  // Chrome 在扫码前被用户关掉：必须能复位重来，否则 UI 无任何恢复入口
  const status = session.reset()
  assert.equal(status.state, 'IDLE')
  assert.equal(status.connected, false)
  db.close()
})

test('每日动作上限拦截：超限 → NO_ACTION，不发点击但流程继续', async () => {
  const db = freshDb()
  // 预置今日已有 1 个 SENT 动作；岗位日上限 1 → 新动作被拦截
  db.prepare("INSERT INTO candidates (job_id, fingerprint, list_summary) VALUES (1, 'fp-x', '{}')").run()
  db.prepare(
    "INSERT INTO actions (candidate_id, action, status, unique_key, sent_at) VALUES (1, 'GREET', 'SENT', 'fp-x|GREET', CURRENT_TIMESTAMP)",
  ).run()

  const gateway = new StubGateway()
  gateway.snapshotQueue = [LIST, LIST, DETAIL_BTN, DETAIL_BTN]
  const job: SessionJobConfig = { ...JOB, actionLimitDay: 1 }
  const session = new ScreeningSession(makeDeps(db, gateway, []))

  session.markChromeLaunched()
  await session.confirmLoginReady()
  session.start(job)
  await waitFor(() => session.getStatus().state === 'COMPLETED')

  // 评估正常落库，但没有新动作记录（只有预置的那条）
  assert.equal((db.prepare('SELECT COUNT(*) AS c FROM evaluations').get() as { c: number }).c, 1)
  assert.equal((db.prepare('SELECT COUNT(*) AS c FROM actions').get() as { c: number }).c, 1)
  db.close()
})
