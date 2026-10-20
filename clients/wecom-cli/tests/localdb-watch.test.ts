/**
 * P2 任务 3.2 本地库 watch 执行器整链（真实三库 + 独立加密 + Python 驱动；对齐
 * localdb-unread.test.ts 合成范式）+ DB 水位存储（watchDbState）单元。
 *
 * 覆盖：首轮基线建立（只报之后）/ 增量与水位推进（只到已返回消息）/ 水位不推进（无新消息
 * 不产生事件）/ last_sender_state 双向透传（py 按 last_participating 判）/ limit 截断
 * has_more / focus 逐名解析与部分失败 / 账号切换全量重置基线 / 版本准入 forced /
 * 身份映射失败 IDENTITY_UNCONFIRMED / 取消透传 / watchDbState 容错（损坏/非法条目/账号变更）。
 * 第八轮评审修复：显式关注空会话水位存 0（首批消息全量上报）/ 源库代际比对（同账号换库、
 * 缺 generation、水位回退 → generation_changed + 基线重建）/ focus 持久绑定（改名不影响
 * 跟踪、ref 输入真实签名验证与账号核对、输出携带绑定时显示名）。
 * 第九轮评审修复：水位锚点（py watch 锚点有效性校验——同文件重写/水位行删除 → 全量重报 +
 * watermark_rebuilt/watermark_reset 标注；正常轮不触发）/ focus ref 身份约束（legacy ref
 * 无账号拒绝、contact/group 类型过滤不绑错、绑定条目 type 存证）。
 * 第十轮评审修复：重建（watermark_reset）路径允许水位回退写入——limit 截断下重写后较小
 * 水位可写入新基线，跨轮依次推进 [1,2]/[3,4]/[5,6]（回归：旧实现每轮重复第一页）；
 * 正常增量路径「不回退」约束不变（伪造 py 回退响应整体不采纳——水位与锚点都不动）。
 * 全量模式（未传 focus）首见空会话不建 0 基线：首条消息落库后按当前 max 建基线（存量不回放）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import {
  runLocaldbWatch,
  parseWatchResponse,
  type LocaldbWatchConversation,
  type LocaldbWatchDeps,
} from '../src/platform/localdb/watch.js'
import {
  loadLocaldbWatchState,
  localdbWatchStatePath,
  saveLocaldbWatchState,
} from '../src/platform/localdb/watchDbState.js'
import { type LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import { writeLocalState } from '../src/platform/localdb/matrix.js'
import { createTargetRef, verifyTargetRef } from '../src/platform/targetRef.js'
import { runLocaldbPy, type RunLocaldbPyOpts } from '../src/platform/localdb/driver.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const helper = join(root, 'tests', 'py', 'read_session_fixture.py')
const SELF = '9007199254740997'
const PEER = '9007199254740999'
const KEY = '000102030405060708090a0b0c0d0e0f' // 合成密钥，不接触真实 keystore。
const VERSION = '5.0.11.6029'
const CONV = `S:${SELF}_${PEER}`
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const windows = { skip: process.platform !== 'win32' }

interface Fixture {
  base: string
  current: CurrentAccount
  deps: LocaldbReadDeps
  stateDir: string
  watchStatePath: string
  command(mode: string, count?: number): void
  close(): void
}

async function fixture(setup = 'unread-setup', count = 0): Promise<Fixture> {
  const base = mkdtempSync(join(tmpdir(), 'wecom-p2-watch-'))
  execFileSync(python, [helper, base, setup, String(count)], { windowsHide: true })
  const data = await locateDataDir({ cefLogDir: null, candidateRoots: [],
    activeAccount: { pid: 4242, message_db: join(base, SELF, 'Data', 'message.db') } })
  const current: CurrentAccount = { account: SELF, windowPid: 4242, dataDir: data }
  const stateDir = join(base, 'state')
  writeLocalState({ mode: 'db', version: VERSION, checked_at: 'fixture-epoch' }, stateDir)
  const deps: LocaldbReadDeps = {
    probeAccountFn: async () => current,
    detectInstallFn: async opts => ({ installed: true, pid: opts?.processPid ?? 4242,
      exe_path: 'C:\\synthetic\\WXWork.exe', install_dir: 'C:\\synthetic', version: VERSION,
      version_source: 'process', process_started_at: null, upgrade_signals: [] }),
    loadMatrixFn: () => ({ entries: { [VERSION]: { status: 'validated', algorithm: 'wxaes128cbc-nonlegacy',
      page_size: 4096, db_magic: readFileSync(data.db_paths!.message!).subarray(0, 8).toString('hex') } } }),
    loadKeyFn: async () => ({ keyHex: KEY, savedAt: 'fixture' }),
    stateDir, keyDir: stateDir, workBaseDir: join(base, 'work'), logFn: () => true,
    now: () => 1700100000000, snapshotRetryMs: 1,
  }
  const watchStatePath = join(base, 'watch-db-state', 'localdb-watch-state.json')
  return { base, current, deps, stateDir, watchStatePath,
    command: (mode: string, count = 0) => execFileSync(python, [helper, base, mode, String(count)], { windowsHide: true }),
    close() { rmSync(base, { recursive: true, force: true }) } }
}

/** deps 附加 DB 水位存储注入（真实文件 IO，路径在临时目录内） */
function withWatchState(f: Fixture): LocaldbWatchDeps {
  return {
    ...f.deps,
    watchStatePathFn: () => f.watchStatePath,
  }
}

const runWatch = (f: Fixture, args: Parameters<typeof runLocaldbWatch>[0] = {}) =>
  runLocaldbWatch(args, ctx(), f.current, withWatchState(f))

const conversationsOf = (data: Record<string, unknown>): LocaldbWatchConversation[] =>
  data.new_messages as LocaldbWatchConversation[]

test('watchDbState：账号作用域加载/保存、账号变更全量重置、损坏与非法条目容错', () => {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-watch-dbstate-'))
  try {
    const path = join(dir, 'localdb-watch-state.json')
    // 缺失 → 空基线
    assert.deepEqual(loadLocaldbWatchState(path, 'acc1'), { account: 'acc1', conversations: {} })
    // 保存 + 回读
    assert.equal(saveLocaldbWatchState(path, {
      account: 'acc1',
      conversations: { 'S:1_2': { last_message_id: 42 } },
    }), true)
    const loaded = loadLocaldbWatchState(path, 'acc1')
    assert.equal(loaded.conversations['S:1_2']!.last_message_id, 42)
    // 账号变化 → 全量重置（conversations 清空）
    assert.deepEqual(loadLocaldbWatchState(path, 'acc2').conversations, {})
    // 损坏 JSON → 空基线；非法条目丢弃、合法条目保留
    writeFileSync(path, '{not json', 'utf8')
    assert.deepEqual(loadLocaldbWatchState(path, 'acc1').conversations, {})
    writeFileSync(path, JSON.stringify({
      account: 'acc1',
      conversations: { good: { last_message_id: 1 }, bad: { last_message_id: 'x' }, neg: { last_message_id: -1 } },
    }), 'utf8')
    const partial = loadLocaldbWatchState(path, 'acc1')
    assert.deepEqual(Object.keys(partial.conversations), ['good'])
    // 缺省路径解析：%LOCALAPPDATA% 缺失（显式 null）→ null
    assert.equal(localdbWatchStatePath(null), null)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('watch 首轮：全部会话建立基线（=当前水位），不产生新消息事件、不调 watch 查询', windows, async () => {
  const f = await fixture()
  try {
    const r = await runWatch(f)
    assert.equal(r.data!.channel, 'local-db')
    assert.deepEqual(conversationsOf(r.data!), [], '首轮只建基线，不回放存量（冻结点 4）')
    assert.equal(r.data!.baseline_established, 6, '6 个有消息的会话建立基线')
    assert.equal(r.data!.failed, undefined)
    const state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 8, '基线 = 该会话当前 max(message_id)')
    assert.equal(state.conversations['R:pending']!.last_message_id, 10)
    // 产物目录清理干净（finally 语义继承）
    assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
  } finally { f.close() }
})

test('watch 增量：只报水位之后的新消息；水位推进到已返回消息；无新消息轮不产生事件', windows, async () => {
  const f = await fixture()
  try {
    await runWatch(f) // 基线轮
    f.command('append', 2) // CONV 追加 id 13（peer）、14（self）
    const r = await runWatch(f)
    const convs = conversationsOf(r.data!)
    assert.deepEqual(convs.map((c) => c.conversation_id), [CONV])
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [13, 14])
    assert.equal(convs[0]!.has_more, undefined, '未截断时无 has_more')
    const msg13 = convs[0]!.messages[0]!
    assert.equal(msg13.side, 'peer')
    assert.equal(msg13.text, 'synthetic-13')
    assert.equal(msg13.parse_trusted, true)
    assert.equal((r.data!.last_sender as Record<string, string>)[CONV], 'self', '最后可参与判定消息 id14=self')
    let state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 14, '水位推进到已返回消息末端')
    assert.match(state.conversations[CONV]!.anchor!, /^[0-9a-f]{64}$/, '水位推进带返回末端行锚点（py _message_anchor 同源）')
    assert.equal(r.data!.watermark_reset, undefined, '正常轮（水位行未变，锚点匹配）→ 不触发重建')
    // 无新消息轮：空事件、水位不动
    const tick = await runWatch(f)
    assert.deepEqual(conversationsOf(tick.data!), [])
    assert.equal(tick.data!.watermark_reset, undefined)
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 14, '水位不推进')
  } finally { f.close() }
})

test('watch 同文件重写（fileIdentity/schema_version 不变）：水位锚点失效 → 全量重报 ID1-6 + 重建标注', windows, async () => {
  const f = await fixture('setup', 4) // CONV 水位 4（同文件重写后重生 ID 1-6，内容/时间不同）
  try {
    await runWatch(f) // 基线：CONV=4 + 锚点；重写前后 fileIdentity 与 schema_version 均不变 → 代际校验不可见
    const before = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(before.conversations[CONV]!.last_message_id, 4)
    assert.match(before.conversations[CONV]!.anchor!, /^[0-9a-f]{64}$/)
    f.command('rewrite-same', 6)
    const r = await runWatch(f)
    assert.equal(r.data!.generation_changed, undefined, '同文件重写代际不变——本用例证明只有锚点校验能捕获')
    const convs = conversationsOf(r.data!)
    assert.deepEqual(convs.map((c) => c.conversation_id), [CONV])
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [1, 2, 3, 4, 5, 6],
      '锚点失效按 0 处理 → 重生 ID 1-6 全量返回（绝不只报 5、6 漏 1-4）')
    assert.equal(convs[0]!.watermark_rebuilt, true, '会话级重建标注')
    assert.deepEqual(r.data!.watermark_reset, [CONV], '顶层重建明细')
    assert.match(r.message, /锚点失效/)
    const state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 6, '水位更新为返回集末端')
    assert.match(state.conversations[CONV]!.anchor!, /^[0-9a-f]{64}$/, '锚点更新为新末端行摘要')
    // 下一轮正常增量：不再触发重建
    f.command('append', 1) // id 7
    const next = await runWatch(f)
    const nextConvs = conversationsOf(next.data!)
    assert.deepEqual(nextConvs[0]!.messages.map((m) => m.msg_id), [7])
    assert.equal(next.data!.watermark_reset, undefined)
    assert.equal(nextConvs[0]!.watermark_rebuilt, undefined)
  } finally { f.close() }
})

test('watch 锚点重建 + limit 截断：重建路径水位回退写入，连续三轮推进 [1,2]/[3,4]/[5,6] 不卡第一页', windows, async () => {
  // 第十轮评审 P1 回归：旧水位 4 → 同文件重写重生 ID 1–6 → limit=2 时「不回退」约束会把
  // 水位卡在 4、每轮重复 [1,2]；重建路径必须以本批末端 + 新锚点写入新基线
  const f = await fixture('setup', 4)
  try {
    await runWatch(f) // 基线：CONV 水位 4 + 锚点
    f.command('rewrite-same', 6) // 同文件重写：DELETE+重插 ID 1–6（内容/时间不同）
    const r1 = await runWatch(f, { limit: 2 })
    const c1 = conversationsOf(r1.data!)
    assert.deepEqual(c1.map((c) => c.conversation_id), [CONV])
    assert.deepEqual(c1[0]!.messages.map((m) => m.msg_id), [1, 2], '锚点失效 → 按 0 全量重报，本页 [1,2]')
    assert.equal(c1[0]!.watermark_rebuilt, true)
    assert.equal(c1[0]!.has_more, true)
    let state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 2,
      '重建路径允许水位回退写入（2 < 旧水位 4）——否则下轮重复 [1,2]')
    assert.match(state.conversations[CONV]!.anchor!, /^[0-9a-f]{64}$/, '锚点 = 本批末端（ID2 重写后行）摘要')
    const anchorR1 = state.conversations[CONV]!.anchor
    const r2 = await runWatch(f, { limit: 2 })
    assert.deepEqual(conversationsOf(r2.data!).map((c) => c.messages.map((m) => m.msg_id)), [[3, 4]],
      '第二页从新基线推进（回归：旧实现每轮重复 [1,2]）')
    assert.equal(r2.data!.watermark_reset, undefined, '锚点已一致 → 正常增量，不再重建')
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 4)
    const r3 = await runWatch(f, { limit: 2 })
    assert.deepEqual(conversationsOf(r3.data!).map((c) => c.messages.map((m) => m.msg_id)), [[5, 6]])
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 6, '最终水位 = ID6')
    assert.notEqual(state.conversations[CONV]!.anchor, anchorR1, '锚点 = ID6 行摘要（与 ID2 不同）')
    // 第四轮：空 tick，水位与锚点不动
    const r4 = await runWatch(f, { limit: 2 })
    assert.deepEqual(conversationsOf(r4.data!), [])
    assert.equal(r4.data!.watermark_reset, undefined)
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 6)
  } finally { f.close() }
})

test('watch 正常增量路径水位不回退：伪造 py 回退响应（nextWatermark < 存储水位）整体不采纳；≥ 存储水位照常推进', windows, async () => {
  // 第十轮评审 P1 旁路约束：「回退写入」仅限重建（watermark_reset）路径；正常增量路径的
  // 回退响应属契约外，必须整体不采纳——水位与锚点都原样保留
  const f = await fixture('setup', 4)
  try {
    await runWatch(f) // 真实基线：CONV 水位 4 + 锚点
    const before = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(before.conversations[CONV]!.last_message_id, 4)
    const anchorBefore = before.conversations[CONV]!.anchor

    // 注入伪造 py：conversations 谎报 max=6（正常增量路径：max > prev，非重建）；
    // watch 响应 next_watermarks 伪造且无 watermark_reset、next_anchor 合法
    const fakeRow = (id: number) => ({ message_id: id, server_id: '9'.repeat(18), sequence: id,
      sender_id: PEER, conversation_id: CONV, content_type: 0, send_time: 1700000000 + id,
      flag: 0, text: 'fake', trusted: true })
    const fakePy = (nextWatermark: number): LocaldbReadDeps['runPyFn'] => (async (args: string[], opts?: RunLocaldbPyOpts) => {
      if (args[0] === 'query' && args.includes('--task')) {
        const task = args[args.indexOf('--task') + 1]
        if (task === 'context') {
          return { ok: true, data: { ok: true, self_user_id: SELF, schema_version: 1 }, raw_last_line: null }
        }
        if (task === 'conversations') {
          return { ok: true, data: { ok: true, task: 'conversations', conversations: [
            { conversation_id: CONV, count: 6, last_send_time: 1700000006, last_message_id: 6, last_anchor: 'a'.repeat(64) },
          ] }, raw_last_line: null }
        }
      }
      if (args[0] === 'watch') {
        return { ok: true, data: { ok: true, task: 'watch', per_conversation: [
          { conversation_id: CONV, messages: [fakeRow(3)], last_message_id: 6, last_sender_state: 'peer',
            has_more: false, next_anchor: 'b'.repeat(64) },
        ], next_watermarks: { [CONV]: nextWatermark } }, raw_last_line: null }
      }
      return runLocaldbPy(args, opts) // decrypt 等真实调用不受注入影响
    })

    // 伪造「非重建路径的回退响应」：水位 3 < 4 → 水位与锚点都必须原样保留
    const rogue: LocaldbWatchDeps = { ...withWatchState(f), runPyFn: fakePy(3) }
    await runLocaldbWatch({ limit: 2 }, ctx(), f.current, rogue)
    let state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 4, '非重建路径 nextWatermark < existing → 水位不动（回退写入仅限重建路径）')
    assert.equal(state.conversations[CONV]!.anchor, anchorBefore, '锚点同样不替换（非法回退响应整体不采纳）')

    // 同管道阳性对照：nextWatermark=5 ≥ 4 → 照常推进（证明注入链路本身能写盘）
    const advance: LocaldbWatchDeps = { ...withWatchState(f), runPyFn: fakePy(5) }
    await runLocaldbWatch({ limit: 2 }, ctx(), f.current, advance)
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 5, '正常增量路径（≥ existing）照常推进')
    assert.equal(state.conversations[CONV]!.anchor, 'b'.repeat(64), '锚点替换为返回末端新锚')
  } finally { f.close() }
})

test('watch 同文件重写且 max 不变（行内容已换、行数相同）：TS 行锚比对补位 → 全量重报 + 重建标注', windows, async () => {
  const f = await fixture('setup', 4) // CONV 仅会话，基线水位 4 + 锚
  try {
    await runWatch(f)
    f.command('rewrite-same', 4) // DELETE+重插 ID 1–4（内容/时间不同），max 仍 4 → 无增量轮 py 原本不会被调
    const r = await runWatch(f)
    assert.equal(r.data!.generation_changed, undefined, '代际不变（同文件）')
    const convs = conversationsOf(r.data!)
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [1, 2, 3, 4],
      'max 未变但水位行锚已失效 → 按 0 全量重报（此前静默压住：行内容已换却无任何输出）')
    assert.equal(convs[0]!.watermark_rebuilt, true)
    assert.deepEqual(r.data!.watermark_reset, [CONV])
    const state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 4)
    assert.match(state.conversations[CONV]!.anchor!, /^[0-9a-f]{64}$/, '锚点更新为重写后末端行')
    // 锚点一致后恢复正常：同水位轮不再触发
    const tick = await runWatch(f)
    assert.deepEqual(conversationsOf(tick.data!), [])
    assert.equal(tick.data!.watermark_reset, undefined, '行未变的同水位轮不触发重建（不增加每轮开销）')
  } finally { f.close() }
})

test('watch 水位行被删（同文件、max 不回退）：锚点校验行缺失 → 全量重报 + 重建标注', windows, async () => {
  const f = await fixture('unread-setup') // CONV 行 id 1、2、7、8（max=8）
  try {
    await runWatch(f) // 基线：CONV=8 + 锚点
    f.command('delete-row', 8) // 删除水位行本身（fileIdentity/schema_version 不变）
    f.command('append', 1) // id 13（populate 只插更大 id，不会重建同 id 行）
    const r = await runWatch(f)
    const convs = conversationsOf(r.data!)
    assert.deepEqual(convs.map((c) => c.conversation_id), [CONV])
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [1, 2, 7, 13],
      '水位行不存在 → 视为水位失效按 0 处理（现存行全量返回）')
    assert.equal(convs[0]!.watermark_rebuilt, true)
    assert.deepEqual(r.data!.watermark_reset, [CONV])
    assert.equal(loadLocaldbWatchState(f.watchStatePath, SELF).conversations[CONV]!.last_message_id, 13)
  } finally { f.close() }
})

test('watch last_sender_state 双向：尾部系统消息按 last_participating 判（py 侧语义透传）', windows, async () => {
  const f = await fixture()
  try {
    await runWatch(f) // 基线：CONV 末条为 1017 系统（self），可参与判定末条 = id3 peer
    f.command('append', 1) // id 13（peer）
    const peerRound = await runWatch(f)
    assert.equal((peerRound.data!.last_sender as Record<string, string>)[CONV], 'peer')
    f.command('append', 1) // id 14（self）
    const selfRound = await runWatch(f)
    assert.equal((selfRound.data!.last_sender as Record<string, string>)[CONV], 'self')
  } finally { f.close() }
})

test('watch limit 截断：每会话独立 limit、has_more=true、水位只推进到已返回消息', windows, async () => {
  const f = await fixture()
  try {
    await runWatch(f) // 基线
    f.command('append', 2) // id 13、14
    const r = await runWatch(f, { limit: 1 })
    const convs = conversationsOf(r.data!)
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [13])
    assert.equal(convs[0]!.has_more, true)
    let state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 13, '只推进到已返回消息')
    const follow = await runWatch(f, { limit: 1 })
    const convs2 = conversationsOf(follow.data!)
    assert.deepEqual(convs2[0]!.messages.map((m) => m.msg_id), [14])
    assert.equal(convs2[0]!.has_more, undefined)
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 14)
  } finally { f.close() }
})

test('watch focus：命中解析、部分失败进 failed（该会话水位不动）、全部失败不冒充空快照', windows, async () => {
  const f = await fixture()
  try {
    await runWatch(f) // 基线
    f.command('append', 1)
    const hit = await runWatch(f, { focus: ['Peer'] })
    assert.deepEqual(conversationsOf(hit.data!).map((c) => c.conversation_id), [CONV])
    assert.equal(hit.data!.failed, undefined)

    const partial = await runWatch(f, { focus: ['Peer', '不存在'] })
    assert.deepEqual(conversationsOf(partial.data!), [], '水位已推进，重复轮无新消息')
    const failed = partial.data!.failed as Array<{ focus: string; reason: string }>
    assert.deepEqual(failed, [{ focus: '不存在', reason: 'TARGET_NOT_FOUND' }])

    const allFailed = await runWatch(f, { focus: ['不存在'] })
    assert.deepEqual(conversationsOf(allFailed.data!), [])
    assert.equal(((allFailed.data!.failed as unknown[]) ?? []).length, 1)
  } finally { f.close() }
})

test('watchDbState：generation 锚与 focus 持久绑定 roundtrip；非法字段/条目容错丢弃', () => {
  const dir = mkdtempSync(join(tmpdir(), 'wecom-watch-dbstate-'))
  try {
    const path = join(dir, 'localdb-watch-state.json')
    // generation + focus 绑定（含 ref 类型存证 type）+ 水位锚点（空串=水位 0 形态）roundtrip
    assert.equal(saveLocaldbWatchState(path, {
      account: 'acc1',
      generation: 'gen-anchor-1',
      focus: [{ key: 'Peer', conversation_id: 'S:1_2', display_name: 'Peer', source: 'ref', type: 'contact', bound_at: '2026-10-16T00:00:00.000Z' }],
      conversations: {
        'S:1_2': { last_message_id: 42, anchor: 'a'.repeat(64) },
        'R:zero': { last_message_id: 0, anchor: '' },
      },
    }), true)
    const loaded = loadLocaldbWatchState(path, 'acc1')
    assert.equal(loaded.generation, 'gen-anchor-1')
    assert.deepEqual(loaded.focus, [{
      key: 'Peer', conversation_id: 'S:1_2', display_name: 'Peer', source: 'ref', type: 'contact', bound_at: '2026-10-16T00:00:00.000Z',
    }])
    assert.equal(loaded.conversations['S:1_2']!.anchor, 'a'.repeat(64))
    assert.equal(loaded.conversations['R:zero']!.anchor, '', '空串锚点（显式关注空会话水位 0）合法保留')
    // 非法 generation / 非法 focus 条目（source 越界）/ 非法 anchor（数字）丢弃，合法条目与水位保留
    writeFileSync(path, JSON.stringify({
      account: 'acc1',
      generation: 123,
      focus: [
        { key: 'bad', conversation_id: 'c', display_name: 'd', source: 'bogus', bound_at: 'x' },
        { key: 'ok', conversation_id: 'c2', display_name: 'd2', source: 'name', bound_at: 'y' },
      ],
      conversations: { good: { last_message_id: 1 }, badanchor: { last_message_id: 2, anchor: 3 } },
    }), 'utf8')
    const partial = loadLocaldbWatchState(path, 'acc1')
    assert.equal(partial.generation, undefined, '非法 generation 丢弃（watch 轮按 generation-missing 重建基线）')
    assert.deepEqual(partial.focus?.map((b) => b.key), ['ok'])
    assert.deepEqual(Object.keys(partial.conversations), ['good'], 'anchor 非字符串 → 该水位条目丢弃（退化为重建基线）')
    // 账号变化 → focus 绑定随水位一并全量重置
    assert.equal(loadLocaldbWatchState(path, 'acc2').focus, undefined)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('watch 显式关注空会话：水位存 0 → 首批新消息（ID1、2）全量上报（不被当新建关注跳过）', windows, async () => {
  const f = await fixture('setup') // setup 且 count=0：message_table 全空，session 有 'Empty Group'（R:empty）
  try {
    const first = await runWatch(f, { focus: ['Empty Group'] })
    assert.deepEqual(conversationsOf(first.data!), [], '空会话本轮无消息')
    assert.equal(first.data!.baseline_established, 1)
    assert.equal(first.data!.generation_changed, undefined)
    let state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations['R:empty']!.last_message_id, 0, '显式关注空会话 → 水位存 0（而非跳过等首条建基线）；0 是合法水位')
    f.command('append-empty', 2) // R:empty 落库 id 1（peer）、2（self）
    const second = await runWatch(f, { focus: ['Empty Group'] })
    const convs = conversationsOf(second.data!)
    assert.deepEqual(convs.map((c) => c.conversation_id), ['R:empty'], '首批新消息按正常增量全量上报（旧实现按当前 max 建基线会整批跳过）')
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [1, 2])
    assert.equal(convs[0]!.display_name, 'Empty Group', '输出显示名 = 绑定时显示名')
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations['R:empty']!.last_message_id, 2, '水位推进到已返回消息')
    f.command('append-empty', 1) // id 3：之后只报 3（水位 0 起点的增量语义回归确认）
    const third = await runWatch(f, { focus: ['Empty Group'] })
    assert.deepEqual(conversationsOf(third.data!)[0]!.messages.map((m) => m.msg_id), [3],
      '首批全量上报之后转入正常增量，绝不重复已返回消息')
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations['R:empty']!.last_message_id, 3)
  } finally { f.close() }
})

test('watch 全量模式（未传 focus）首见空会话：不建 0 基线；首条消息落库后按当前 max 建基线（存量不回放）', windows, async () => {
  const f = await fixture('setup') // message_table 全空；session 有 'Empty Group'（R:empty）
  try {
    const r1 = await runWatch(f)
    assert.equal(loadLocaldbWatchState(f.watchStatePath, SELF).conversations['R:empty'], undefined,
      '全量模式首见（无消息）不建基线——水位 0 仅限显式 focus 解析成功的空会话')
    f.command('append-empty', 2) // R:empty 落库 id 1（peer）、2（self）
    const r2 = await runWatch(f)
    assert.deepEqual(conversationsOf(r2.data!), [], '首见会话按当前 max 建基线（不回放存量）')
    assert.equal(loadLocaldbWatchState(f.watchStatePath, SELF).conversations['R:empty']!.last_message_id, 2)
  } finally { f.close() }
})

test('watch 同账号换库（新 fileIdentity + 小 message_id）：generation_changed + 基线重建 + 新消息正常报', windows, async () => {
  const f = await fixture()
  try {
    await runWatch(f) // 基线：CONV max=8（存 generation G1）
    f.command('append', 1) // CONV id 13
    const inc = await runWatch(f)
    assert.deepEqual(conversationsOf(inc.data!).map((c) => c.messages.map((m) => m.msg_id)).flat(), [13])
    // 同账号源库替换重建：新 fileIdentity + AUTOINCREMENT 归零（CONV 仅剩 id 1）
    f.command('rebuild-small')
    const r = await runWatch(f)
    assert.equal(r.data!.generation_changed, true, '代际不一致必须明确报告，绝不冒充「正常无消息轮」')
    assert.equal(r.data!.generation_reason, 'generation-mismatch')
    assert.deepEqual(conversationsOf(r.data!), [], '基线重建轮不报增量（旧水位不可信）')
    let state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 1, '基线重建 = 新库当前 max')
    f.command('append', 2) // 新库 id 2（self）、3（peer）——旧水位 13 会把它们永久压住
    const after = await runWatch(f)
    const convs = conversationsOf(after.data!)
    assert.deepEqual(convs.map((c) => c.conversation_id), [CONV], '重建后的新消息正常上报')
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [2, 3])
    assert.equal(after.data!.generation_changed, undefined)
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 3)
  } finally { f.close() }
})

test('watch 旧版本状态文件缺 generation：generation-missing + 基线重建（不冒充无消息轮）', windows, async () => {
  const f = await fixture()
  try {
    await runWatch(f) // 基线（带 generation）
    // 模拟旧版本写入的状态：有水位、无 generation
    saveLocaldbWatchState(f.watchStatePath, {
      account: SELF,
      conversations: { [CONV]: { last_message_id: 100 } },
    })
    const r = await runWatch(f)
    assert.equal(r.data!.generation_changed, true)
    assert.equal(r.data!.generation_reason, 'generation-missing')
    assert.deepEqual(conversationsOf(r.data!), [])
    assert.equal(loadLocaldbWatchState(f.watchStatePath, SELF).conversations[CONV]!.last_message_id, 8, '基线重建 = 当前 max')
  } finally { f.close() }
})

test('watch 水位回退（当前 max < 存储水位且代际未变）：按代际变更同处理（报告 + 重建基线）', windows, async () => {
  const f = await fixture()
  try {
    await runWatch(f) // 基线
    // 保留当前 generation，仅把水位改到当前 max 之上（模拟源库回退）
    const saved = loadLocaldbWatchState(f.watchStatePath, SELF)
    saveLocaldbWatchState(f.watchStatePath, {
      account: SELF,
      ...(saved.generation !== undefined ? { generation: saved.generation } : {}),
      conversations: { [CONV]: { last_message_id: 100 } },
    })
    const r = await runWatch(f)
    assert.equal(r.data!.generation_changed, true)
    assert.equal(r.data!.generation_reason, 'watermark-regressed')
    assert.deepEqual(conversationsOf(r.data!), [])
    assert.equal(loadLocaldbWatchState(f.watchStatePath, SELF).conversations[CONV]!.last_message_id, 8, '该会话基线重建 = 当前 max')
  } finally { f.close() }
})

test('watch focus 改名容错：绑定后联系人改名，仍按持久 conv_id 正常增量（不再按名 resolve）', windows, async () => {
  const f = await fixture()
  try {
    const bind = await runWatch(f, { focus: ['Peer'] })
    assert.equal(bind.data!.failed, undefined)
    let state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.deepEqual(state.focus, [{
      key: 'Peer', conversation_id: CONV, display_name: 'Peer', source: 'name', bound_at: state.focus![0]!.bound_at,
    }])
    f.command('rename-peer') // user_table：PEER 名改 'Renamed'（旧实现每轮按名 resolve → TARGET_NOT_FOUND 停止跟踪）
    f.command('bad-session-schema') // conversation_table 换坏 schema：按名 resolve 的标题来源也被破坏（零 resolve 强证明）
    f.command('append', 1) // CONV id 13（peer）
    const r = await runWatch(f, { focus: ['Peer'] })
    assert.equal(r.data!.failed, undefined, '改名 + 会话解析来源同时破坏仍不影响跟踪（按绑定 conversation_id 读数，零 resolve）')
    const convs = conversationsOf(r.data!)
    assert.deepEqual(convs.map((c) => c.conversation_id), [CONV])
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [13])
    assert.equal(convs[0]!.display_name, 'Peer', '输出显示名 = 绑定时显示名（不随改名漂移）')
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 13)
  } finally { f.close() }
})

test('watch focus target_ref 输入：真实签名+账号匹配 → 绑定与跟踪；跨账号 ref → failed 不绑定', windows, async () => {
  const f = await fixture()
  try {
    const keyDir = f.stateDir // 真实签发/验证（本机密钥目录注入临时目录，不触真实 %LOCALAPPDATA%）
    const ref = createTargetRef('Peer', 'contact', '', { account: SELF, keyDir })
    const deps: LocaldbWatchDeps = {
      ...withWatchState(f),
      verifyRefFn: (r) => verifyTargetRef(r, { keyDir }),
    }
    const first = await runLocaldbWatch({ focus: [ref] }, ctx(), f.current, deps)
    assert.equal(first.data!.failed, undefined, '签名有效且账号匹配的 contact ref 可作关注输入')
    assert.equal(first.data!.baseline_established, 1, '建立基线 = 当前 max（存量不回放）')
    let state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 8)
    assert.equal(state.focus?.length, 1)
    assert.equal(state.focus![0]!.source, 'ref')
    assert.equal(state.focus![0]!.display_name, 'Peer')
    assert.equal(state.focus![0]!.conversation_id, CONV)
    f.command('append', 1)
    const second = await runLocaldbWatch({ focus: [ref] }, ctx(), f.current, deps)
    assert.equal(second.data!.failed, undefined, '后续轮次按绑定跟踪')
    const convs = conversationsOf(second.data!)
    assert.deepEqual(convs.map((c) => c.conversation_id), [CONV])
    assert.deepEqual(convs[0]!.messages.map((m) => m.msg_id), [13])

    // 跨账号 ref：账号一致性核对失败 → failed（TARGET_REF_STALE），不落绑定
    const foreign = createTargetRef('Peer', 'contact', '', { account: 'someone-else', keyDir })
    const third = await runLocaldbWatch({ focus: [foreign] }, ctx(), f.current, deps)
    assert.deepEqual(third.data!.failed, [{ focus: foreign, reason: 'TARGET_REF_STALE' }])
    state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.focus!.some((b) => b.key === foreign), false)
    assert.ok(state.focus!.some((b) => b.key === ref), '失败进 failed：既有绑定保留（不解绑）')
    assert.equal(state.conversations[CONV]!.last_message_id, 13, '水位不受失败项影响')
  } finally { f.close() }
})

test('watch focus ref 身份约束：legacy ref（无账号）拒绝；group ref 不绑到同名联系人；contact 绑定带 type', windows, async () => {
  const f = await fixture('unread-setup') // conversation_table 有 (CONV,'Peer')（S 标题）+ user_table 有 (PEER,'Peer')
  try {
    const keyDir = f.stateDir
    const deps: LocaldbWatchDeps = {
      ...withWatchState(f),
      verifyRefFn: (r) => verifyTargetRef(r, { keyDir }),
    }
    // legacy ref（payload 无 account，签名有效）：本地库只接受账号绑定 ref → failed，不静默绑到当前账号会话
    const legacy = createTargetRef('Peer', 'contact', '', { keyDir })
    const first = await runLocaldbWatch({ focus: [legacy] }, ctx(), f.current, deps)
    assert.deepEqual(first.data!.failed, [{ focus: legacy, reason: 'legacy-ref-unsupported' }],
      'legacy ref 无账号证据 → 拒绝（同 readSession 的本地库 target 约束）')
    assert.equal(loadLocaldbWatchState(f.watchStatePath, SELF).focus, undefined, '不落绑定')

    // group ref 名「Peer」+ 库中同名联系人：类型约束过滤 → 无 R: 命中 → failed（不绑到联系人 S 会话）
    const groupRef = createTargetRef('Peer', 'group', '', { account: SELF, keyDir })
    const second = await runLocaldbWatch({ focus: [groupRef] }, ctx(), f.current, deps)
    assert.deepEqual(second.data!.failed, [{ focus: groupRef, reason: 'TARGET_NOT_FOUND' }],
      'group 引用只认 R:，不能绑到同名 contact 的 S 会话')
    assert.equal(loadLocaldbWatchState(f.watchStatePath, SELF).focus, undefined)

    // 回归：contact ref 正常绑定不受影响，绑定条目存证 ref 类型
    const contactRef = createTargetRef('Peer', 'contact', '', { account: SELF, keyDir })
    const third = await runLocaldbWatch({ focus: [contactRef] }, ctx(), f.current, deps)
    assert.equal(third.data!.failed, undefined)
    const state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.focus?.length, 1)
    assert.equal(state.focus![0]!.type, 'contact')
    assert.equal(state.focus![0]!.conversation_id, CONV)
    assert.equal(state.conversations[CONV]!.last_message_id, 8, '绑定基线语义不变')
  } finally { f.close() }
})

test('watch 账号切换：DB 水位存储账号不符 → 全量重置基线（不回放旧账号水位）', windows, async () => {
  const f = await fixture()
  try {
    await runWatch(f) // 基线（账号 SELF）
    f.command('append', 1)
    // 伪造旧账号水位存储：账号不符 → loadLocaldbWatchState 重置 → 重新建基线而非报增量
    saveLocaldbWatchState(f.watchStatePath, {
      account: 'someone-else',
      conversations: { [CONV]: { last_message_id: 1 } },
    })
    const r = await runWatch(f)
    assert.deepEqual(conversationsOf(r.data!), [], '账号变化 → 重建基线，不按旧水位报增量')
    const state = loadLocaldbWatchState(f.watchStatePath, SELF)
    assert.equal(state.conversations[CONV]!.last_message_id, 13)
  } finally { f.close() }
})

test('watch 版本准入：落盘版本不一致拒绝；forced 只覆盖落盘状态选择不绕过矩阵', windows, async () => {
  const f = await fixture()
  try {
    writeLocalState({ mode: 'db', version: '9.9.9.9', checked_at: 'fixture-epoch' }, f.stateDir)
    await assert.rejects(runWatch(f),
      (err: unknown) => err instanceof CodedOperationError && err.code === 'VERSION_UNSUPPORTED')
    const forced = await runWatch(f, { forced: true })
    assert.equal(forced.data!.channel, 'local-db')
  } finally { f.close() }
})

test('watch 本人映射缺失 → IDENTITY_UNCONFIRMED（不吞）；取消 → CancelledError 透传', windows, async () => {
  const f = await fixture()
  try {
    f.command('missing-self')
    await assert.rejects(runWatch(f),
      (err: unknown) => err instanceof CodedOperationError && err.code === 'IDENTITY_UNCONFIRMED')
  } finally { f.close() }

  const f2 = await fixture()
  try {
    const ac = new AbortController()
    ac.abort()
    await assert.rejects(
      runLocaldbWatch({}, { signal: ac.signal, progress() {} }, f2.current, withWatchState(f2)),
      (err: unknown) => err instanceof CancelledError,
    )
  } finally { f2.close() }
})

test('parseWatchResponse：last_sender_state 非法值归一 null；next_watermarks 缺失/非法不推进水位；锚点/重建标注防御解析', () => {
  const { per } = parseWatchResponse({
    per_conversation: [
      { conversation_id: 'R:1', messages: [{ message_id: 5, sender_id: 'x', text: null, trusted: false }], last_sender_state: 'bogus', has_more: true, watermark_reset: true, next_anchor: 'a'.repeat(64) },
      { conversation_id: 'R:2', messages: [], last_sender_state: 'peer' },
      { conversation_id: 'R:4', messages: [{ message_id: 7, sender_id: 'y', text: null, trusted: false }], next_anchor: 'not-hex', watermark_reset: 'yes' },
      'junk-row',
    ],
    next_watermarks: { 'R:1': 5, 'R:2': 'bad', 'R:3': 9 },
  })
  assert.equal(per.length, 3)
  assert.equal(per[0]!.lastSenderState, null, '非 self/peer → null')
  assert.equal(per[0]!.hasMore, true)
  assert.equal(per[0]!.nextWatermark, 5)
  assert.equal(per[0]!.watermarkReset, true)
  assert.equal(per[0]!.nextAnchor, 'a'.repeat(64))
  assert.equal(per[1]!.nextWatermark, -1, 'next_watermarks 非法 → 该会话水位不动（防御）')
  assert.equal(per[1]!.watermarkReset, false, '缺省非重建')
  assert.equal(per[2]!.nextAnchor, undefined, '锚点非法（非 64-hex）→ undefined（水位推进但锚保持旧值，下轮校验失效重建）')
  assert.equal(per[2]!.watermarkReset, false, 'watermark_reset 非布尔 true → 按未重建（不信任契约漂移）')
})
