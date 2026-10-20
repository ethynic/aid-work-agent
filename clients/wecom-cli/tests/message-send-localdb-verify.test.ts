/**
 * P2 任务 3.4 message_send 本地库发送核验编排（合成链：真实三库 + 独立加密 + Python 驱动，
 * runDriverFn 注入发送驱动 mock——绝不触达真实企微窗口）。
 *
 * 覆盖：db 核验命中（local-record + 匹配 message_id；基线-发送-匹配时序由「发送 mock 落库
 * 后仍命中」证明——基线若晚于发送则 after-id 查不到新记录）/ 未命中转 -VerifyOnly
 * （ocr-bubble）/ db 核验失败（快照类）转 -VerifyOnly / 双不足 EXECUTION_UNKNOWN（effect=
 * unknown，不重入发送）/ localdb 不可用走老路径零改动（无 -SkipVerify、无 verification）/
 * 取消（发送后）→ CANCELLED partial 不重入核验与发送。
 * 第八轮评审修复：跨账号 target_ref 发送前拒绝（TARGET_REF_STALE / 驱动零调用，校验在本地
 * 库兜底 try 之外；含真实签发+真实验签、仅 payload.account 为他账号的整链形态）/ 账号匹配
 * ref 正常发送 / probe 身份失败按自身错误码透传不静默 / 核验阶段一切错误（UI_CHANGED 等
 * Coded 与非 Coded 驱动错误）归并 EXECUTION_UNKNOWN 不误报 effect=none / ps1 -VerifyOnly
 * 标题一致性静态核对（核对先于三选二终态、不一致抛错中止、发送路径 step4 复核保持
 * UI_CHANGED）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createWecomMessageSendOperation } from '../src/operations/messageSend.js'
import { CancelledError, CodedOperationError, type OpContext } from '../src/operations/types.js'
import type { PowerShellScriptOptions, RunPowerShellDriverFn } from '../src/platform/powershell.js'
import type { LocaldbReadDeps } from '../src/platform/localdb/readSession.js'
import { locateDataDir } from '../src/platform/localdb/datadir.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import { writeLocalState } from '../src/platform/localdb/matrix.js'
import { captureSnapshot } from '../src/platform/localdb/snapshot.js'
import { createTargetRef, verifyTargetRef } from '../src/platform/targetRef.js'

const root = fileURLToPath(new URL('../../', import.meta.url))
const python = join(root, '..', '..', 'venv', 'Scripts', 'python.exe')
const helper = join(root, 'tests', 'py', 'read_session_fixture.py')
const SELF = '9007199254740997'
const KEY = '000102030405060708090a0b0c0d0e0f'
const VERSION = '5.0.11.6029'
const windows = { skip: process.platform !== 'win32' }

const SKIP_OK = {
  navigate_required: false,
  title: 'Peer',
  sent_verification: { method: 'skipped', result: 'unverified' },
  input_point: { x: 640, y: 648, source: 'jev' },
  timing_ms: { total: 5000 },
  screenshot_paths: ['a.png'],
}
const VERIFY_OK = {
  navigate_required: false,
  title: 'Peer',
  sent_verification: { method: 'jev', result: 'sent' },
  input_point: null,
  timing_ms: { final_ocr: 800, jev2: 700, total: 3000 },
  screenshot_paths: ['b.png'],
}

interface Fixture {
  base: string
  current: CurrentAccount
  deps: LocaldbReadDeps
  stateDir: string
  artifactRoot: string
  /** 模拟发送后消息落库（unread-setup 基础上 append 2 条到 S:SELF_PEER：id13=peer、id14=self） */
  appendTwo(): void
  close(): void
}

async function fixture(): Promise<Fixture> {
  const base = mkdtempSync(join(tmpdir(), 'wecom-p2-sendverify-'))
  execFileSync(python, [helper, base, 'unread-setup'], { windowsHide: true })
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
  return { base, current, deps, stateDir, artifactRoot: join(base, 'artifacts'),
    appendTwo: () => execFileSync(python, [helper, base, 'append', '2'], { windowsHide: true }),
    close() { rmSync(base, { recursive: true, force: true }) } }
}

interface DriverMock {
  calls: PowerShellScriptOptions[]
  /** message-send.ps1 各次调用的响应队列（Error 原样抛出） */
  stages: Array<Record<string, unknown> | Error>
  /** 发送阶段回调（模拟「Enter 后消息落库」等副作用） */
  onSend?: (call: PowerShellScriptOptions) => void
}

function makeRunDriver(mock: DriverMock): RunPowerShellDriverFn {
  return (async (opts: PowerShellScriptOptions) => {
    mock.calls.push(opts)
    const base = opts.script.replace(/^.*[\\/]/, '')
    if (base === 'message-send.ps1') {
      const isVerifyOnly = opts.args?.includes('-VerifyOnly') ?? false
      if (!isVerifyOnly) mock.onSend?.(opts)
      const stage = mock.stages.shift()
      if (stage === undefined) throw new Error('message-send.ps1 出现未预期的额外调用')
      if (stage instanceof Error) throw stage
      return stage
    }
    throw new Error(`未预期的驱动脚本：${base}`)
  }) as RunPowerShellDriverFn
}

function silentCtx(signal = new AbortController().signal): OpContext {
  return { signal, progress: () => {} }
}

const driverArgs = (call: PowerShellScriptOptions): string[] => call.args ?? []

test('db 核验命中：-SkipVerify 发送 → 本地记录匹配 → applied + verification=local-record + 匹配 message_id', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK }] }
    // 发送动作落库（时序证明：基线必须在发送前记录——基线若含 id13/14，after-id 查询将
    // 查不到新记录，核验只能落 ocr-bubble；命中 local-record 即证明基线先于发送）
    mock.onSend = () => f.appendTwo()
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: 'synthetic-14' }, silentCtx())
    assert.equal(r.success, true)
    assert.equal(r.code, 'OK')
    assert.equal(r.effect, 'applied')
    assert.equal(r.data.verification, 'local-record')
    assert.deepEqual(r.data.verification_detail, { method: 'local-record', message_id: 14 })
    assert.equal(r.data.verification_local_note, undefined)
    assert.match(r.message, /不代表对方已收到/)
    // 发送驱动仅一次调用且带 -SkipVerify；无 -VerifyOnly 补验
    assert.equal(mock.calls.length, 1)
    const args = driverArgs(mock.calls[0]!)
    assert.ok(args.includes('-SkipVerify'), '发送驱动应带 -SkipVerify')
    assert.ok(!args.includes('-VerifyOnly'))
  } finally { f.close() }
})

test('未命中转 OCR 核验：-VerifyOnly 命中 → applied + verification=ocr-bubble', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK }, { ...VERIFY_OK }] }
    mock.onSend = () => f.appendTwo() // 落库文本与发送文本不一致 → 本地库不匹配
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 2, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: '完全不同的文本' }, silentCtx())
    assert.equal(r.success, true)
    assert.equal(r.data.verification, 'ocr-bubble')
    assert.equal(r.data.verification_detail, undefined)
    assert.equal(mock.calls.length, 2)
    assert.ok(driverArgs(mock.calls[0]!).includes('-SkipVerify'))
    assert.ok(driverArgs(mock.calls[1]!).includes('-VerifyOnly'), '未命中应补 -VerifyOnly 核验')
    assert.ok(!driverArgs(mock.calls[1]!).includes('-SkipVerify'), '核验调用绝不带 -SkipVerify（不重入发送）')
  } finally { f.close() }
})

test('db 核验失败（快照类）转 -VerifyOnly：ocr-bubble + localdb_verify_failed 透传', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK }, { ...VERIFY_OK }] }
    mock.onSend = () => f.appendTwo()
    let captureCalls = 0
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: {
        ...f.deps,
        // 基线管线成功（每管线按 message/user/session 捕获 3 次：第 1-3 次放行），
        // 发送后核验管线的快照全部失败（第 4 次起）→ SNAPSHOT_UNSTABLE
        captureFn: async (db, wal, dir) => {
          captureCalls++
          if (captureCalls <= 3) return captureSnapshot(db, wal, dir)
          return { ok: false, reason: 'copy-failed', detail: 'synthetic-after-send' }
        },
      },
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: 'synthetic-14' }, silentCtx())
    assert.equal(r.success, true)
    assert.equal(r.data.verification, 'ocr-bubble')
    assert.equal(r.data.localdb_verify_failed, 'SNAPSHOT_UNSTABLE')
    assert.equal(mock.calls.length, 2)
  } finally { f.close() }
})

test('双不足 → EXECUTION_UNKNOWN（effect=unknown），不重入发送阶段（无第三次驱动调用）', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK },
      new CodedOperationError('EXECUTION_UNKNOWN', '发送后校验失败（三选二仅过 1/3 项）：消息可能已发出，不会自动重试，请人工核对（详见 driver-log.txt）')] }
    mock.onSend = () => f.appendTwo()
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: '完全不同的文本' }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'EXECUTION_UNKNOWN')
    assert.equal(r.effect, 'unknown')
    assert.equal(r.data.verification, undefined, 'EXECUTION_UNKNOWN 时 verification 缺省（三值语义）')
    assert.equal(mock.calls.length, 2, '发送 1 次 + 核验 1 次，绝不重入发送')
    assert.ok(driverArgs(mock.calls[0]!).includes('-SkipVerify'))
    assert.ok(driverArgs(mock.calls[1]!).includes('-VerifyOnly'))
  } finally { f.close() }
})

test('核验驱动超时：-VerifyOnly RESULT_TIMEOUT → 归并 EXECUTION_UNKNOWN（effect=unknown，不误报 effect=none）', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK },
      new CodedOperationError('RESULT_TIMEOUT', 'PowerShell 驱动执行超时（>300s），已终止子进程')] }
    mock.onSend = () => f.appendTwo() // 落库文本与发送文本不一致 → 本地库未命中 → 转 -VerifyOnly
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: '完全不同的文本' }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'EXECUTION_UNKNOWN', '核验阶段超时按「消息可能已发出」归并，不透传 RESULT_TIMEOUT')
    assert.equal(r.effect, 'unknown', 'completed=1 时 effect=unknown，绝不是 none（消息已发出）')
    assert.equal(r.data.completed, 1)
    assert.equal(r.data.verification, undefined)
    assert.equal(mock.calls.length, 2, '超时后不重入发送与核验')
    assert.ok(driverArgs(mock.calls[0]!).includes('-SkipVerify'))
    assert.ok(driverArgs(mock.calls[1]!).includes('-VerifyOnly'))
  } finally { f.close() }
})

test('localdb 不可用（unset 状态）→ 完全走既有单次调用路径（零改动：无 -SkipVerify、无 verification 字段）', async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{
      navigate_required: false, title: 'Peer',
      sent_verification: { method: 'jev', result: 'sent' },
      input_point: { x: 1, y: 2, source: 'jev' }, timing_ms: { total: 6000 }, screenshot_paths: [],
    }] }
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: { ...f.deps, readStateFn: () => null },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: 'hello' }, silentCtx())
    assert.equal(r.success, true)
    assert.equal(r.data.verification, undefined, '老路径无 verification 字段（行为零改动）')
    assert.equal(mock.calls.length, 1)
    const args = driverArgs(mock.calls[0]!)
    assert.ok(!args.includes('-SkipVerify'), '老路径驱动参数不含 -SkipVerify')
    assert.match(r.message, /Jev/)
  } finally { f.close() }
})

test('发送后取消：CANCELLED + effect=partial（completed=1），不重入核验与发送', windows, async () => {
  const f = await fixture()
  try {
    const ac = new AbortController()
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK }] }
    mock.onSend = () => {
      f.appendTwo()
      ac.abort() // 发送动作完成后、本地库核验前取消
    }
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 3, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: 'synthetic-14' }, silentCtx(ac.signal))
    assert.equal(r.success, false)
    assert.equal(r.code, 'CANCELLED')
    assert.equal(r.effect, 'partial', '发送已完成（completed=1），取消按 partial 归并')
    assert.equal(mock.calls.length, 1, '取消后不重入核验（无 -VerifyOnly）与发送')
  } finally { f.close() }
})

test('取消发生在基线阶段（未发送）→ CANCELLED effect=none，未触达发送驱动', windows, async () => {
  const f = await fixture()
  try {
    const ac = new AbortController()
    const mock: DriverMock = { calls: [], stages: [] }
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: { ...f.deps, probeAccountFn: async () => {
        ac.abort()
        throw new CancelledError()
      } },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: 'hello' }, silentCtx(ac.signal))
    assert.equal(r.code, 'CANCELLED')
    assert.equal(r.effect, 'none', '未发送任何消息')
    assert.equal(mock.calls.length, 0)
  } finally { f.close() }
})

test('跨账号 target_ref：发送/降级之前拒绝（TARGET_REF_STALE / effect=none），发送驱动零调用', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [] } // 任何驱动调用都视为失败信号
    let probeCalls = 0
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '', account: 'someone-else' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      // 落盘状态为 db（fixture）：若校验被 planLocaldb 的兜底 catch 吞掉，流程会回退老路径
      // 并调用发送驱动——零调用断言同时钉死「校验在本地库 try 之外」的落点
      localdb: {
        ...f.deps,
        probeAccountFn: async () => {
          probeCalls++
          return f.current
        },
      },
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: 'hello' }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'TARGET_REF_STALE', '跨账号签发的有效 ref 不得 OK/applied')
    assert.equal(r.effect, 'none', '未发送任何消息')
    assert.equal(r.data.completed, undefined)
    assert.equal(mock.calls.length, 0, '发送驱动零调用（不被本地库核验失败兜底转旧发送路径）')
    assert.equal(probeCalls, 1, '校验用当前账号直接证据')
  } finally { f.close() }
})

test('跨账号真实签名 ref（真实签发+验签，仅 payload.account 为他账号）：发送/降级之前拒绝，发送驱动零调用', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [] } // 任何驱动调用都视为失败信号
    // ref 用真实本机密钥签发（签名有效、未过期、可正常 verify），仅 payload.account 是另一账号：
    // 钉死「真实验签链路把 account 交给发送侧账号核对」，而非仅 mock verifyRefFn 的操作内逻辑
    const ref = createTargetRef('Peer', 'contact', '', { keyDir: f.stateDir, account: 'the-other-account' })
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: (r) => verifyTargetRef(r, { keyDir: f.stateDir }),
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: ref, text: 'hello' }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'TARGET_REF_STALE', '有效签名的跨账号 ref 不得 OK/applied，必须在任何发送/降级前拒绝')
    assert.equal(r.effect, 'none', '未发送任何消息')
    assert.equal(r.data.completed, undefined)
    assert.equal(mock.calls.length, 0, '发送驱动零调用（含降级 search/select 与 -SkipVerify/-VerifyOnly）')
  } finally { f.close() }
})

test('probe 身份失败透传不静默：NOT_LOGGED_IN 原样抛出、不跳过账号核对、不发送', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [] }
    // 真实签名、账号匹配的 ref：账号核对触发 probe；probe 抛 NOT_LOGGED_IN 必须原样透传
    // （这段核对在 planLocaldb 兜底 catch 之外，不得静默跳过核对转发送）
    const ref = createTargetRef('Peer', 'contact', '', { keyDir: f.stateDir, account: SELF })
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: (r) => verifyTargetRef(r, { keyDir: f.stateDir }),
      artifactDirFn: () => f.artifactRoot,
      localdb: { ...f.deps, probeAccountFn: async () => { throw new CodedOperationError('NOT_LOGGED_IN', '企微未登录') } },
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: ref, text: 'hello' }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'NOT_LOGGED_IN', 'probe 失败按其自身错误码透传（不静默跳过账号核对转发送）')
    assert.equal(mock.calls.length, 0, '核对未完成绝不进入发送')
  } finally { f.close() }
})

test('账号匹配 target_ref：核对通过正常发送（local-record 命中，行为不变）', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK }] }
    mock.onSend = () => f.appendTwo()
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '', account: SELF }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: 'synthetic-14' }, silentCtx())
    assert.equal(r.success, true)
    assert.equal(r.effect, 'applied')
    assert.equal(r.data.verification, 'local-record')
    assert.equal(mock.calls.length, 1)
  } finally { f.close() }
})

test('发送后核验失败（VerifyOnly 抛 UI_CHANGED）：归并 EXECUTION_UNKNOWN（effect=unknown），不透传、不重入发送', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK },
      new CodedOperationError('UI_CHANGED', '发送后校验失败（UI 变化）：证据不可信')] }
    mock.onSend = () => f.appendTwo() // 落库文本与发送文本不一致 → 本地库未命中 → 转 -VerifyOnly
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      createRefFn: (name, type, subtitle) => `ref:${name}:${type}:${subtitle}`,
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: '完全不同的文本' }, silentCtx())
    assert.equal(r.success, false)
    assert.equal(r.code, 'EXECUTION_UNKNOWN', '发送已完成：核验阶段错误一律不透传原码（UI_CHANGED 会被归为 effect=none，把「可能已发出」误报成「未写入」误导重发）')
    assert.equal(r.effect, 'unknown')
    assert.equal(r.data.completed, 1)
    assert.equal(r.data.verification, undefined, 'EXECUTION_UNKNOWN 时 verification 缺省（三值语义）')
    assert.equal(mock.calls.length, 2, '不重入发送阶段')
    assert.ok(driverArgs(mock.calls[1]!).includes('-VerifyOnly'))
  } finally { f.close() }
})

test('核验驱动非 Coded 错误（驱动崩溃）：同样归并 EXECUTION_UNKNOWN（effect=unknown，不透传崩溃）', windows, async () => {
  const f = await fixture()
  try {
    const mock: DriverMock = { calls: [], stages: [{ ...SKIP_OK }, new Error('ocr 引擎崩溃（非 Coded）')] }
    mock.onSend = () => f.appendTwo() // 落库文本与发送文本不一致 → 本地库未命中 → 转 -VerifyOnly
    const op = createWecomMessageSendOperation({
      runDriverFn: makeRunDriver(mock),
      verifyRefFn: () => ({ name: 'Peer', type: 'other', subtitle: '' }),
      artifactDirFn: () => f.artifactRoot,
      localdb: f.deps,
      localdbVerifyPoll: { attempts: 1, delayMs: 1 },
    })
    const r = await op.execute({ target_ref: 'ref-peer', text: '完全不同的文本' }, silentCtx())
    assert.equal(r.code, 'EXECUTION_UNKNOWN', '非 Coded 驱动错误同样按「消息可能已发出」归并（消息注明驱动执行失败）')
    assert.match(r.message, /驱动执行失败/, '非 Coded 分支的消息标注（区别于 Coded 错误的 err.code 插值）')
    assert.equal(r.effect, 'unknown')
    assert.equal(r.data.completed, 1)
    assert.equal(mock.calls.length, 2, '不重入发送阶段')
    assert.ok(driverArgs(mock.calls[1]!).includes('-VerifyOnly'))
  } finally { f.close() }
})

test('静态核对（P2）：-VerifyOnly 分支的标题一致性检查存在且先于三选二终态判定', () => {
  // dist/tests → 包根 drivers/ps1（编译布局；测试断言源文件内容，与运行平台无关）
  const src = readFileSync(fileURLToPath(new URL('../../drivers/ps1/message-send.ps1', import.meta.url)), 'utf8')
  const verifyTitleLog = src.indexOf("Write-DriverLog ('verify-only 当前标题 title='")
  assert.ok(verifyTitleLog > -1, '定位 -VerifyOnly 分支（标题 OCR 日志锚点）')
  const check = src.indexOf('if (-not (Test-WeComTitleMatch -Title $title2 -Expected $TargetName)) {', verifyTitleLog)
  assert.ok(check > verifyTitleLog, '标题一致性检查位于 -VerifyOnly 分支内（复用发送路径 Test-WeComTitleMatch 规则）')
  const ruleSection = src.indexOf('终态规则三选二')
  assert.ok(ruleSection > check, '标题一致性检查先于三选二/Jev 终态判定（防非目标会话的证据凑满两项）')
  const checkBlock = src.slice(check, ruleSection)
  assert.ok(checkBlock.includes('EXECUTION_UNKNOWN'), '不一致 → EXECUTION_UNKNOWN（无法确认），绝不 ocr-bubble 成功')
  assert.ok(checkBlock.includes('Throw-DriverError'), '不一致必须抛错中止（Throw-DriverError）')
  // 发送路径的 step4 发送前复核仍是 UI_CHANGED（不被 -VerifyOnly 分支的修复误改）
  const sendRecheck = src.indexOf('step4 发送前复核')
  assert.ok(sendRecheck > -1 && sendRecheck < verifyTitleLog, '定位发送路径 step4 复核（位于 -VerifyOnly 分支之前）')
  const sendBlock = src.slice(sendRecheck, verifyTitleLog)
  assert.ok(sendBlock.includes('UI_CHANGED'), '发送路径 step4 复核语义保持 UI_CHANGED')
})
