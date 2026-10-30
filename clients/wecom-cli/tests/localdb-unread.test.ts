/**
 * P2 任务 3.1 本地库 unread 执行器整链（真实三库 + 独立加密 + Python 驱动；对齐
 * localdb-read-session.test.ts 合成范式，只替换活进程/DPAPI 边界）。
 *
 * 覆盖：待回复双向判定（系统消息既不制造也不清除）/ 自会话排除 / 全系统类计数标注 /
 * focus 逐名解析与部分失败 / name 过滤 DB 通道（与 UI 通道同语义子串匹配）/ 版本准入 forced /
 * 身份映射（missing-self）/ 可选 session 容忍 / 清理失败 cleanup_failed 保留。
 * 第十轮评审修复：备注/别名参与反向取名（match_names，name 过滤命中 name 或 match_names
 * 任意一项）/ 命名来源不可用（R: 会话 + session 缺失）指定 name → LOCALDB_UNAVAILABLE 明确
 * 失败、未指定 name → 快照 + naming_incomplete 标注、全 S 会话不误伤。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { CodedOperationError, type OpContext } from '../src/operations/types.js'
import { readLocaldbUnread, type UnrepliedConversation } from '../src/platform/localdb/unread.js'
import { type LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import { writeLocalState } from '../src/platform/localdb/matrix.js'
import { runLocaldbPy } from '../src/platform/localdb/driver.js'
import { createLocaldbWorkDir } from '../src/platform/localdb/cleanup.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const helper = join(root, 'tests', 'py', 'read_session_fixture.py')
const SELF = '9007199254740997'
const KEY = '000102030405060708090a0b0c0d0e0f' // 合成密钥，不接触真实 keystore。
const VERSION = '5.0.11.6029'
const CONV = `S:${SELF}_9007199254740999`
const ctx = (): OpContext => ({ signal: new AbortController().signal, progress() {} })
const windows = { skip: process.platform !== 'win32' }

interface Fixture {
  base: string
  current: CurrentAccount
  deps: LocaldbReadDeps
  stateDir: string
  command(mode: string): void
  close(): void
}

async function fixture(setup = 'unread-setup', count = 0): Promise<Fixture> {
  const base = mkdtempSync(join(tmpdir(), 'wecom-p2-unread-'))
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
  return { base, current, deps, stateDir,
    command: (mode: string) => execFileSync(python, [helper, base, mode], { windowsHide: true }),
    close() { rmSync(base, { recursive: true, force: true }) } }
}

const ids = (conversations: UnrepliedConversation[]): string[] => conversations.map((c) => c.conversation_id)

test('全量待回复快照：仅待回复会话出现（unreplied:true + last_sender 判定）+ 全系统类计数', windows, async () => {
  const f = await fixture()
  try {
    const r = await readLocaldbUnread({}, ctx(), f.current, f.deps)
    assert.equal(r.data!.channel, 'local-db')
    assert.equal(r.data!.judgment_basis, 'last_sender')
    const conversations = r.data!.conversations as UnrepliedConversation[]
    // 待回复：S:SELF_PEER（尾部系统消息不清除）+ R:pending；不出现：本人最后发言 / 系统不制造 / 自会话
    assert.deepEqual(ids(conversations).sort(), [CONV, 'R:pending'].sort())
    for (const c of conversations) assert.equal(c.unreplied, true)
    const main = conversations.find((c) => c.conversation_id === CONV)!
    assert.equal(main.last_text_preview, '在吗', '待回复预览 = 最后一条可参与判定消息（尾部系统消息不顶掉）')
    assert.match(main.last_time, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
    assert.equal(r.data!.system_only_conversations, 1, '全系统类会话（R:sysonly）非待回复且标注计数')
    assert.equal((r.data!.failed as unknown[] | undefined), undefined)
    assert.match(r.message, /待回复会话 2 个/)
    // 产物目录清理干净（finally 语义继承）
    assert.deepEqual(readdirSync(f.deps.workBaseDir!), [])
  } finally { f.close() }
})

test('待回复双向判定：尾部系统消息不清除对方待回复，也不制造本人会话的待回复', windows, async () => {
  const f = await fixture()
  try {
    const r = await readLocaldbUnread({}, ctx(), f.current, f.deps)
    const conversations = ids(r.data!.conversations as UnrepliedConversation[])
    assert.ok(conversations.includes(CONV), 'peer 最后发言后的系统消息不得清除待回复')
    assert.ok(!conversations.includes('R:selfsys'), '本人最后发言后的系统消息不得制造待回复')
    assert.ok(!conversations.includes('R:project'), '本人最后发言 → 非待回复不出现')
  } finally { f.close() }
})

test('自会话排除：S:self_self 永不出现', windows, async () => {
  const f = await fixture()
  try {
    const r = await readLocaldbUnread({}, ctx(), f.current, f.deps)
    assert.ok(!ids(r.data!.conversations as UnrepliedConversation[]).includes(`S:${SELF}_${SELF}`))
  } finally { f.close() }
})

test('focus：命中附 name；部分失败进 failed 不影响其他；解析成功但非待回复 → 空快照无 failed', windows, async () => {
  const f = await fixture()
  try {
    const hit = await readLocaldbUnread({ focus: ['Peer'] }, ctx(), f.current, f.deps)
    let conversations = hit.data!.conversations as UnrepliedConversation[]
    assert.deepEqual(ids(conversations), [CONV])
    assert.equal(conversations[0]!.name, 'Peer')

    const partial = await readLocaldbUnread({ focus: ['Peer', '不存在'] }, ctx(), f.current, f.deps)
    conversations = partial.data!.conversations as UnrepliedConversation[]
    assert.deepEqual(ids(conversations), [CONV], '单名解析失败不影响其他 focus')
    const failed = partial.data!.failed as Array<{ focus: string; reason: string }>
    assert.deepEqual(failed, [{ focus: '不存在', reason: 'TARGET_NOT_FOUND' }])

    const allFailed = await readLocaldbUnread({ focus: ['不存在', '也没有'] }, ctx(), f.current, f.deps)
    assert.deepEqual(allFailed.data!.conversations, [], '全部失败不冒充正常空快照之外的内容')
    assert.equal(((allFailed.data!.failed as unknown[]) ?? []).length, 2)

    const notPending = await readLocaldbUnread({ focus: ['项目群'] }, ctx(), f.current, f.deps)
    assert.deepEqual(notPending.data!.conversations, [], '解析成功但本人最后发言 → 无待回复')
    assert.equal(notPending.data!.failed, undefined)
  } finally { f.close() }
})

test('name 过滤 DB 通道：display_name 子串包含（与 UI 通道同语义），无显示名的会话排除', windows, async () => {
  const f = await fixture()
  try {
    // focus 命中的会话携带显示名：'待回复群'（R:pending，待回复）与 'Peer'（CONV，待回复）
    const r = await readLocaldbUnread({ name: '待回复', focus: ['待回复群', 'Peer'] }, ctx(), f.current, f.deps)
    let conversations = r.data!.conversations as UnrepliedConversation[]
    assert.deepEqual(ids(conversations), ['R:pending'],
      '子串匹配：会话名「待回复群」含「待回复」命中；「Peer」同为待回复但不含子串 → 排除（与 UI 通道 u.name.includes 同语义）')
    assert.match(r.message, /匹配「待回复」/)
    // 无任何匹配 → 空列表（正常返回，不报错）
    const none = await readLocaldbUnread({ name: '不存在的子串', focus: ['待回复群', 'Peer'] }, ctx(), f.current, f.deps)
    assert.deepEqual(ids(none.data!.conversations as UnrepliedConversation[]), [])
    // 未传 name → 全量（既有语义不变；两个 focus 会话都待回复）
    const all = await readLocaldbUnread({ focus: ['待回复群', 'Peer'] }, ctx(), f.current, f.deps)
    assert.deepEqual(ids(all.data!.conversations as UnrepliedConversation[]).sort(), ['R:pending', CONV].sort())
    // 未传 focus：反向取名（py --with-names）后按实际名称匹配（第九轮评审 P2 修正——不再把
    // 全部会话当「无显示名」排除）：CONV 对方在 user_table 名「Peer」→ 命中真实待回复
    const noFocus = await readLocaldbUnread({ name: 'Peer' }, ctx(), f.current, f.deps)
    conversations = noFocus.data!.conversations as UnrepliedConversation[]
    assert.deepEqual(ids(conversations), [CONV])
    assert.equal(conversations[0]!.name, 'Peer', '命中会话附反向取名的实际名称')
    // 反向取名无匹配 → 空（正常返回）
    const noFocusNone = await readLocaldbUnread({ name: '不存在' }, ctx(), f.current, f.deps)
    assert.deepEqual(ids(noFocusNone.data!.conversations as UnrepliedConversation[]), [])
  } finally { f.close() }
})

test('name 过滤无 focus：无命名来源会话（反向取名 null）指定 name 时排除、未指定时保留', windows, async () => {
  const f = await fixture()
  try {
    f.command('anon-pending') // R:anon：不在 conversation_table、对方不在 user_table → 反向取名 null
    // 未指定 name：R:anon 待回复照常出现，name 字段缺省
    const all = await readLocaldbUnread({}, ctx(), f.current, f.deps)
    const conversations = all.data!.conversations as UnrepliedConversation[]
    const anon = conversations.find((c) => c.conversation_id === 'R:anon')
    assert.ok(anon, '无命名来源的待回复会话保留')
    assert.equal(anon!.name, undefined, '未指定 name 时 name 字段缺省')
    // 指定 name：null 名无法匹配而排除（R:peer 最后发言，待回复但无显示名）
    const named = await readLocaldbUnread({ name: 'Peer' }, ctx(), f.current, f.deps)
    assert.deepEqual(ids(named.data!.conversations as UnrepliedConversation[]), [CONV],
      '反向取名命中的 Peer（CONV）出现；R:anon（null 名）排除')
    const anonFilter = await readLocaldbUnread({ name: 'anon' }, ctx(), f.current, f.deps)
    assert.deepEqual(ids(anonFilter.data!.conversations as UnrepliedConversation[]), [],
      'null 名会话对任何 name 子串都无法匹配 → 排除（不冒充匹配）')
  } finally { f.close() }
})

test('name 过滤命中备注/别名（match_names）：与 resolve 同语义（第十轮评审 P2①）', windows, async () => {
  const f = await fixture()
  try {
    f.command('peer-remark') // PEER：name='Peer'（不变）+ remark='BossRemark'
    // 规范名照常命中，conversations 仍带规范 name
    const byName = await readLocaldbUnread({ name: 'Peer' }, ctx(), f.current, f.deps)
    let conversations = byName.data!.conversations as UnrepliedConversation[]
    assert.deepEqual(ids(conversations), [CONV])
    assert.equal(conversations[0]!.name, 'Peer')
    // 备注命中（无 focus 路径）：旧实现只读 name 列 → 空列表；现在 match_names 命中
    const byRemark = await readLocaldbUnread({ name: 'BossRemark' }, ctx(), f.current, f.deps)
    conversations = byRemark.data!.conversations as UnrepliedConversation[]
    assert.deepEqual(ids(conversations), [CONV], '备注子串命中 match_names（与 resolve 的名称/别名/备注规则一致）')
    assert.equal(conversations[0]!.name, 'Peer', '输出仍带规范 name（非备注）')
    // 无任何命中 → 空列表
    const none = await readLocaldbUnread({ name: '不存在' }, ctx(), f.current, f.deps)
    assert.deepEqual(ids(none.data!.conversations as UnrepliedConversation[]), [])
    // resolve 多列语义一致性：focus 路径同样经备注列命中 → 解析到同一目标会话（不进 failed）
    const viaFocus = await readLocaldbUnread({ focus: ['BossRemark'] }, ctx(), f.current, f.deps)
    assert.equal(viaFocus.data!.failed, undefined, 'resolve 经 remark 列命中（与 match_names 同列集），不进 failed')
    conversations = viaFocus.data!.conversations as UnrepliedConversation[]
    assert.deepEqual(ids(conversations), [CONV], 'focus=备注 解析到同一会话（待回复照常输出）')
    assert.equal(conversations[0]!.name, 'BossRemark', 'focus 命中显示名 = focus 原名（既有语义）')
  } finally { f.close() }
})

test('命名来源不可用（R: 会话 + session 缺失）：指定 name → LOCALDB_UNAVAILABLE；不传 name → 快照 + naming_incomplete 标注', windows, async () => {
  const f = await fixture()
  try {
    unlinkSync(f.current.dataDir.db_paths!.session!)
    f.current.dataDir.db_paths!.session = null
    // 指定 name：存在待回复群但群名取不到 → 明确失败，不冒充「确实无匹配」的空结果
    await assert.rejects(readLocaldbUnread({ name: '待回复群' }, ctx(), f.current, f.deps),
      (err: unknown) => err instanceof CodedOperationError && err.code === 'LOCALDB_UNAVAILABLE' &&
        err.message.includes('群命名来源不可用'))
    // 未指定 name：普通快照保持 session 可选语义不变（不报错），naming_incomplete 只标注
    const all = await readLocaldbUnread({}, ctx(), f.current, f.deps)
    assert.equal(all.data!.naming_incomplete, true)
    assert.deepEqual(ids(all.data!.conversations as UnrepliedConversation[]).sort(),
      [CONV, 'R:pending'].sort(), '快照结果不受影响')
  } finally { f.close() }
})

test('命名来源不可用但全 S 会话：指定 name 正常过滤（不误伤）', windows, async () => {
  const f = await fixture('setup', 1) // 仅 CONV（S:）有消息（id 1 peer）；R:empty 无行
  try {
    unlinkSync(f.current.dataDir.db_paths!.session!)
    f.current.dataDir.db_paths!.session = null
    const r = await readLocaldbUnread({ name: 'Peer' }, ctx(), f.current, f.deps)
    assert.deepEqual(ids(r.data!.conversations as UnrepliedConversation[]), [CONV],
      '无 R: 会话 → session 缺失不影响 S 联系人命名与过滤')
    assert.equal(r.data!.naming_incomplete, undefined, '无 R: 会话 → 不标 naming_incomplete')
  } finally { f.close() }
})

test('版本准入：落盘版本不一致拒绝；forced 只覆盖落盘状态选择不绕过矩阵', windows, async () => {
  const f = await fixture()
  try {
    writeLocalState({ mode: 'db', version: '9.9.9.9', checked_at: 'fixture-epoch' }, f.stateDir)
    await assert.rejects(readLocaldbUnread({}, ctx(), f.current, f.deps),
      (err: unknown) => err instanceof CodedOperationError && err.code === 'VERSION_UNSUPPORTED')
    const forced = await readLocaldbUnread({ forced: true }, ctx(), f.current, f.deps)
    assert.equal(forced.data!.channel, 'local-db')
  } finally { f.close() }
})

test('本人映射缺失 → IDENTITY_UNCONFIRMED（context 任务 --account 校验同 readSession）', windows, async () => {
  const f = await fixture()
  try {
    f.command('missing-self')
    await assert.rejects(readLocaldbUnread({}, ctx(), f.current, f.deps),
      (err: unknown) => err instanceof CodedOperationError && err.code === 'IDENTITY_UNCONFIRMED')
  } finally { f.close() }
})

test('可选 session 缺失：无 focus 快照不受影响；focus 群名单名失败进 failed', windows, async () => {
  const f = await fixture()
  try {
    unlinkSync(f.current.dataDir.db_paths!.session!)
    f.current.dataDir.db_paths!.session = null
    const all = await readLocaldbUnread({}, ctx(), f.current, f.deps)
    assert.deepEqual(ids(all.data!.conversations as UnrepliedConversation[]).sort(), [CONV, 'R:pending'].sort())
    const focused = await readLocaldbUnread({ focus: ['待回复群'] }, ctx(), f.current, f.deps)
    assert.deepEqual(focused.data!.conversations, [])
    const failed = focused.data!.failed as Array<{ focus: string; reason: string }>
    assert.equal(failed.length, 1)
    assert.equal(failed[0]!.focus, '待回复群')
    assert.equal(failed[0]!.reason, 'LOCALDB_UNAVAILABLE', '会话定位来源不足按能力不足报告，不冒充查无目标')
  } finally { f.close() }
})

test('段内失败且工作目录清理失败 → 错误附 cleanup_failed，不掩盖原错误码', windows, async () => {
  const f = await fixture()
  try {
    f.deps.captureFn = async () => ({ ok: false, reason: 'copy-failed', detail: 'synthetic' })
    f.deps.workDirFn = () => {
      const work = createLocaldbWorkDir('unread', f.deps.workBaseDir)!
      return { ...work, cleanup: () => false }
    }
    await assert.rejects(readLocaldbUnread({}, ctx(), f.current, f.deps),
      (err: unknown) => err instanceof CodedOperationError && err.code === 'SNAPSHOT_UNSTABLE' &&
        Array.isArray(err.data?.cleanup_failed) && (err.data!.cleanup_failed as unknown[]).length === 1)
  } finally { f.close() }
})

test('真实 keyscan 未命中（无 keystore 密钥）→ KEY_SCAN_FAILED 与负缓存落盘语义同 readSession', windows, async () => {
  const f = await fixture()
  try {
    f.deps.loadKeyFn = async () => null
    f.deps.detectRunningFn = async () => ({ running: true, pids: [4242], probe: 'ok' })
    f.deps.runPyFn = async (args, opts) => {
      if (args[0] === 'keyscan') return { ok: true, data: { ok: false, error: 'KEY_NOT_FOUND' }, raw_last_line: null }
      return runLocaldbPy(args, opts)
    }
    await assert.rejects(readLocaldbUnread({ forced: true }, ctx(), f.current, f.deps),
      (err: unknown) => err instanceof CodedOperationError && err.code === 'KEY_SCAN_FAILED')
    assert.equal(existsSync(join(f.stateDir, 'localdb-state.json')), true)
  } finally { f.close() }
})
