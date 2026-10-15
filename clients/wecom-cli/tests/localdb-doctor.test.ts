/**
 * 1.11 doctor 深检编排单测（全 mock，零真实进程/文件依赖；设计 §5 两阶段语义逐项验证）。
 *
 * 覆盖：
 * - 全过 → mode=db + 状态文件写入 + mode_decided 日志 + keyscan_result 日志；
 * - 引导阶段失败（未安装 / 未登录 / 登录态 unknown）→ 终态报告，mode='unset'，不写状态不产日志；
 * - 深检各步失败分类：目录暂缺=ui(transient)、目录身份未确认=ui(transient)（identity-unconfirmed，
 *   主窗口所属进程的完整句柄证据不足时拒绝）、
 *   版本 unknown=ui(compat)、指纹不匹配=ui(compat)、keyscan 未命中=ui(transient)、
 *   解密失败=ui(transient)、查询 SCHEMA_UNSUPPORTED=ui(compat)、查询其他失败=ui(transient)；
 * - 缓存密钥命中跳过 keyscan；缓存密钥页 1 核验失败 → clearKey + 重扫 + 重试解密；
 * - 密钥安全：key_hex 不出现在报告 JSON / 日志事件 / 步骤 detail；
 * - 步骤 7 快照：瞬时失败有限重试（§3.3 整体重试 N 次小退避；重试成功不改结论 / 重试耗尽
 *   ui(transient) / snapshotMaxAttempts=1 单次语义）；工作目录 finally 清理（成功与失败路径都调用）；
 * - UI 就绪块独立报告（本地库不继承交互会话门禁）；
 * - CLI 接线：doctor --localdb 输出双就绪块 + 退出码语义。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  runLocaldbDoctor,
  type DoctorWorkDir,
  type LocaldbDoctorDeps,
  type LocaldbDoctorReport,
} from '../src/platform/localdb/doctorLocaldb.js'
import { doctorCommand } from '../src/cli/commands/doctor.js'
import type { InstallInfo } from '../src/platform/localdb/version.js'
import type { RunningInfo } from '../src/platform/localdb/running.js'
import type { DataDirInfo } from '../src/platform/localdb/datadir.js'
import type { LocalDbState, VersionMatrix } from '../src/platform/localdb/matrix.js'
import type { DbFingerprintExpected, DbFingerprintResult } from '../src/platform/localdb/fingerprint.js'
import type { CaptureSnapshotResult } from '../src/platform/localdb/snapshot.js'
import type { KeyBinding, StoredKey } from '../src/platform/localdb/keystore.js'
import type { LocaldbLogEvent } from '../src/platform/localdb/log.js'
import type { LocaldbPyResult, RunLocaldbPyOpts } from '../src/platform/localdb/driver.js'
import type { EnvironmentProbe } from '../src/platform/environment.js'

// ---------- 固定夹具 ----------

const KEY_HEX = 'ab12cd34ef56ab12cd34ef56ab12cd34' // 32 hex（测试夹具，非真实密钥）

const MATRIX: VersionMatrix = {
  schema_version: 1,
  updated_at: '2026-10-14',
  entries: {
    '5.0.11.6029': {
      status: 'validated',
      verified_date: '2026-10-14',
      algorithm: 'wxaes128cbc-nonlegacy',
      page_size: 4096,
      db_magic: 'b3df88da44b8102f',
      key_form: 'raw16-in-private-rw',
      scan_strategy: 'dual-md5-sha256-oracle',
      data_dir_pattern: '{documents}/WXWork/{user_id}',
      schema_profile: 'wecom-5.0.x',
    },
  },
}

const INSTALL: InstallInfo = {
  installed: true,
  exe_path: 'C:\\Program Files (x86)\\WXWork\\5.0.11.6029\\WXWork.exe',
  version: '5.0.11.6029',
  version_source: 'process',
  pid: 4321,
  process_started_at: 1_760_000_000_000, // 默认夹具：实例锚点可得（步骤 3 身份核验前提）
  install_dir: 'C:\\Program Files (x86)\\WXWork\\5.0.11.6029',
  upgrade_signals: [],
}

const RUNNING: RunningInfo = { running: true, pids: [4321, 4322], probe: 'ok' }

const DATADIR: DataDirInfo = {
  source: 'cef-log',
  root: 'C:\\Users\\t\\OneDrive\\文档\\WXWork',
  user_id: '1688850000000001',
  dir: 'C:\\Users\\t\\OneDrive\\文档\\WXWork\\1688850000000001',
  db_paths: {
    message: 'C:\\Users\\t\\OneDrive\\文档\\WXWork\\1688850000000001\\Data\\message.db',
    session: 'C:\\Users\\t\\OneDrive\\文档\\WXWork\\1688850000000001\\Data\\session.db',
    user: 'C:\\Users\\t\\OneDrive\\文档\\WXWork\\1688850000000001\\Data\\user.db',
    message_lookup: 'C:\\Users\\t\\OneDrive\\文档\\WXWork\\1688850000000001\\Data\\message_lookup.db',
  },
  accounts: [],
  identity: {
    config_found: true,
    login_company_id: null,
    cross_check: 'process-handle-match',
    identity_confirmed: true,
    reason: null,
    process_pid: 4321,
  },
}

const FINGERPRINT_OK: DbFingerprintResult = {
  match: true,
  detail: '魔数与明文页头均匹配',
  observed: { magic: 'b3df88da44b8102f', plaintext_header: '100002020004020 20'.replace(' ', '') },
}

const ENV_OK: EnvironmentProbe = {
  platform_ok: true,
  platform: 'win32',
  interactive_session: true,
  session_name: 'Console',
  powershell_available: true,
  powershell_path: 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe',
  wecom_running: true,
  wecom_processes: 2,
}

const pyResult = (payload: Record<string, unknown>): LocaldbPyResult => ({ ok: true, data: payload, raw_last_line: null })

/** 默认 py 驱动替身：按 args[0] 分发 keyscan/decrypt/query；记录调用供断言 */
interface PyCall {
  args: string[]
  opts?: RunLocaldbPyOpts
}
interface Harness {
  deps: LocaldbDoctorDeps
  pyCalls: PyCall[]
  states: LocalDbState[]
  events: LocaldbLogEvent[]
  cleanups: number
  loadKeyCalls: KeyBinding[]
  clearKeyCalls: number
  snapshotCalls: Array<{ srcDb: string; srcWal: string | null; workDir: string }>
  sweepCalls: number
  /** 组件调用顺序标记（'sweep'/'install'/…，断言清扫先于步骤 1） */
  order: string[]
}

interface HarnessOverrides {
  install?: Partial<InstallInfo> | null
  loginState?: 'online' | 'need_login' | 'offline' | 'unknown'
  dataDir?: Partial<DataDirInfo> | null
  matrix?: VersionMatrix | null
  lookupVersionOverride?: LocaldbDoctorDeps['lookupVersionFn']
  fingerprint?: Partial<DbFingerprintResult>
  storedKey?: StoredKey | null
  activeAccountPayload?: Record<string, unknown>
  keyscanPayload?: Record<string, unknown> | null
  keyscanBridgeFail?: string
  decryptPayload?: Record<string, unknown> | null
  decryptBridgeFail?: string
  queryPayload?: Record<string, unknown> | null
  queryBridgeFail?: string
  snapshotResult?: CaptureSnapshotResult
  saveKeyOk?: boolean
  env?: Partial<EnvironmentProbe>
  pids?: number[]
  walExists?: boolean
  workDirMissing?: boolean
  loadMatrixFn?: LocaldbDoctorDeps['loadMatrixFn']
  sweepFailed?: boolean
  sweepThrow?: string
}

/** 组装全 mock 依赖（默认八步全过路径）；返回记录器供断言 */
function makeHarness(over: HarnessOverrides = {}): Harness {
  const pyCalls: PyCall[] = []
  const states: LocalDbState[] = []
  const events: LocaldbLogEvent[] = []
  const loadKeyCalls: KeyBinding[] = []
  const h: Harness = {
    pyCalls,
    states,
    events,
    cleanups: 0,
    loadKeyCalls,
    clearKeyCalls: 0,
    snapshotCalls: [],
    sweepCalls: 0,
    order: [],
    deps: {
      detectInstallFn: async (opts) => {
        h.order.push('install')
        return over.install === null
          ? { ...INSTALL, installed: false, exe_path: null, version: null, version_source: null, pid: null, install_dir: null }
          : { ...INSTALL, ...over.install, ...(opts?.processPid ? { pid: opts.processPid } : {}) }
      },
      detectRunningFn: async () => ({ ...RUNNING, pids: over.pids ?? RUNNING.pids }),
      probeLoginStateFn: async () => ({ login_state: over.loginState ?? 'online', source: 'localdb-login-driver', window_pid: 4321 }),
      locateDataDirFn: async () =>
        over.dataDir === null
          ? { ...DATADIR, source: null, root: null, user_id: null, dir: null, db_paths: null, accounts: [] }
          : { ...DATADIR, ...over.dataDir },
      loadMatrixFn: over.loadMatrixFn ?? (() => (over.matrix !== undefined ? over.matrix : MATRIX)),
      lookupVersionFn:
        over.lookupVersionOverride ??
        ((matrix, version) => {
          const entry = matrix !== null && typeof version === 'string' ? matrix.entries[version] : undefined
          if (!entry) return { entry: null, status: 'unknown' }
          const status = entry.status
          return status === 'validated' || status === 'stale' || status === 'broken'
            ? { entry, status }
            : { entry, status: 'unknown' }
        }),
      checkDbFingerprintFn: (dbPath: string, expected: DbFingerprintExpected) => {
        void dbPath
        assert.equal(expected.db_magic, 'b3df88da44b8102f')
        assert.equal(expected.page_size, 4096)
        return { ...FINGERPRINT_OK, ...over.fingerprint }
      },
      loadKeyFn: async (binding: KeyBinding) => {
        h.loadKeyCalls.push(binding)
        return over.storedKey !== undefined ? over.storedKey : null
      },
      saveKeyFn: async () => over.saveKeyOk !== false,
      clearKeyFn: () => {
        h.clearKeyCalls++
        return true
      },
      runLocaldbPyFn: async (args: string[], o?: RunLocaldbPyOpts) => {
        pyCalls.push({ args, opts: o })
        const mode = args[0]
        if (mode === 'active-account') return pyResult(over.activeAccountPayload ?? { ok: true, pid: 4321, message_db: DATADIR.db_paths!.message })
        if (mode === 'keyscan') {
          if (over.keyscanBridgeFail !== undefined) return { ok: false, data: null, raw_last_line: null, error: over.keyscanBridgeFail }
          return pyResult(over.keyscanPayload ?? { ok: true, kind: 'OPENED', key_hex: KEY_HEX, pid: 4321, scanned_bytes: 1048576, elapsed_ms: 1234 })
        }
        if (mode === 'decrypt') {
          if (over.decryptBridgeFail !== undefined) return { ok: false, data: null, raw_last_line: null, error: over.decryptBridgeFail }
          return pyResult(over.decryptPayload ?? { ok: true, pages: 104, wal_frames: 3, stale_tail_frames: 0, integrity: 'ok', out_db: 'X\\message.dec.db' })
        }
        if (mode === 'query') {
          if (over.queryBridgeFail !== undefined) return { ok: false, data: null, raw_last_line: null, error: over.queryBridgeFail }
          return pyResult(
            over.queryPayload ?? { ok: true, task: 'conversations', conversations: [{ conversation_id: 'c1', count: 42, last_send_time: 1, last_message_id: 9 }] },
          )
        }
        throw new Error(`unexpected py mode: ${String(mode)}`)
      },
      captureSnapshotFn: async (srcDb: string, srcWal: string | null, workDir: string) => {
        h.snapshotCalls.push({ srcDb, srcWal, workDir })
        return (
          over.snapshotResult ?? {
            ok: true,
            dbSnapshot: `${workDir}\\message.db`,
            walSnapshot: srcWal === null ? null : `${workDir}\\message.db-wal`,
            capture: { h0: 'aa', h1: 'aa', sizes: { db_size: 425984, wal_size: 32768 } },
          }
        )
      },
      probeEnvironmentFn: async () => ({ ...ENV_OK, ...over.env }),
      sweepArtifactsFn: () => {
        h.order.push('sweep')
        h.sweepCalls++
        if (over.sweepThrow !== undefined) throw new Error(over.sweepThrow)
        return over.sweepFailed === true
          ? { swept: [], skipped: [], failed: [{ dir: 'A\\doctor_x', error: 'EBUSY: resource busy or locked' }] }
          : { swept: [], skipped: [], failed: [] }
      },
      writeLocalStateFn: (state: LocalDbState) => {
        states.push(state)
        return true
      },
      logLocaldbEventFn: (event: LocaldbLogEvent) => {
        events.push(event)
        return true
      },
      createWorkDirFn: () => {
        if (over.workDirMissing) return null
        const work: DoctorWorkDir = {
          dir: 'W:\\art\\doctor_run1',
          run_id: 'doctor_run1',
          cleanup: () => {
            h.cleanups++
            return true
          },
        }
        return work
      },
      existsFn: (path: string) => over.walExists ?? path.endsWith('message.db-wal'),
      now: (() => {
        let t = 1_700_000_000_000
        return () => new Date((t += 5))
      })(),
    },
  }
  return h
}

const step = (r: LocaldbDoctorReport, n: number) => r.steps.find((s) => s.step === n)!
const pyCallsOf = (h: Harness, mode: string) => h.pyCalls.filter((c) => c.args[0] === mode)

// ---------- 全过路径 ----------

test('全过 → mode=db：八步全 ok、状态写入 {mode:db,version,checked_at}、mode_decided 日志', async () => {
  const h = makeHarness()
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.success, true)
  assert.equal(r.localdb_ready, true)
  assert.equal(r.steps.length, 8)
  assert.ok(r.steps.every((s) => s.ok))
  assert.ok(r.steps.every((s) => s.elapsed_ms >= 0))
  assert.equal(r.mode_decision.mode, 'db')
  assert.equal(r.mode_decision.kind, undefined)
  assert.equal(r.mode_decision.version, '5.0.11.6029')
  assert.ok(r.mode_decision.checked_at.length > 0)
  // 状态文件：写入一次，shape = {mode, reason, version, checked_at}（db 无 kind）
  assert.equal(h.states.length, 1)
  assert.equal(h.states[0]!.mode, 'db')
  assert.equal(h.states[0]!.kind, undefined)
  assert.equal(h.states[0]!.version, '5.0.11.6029')
  // mode_decided 日志恰一次
  const decided = h.events.filter((e) => e.type === 'mode_decided')
  assert.equal(decided.length, 1)
  assert.ok(decided[0]!.detail!.includes('mode=db'))
  // UI 就绪块独立呈现
  assert.equal(r.ui_ready.ready, true)
})

test('keystore 未命中 → keyscan 路径：--pids 取 detectRunning 列表、命中后 saveKey、keyscan_result 日志', async () => {
  const h = makeHarness()
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'db')
  const scans = pyCallsOf(h, 'keyscan')
  assert.equal(scans.length, 1)
  assert.equal(scans[0]!.args[1], '--pids')
  assert.equal(scans[0]!.args[2], '4321,4322')
  assert.equal(scans[0]!.args[3], '--db')
  assert.ok(scans[0]!.args[4]!.endsWith('message.db'))
  // key_hex 绝不进命令行参数（stdin 通道专属）
  assert.ok(!scans[0]!.args.join(' ').includes(KEY_HEX))
  const events = h.events.filter((e) => e.type === 'keyscan_result')
  assert.equal(events.length, 1)
  assert.equal(events[0]!.reason, 'hit')
  // 步骤 6 detail 记录扫描命中（不含密钥）
  assert.ok(step(r, 6)!.detail.includes('内存扫描命中'))
  assert.ok(!step(r, 6)!.detail.includes(KEY_HEX))
})

test('缓存密钥命中 → 跳过 keyscan（keyscan 未被调用），binding={user_id,data_dir,algorithm}', async () => {
  const h = makeHarness({ storedKey: { keyHex: KEY_HEX, savedAt: '2026-10-14T00:00:00.000Z' } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'db')
  assert.equal(h.loadKeyCalls.length, 1)
  assert.deepEqual(h.loadKeyCalls[0], {
    user_id: DATADIR.user_id,
    data_dir: DATADIR.dir,
    algorithm: 'wxaes128cbc-nonlegacy',
  })
  assert.equal(pyCallsOf(h, 'keyscan').length, 0) // 关键断言：缓存命中不扫描
  assert.ok(step(r, 6)!.detail.includes('keystore'))
  // 解密 stdin = 缓存密钥
  const dec = pyCallsOf(h, 'decrypt')
  assert.equal(dec.length, 1)
  assert.equal(dec[0]!.opts?.stdin, KEY_HEX)
})

test('缓存密钥页 1 核验失败 → clearKey + keyscan + saveKey + 重试解密一次 → mode=db', async () => {
  const h = makeHarness({ storedKey: { keyHex: 'ff'.repeat(16), savedAt: 'x' } })
  let decryptCalls = 0
  const baseFn = h.deps.runLocaldbPyFn!
  h.deps.runLocaldbPyFn = async (args, opts) => {
    if (args[0] === 'decrypt') {
      decryptCalls++
      if (decryptCalls === 1) return pyResult({ ok: false, error: 'KEY_VERIFY_FAILED', stage: 'page-1', detail: '页 1 明文页头校验失败' })
    }
    return baseFn(args, opts)
  }
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'db')
  assert.equal(h.clearKeyCalls, 1) // 仅页 1 核验明确失败才删密钥
  assert.equal(pyCallsOf(h, 'keyscan').length, 1) // 重扫恰好一次
  assert.equal(decryptCalls, 2) // 重试解密一次
  assert.ok(step(r, 6)!.detail.includes('缓存密钥页 1 核验失败已清除'))
  assert.ok(step(r, 7)!.detail.includes('重扫重试通过'))
})

// ---------- 引导阶段（不产出模式） ----------

test('步骤 1 未安装 → 终态报告：success=false、mode=unset、不写状态、不产 mode_decided 日志', async () => {
  const h = makeHarness({ install: null })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.success, false)
  assert.equal(r.localdb_ready, false)
  assert.equal(r.mode_decision.mode, 'unset')
  assert.equal(r.steps.length, 1)
  assert.equal(step(r, 1)!.ok, false)
  assert.equal(step(r, 1)!.kind, undefined) // 引导阶段失败不产出 kind
  assert.ok((r.guidance ?? []).some((g) => g.includes('安装')))
  assert.equal(h.states.length, 0) // 不写状态（引导阶段不产出模式）
  assert.equal(h.events.filter((e) => e.type === 'mode_decided').length, 0)
})

test('步骤 2 未登录（need_login）→ 终态：mode=unset、不写状态、登录引导文案', async () => {
  const h = makeHarness({ loginState: 'need_login' })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.success, false)
  assert.equal(r.mode_decision.mode, 'unset')
  assert.equal(r.steps.length, 2)
  assert.equal(step(r, 2)!.ok, false)
  assert.ok(step(r, 2)!.detail.includes('登录'))
  assert.ok((r.guidance ?? []).some((g) => g.includes('登录')))
  assert.equal(h.states.length, 0)
  assert.equal(h.events.filter((e) => e.type === 'mode_decided').length, 0)
  // 深检阶段未启动：目录/密钥等组件未被触达
  assert.equal(pyCallsOf(h, 'keyscan').length, 0)
})

test('步骤 2 登录态 unknown（探测失败）→ 同终态（非 online 即引导失败）', async () => {
  const h = makeHarness({ loginState: 'unknown' })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.success, false)
  assert.equal(r.mode_decision.mode, 'unset')
  assert.ok(step(r, 2)!.detail.includes('无法确认'))
})

// ---------- 深检阶段失败分类 ----------

test('步骤 3 目录未定位 → ui(transient)，状态写入 mode=ui kind=transient', async () => {
  const h = makeHarness({ dataDir: null })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.success, true)
  assert.equal(r.localdb_ready, false)
  assert.equal(r.mode_decision.mode, 'ui')
  assert.equal(r.mode_decision.kind, 'transient')
  assert.equal(step(r, 3)!.kind, 'transient')
  assert.equal(h.states.length, 1)
  assert.equal(h.states[0]!.mode, 'ui')
  assert.equal(h.states[0]!.kind, 'transient')
  assert.equal(h.events.filter((e) => e.type === 'mode_decided').length, 1)
  assert.ok((r.guidance ?? []).some((g) => g.includes('ui')))
})

test('步骤 3 目录在位但 message.db 缺失 → ui(transient)', async () => {
  const h = makeHarness({ dataDir: { db_paths: { ...DATADIR.db_paths!, message: null } } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'ui')
  assert.equal(r.mode_decision.kind, 'transient')
  assert.ok(step(r, 3)!.detail.includes('message.db'))
})

test('步骤 3 身份未确认：有缓存密钥也不得继续读取', async () => {
  const h = makeHarness({ dataDir: { identity: { ...DATADIR.identity, identity_confirmed: false } },
    storedKey: { keyHex: KEY_HEX, savedAt: 'x' } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'ui')
  assert.ok(r.mode_decision.reason.includes('identity-unconfirmed'))
  assert.equal(h.loadKeyCalls.length, 0)
  assert.equal(h.snapshotCalls.length, 0)
  assert.equal(pyCallsOf(h, 'keyscan').length, 0)
})

test('步骤 3 无窗口 PID、句柄不完整/多账号/错误 PID：明确拒绝，不能借历史目录放行', async () => {
  for (const payload of [
    { ok: false, error: 'HANDLE_QUERY_FAILED' },
    { ok: false, error: 'ACCOUNT_AMBIGUOUS' },
    { ok: true, pid: 999, message_db: DATADIR.db_paths!.message },
    null,
  ]) {
    const h = makeHarness(payload ? { activeAccountPayload: payload } : {})
    if (!payload) h.deps.probeLoginStateFn = async () => ({ login_state: 'online', source: 'localdb-login-driver', window_pid: null, window_error: 'WINDOW_AMBIGUOUS' })
    const r = await runLocaldbDoctor({}, h.deps)
    assert.equal(r.mode_decision.mode, 'ui')
    assert.ok(r.mode_decision.reason.includes('identity-unconfirmed'))
    assert.equal(h.loadKeyCalls.length, 0)
    assert.equal(h.snapshotCalls.length, 0)
    assert.equal(pyCallsOf(h, 'keyscan').length, 0)
    assert.ok((r.guidance ?? []).some(g => g.includes('禁止注入密钥材料')))
    assert.ok((r.guidance ?? []).every(g => !g.includes('重启')))
  }
})

test('步骤 3 当前进程直接证据可用时，旧进程时间不可得不再阻塞', async () => {
  const h = makeHarness({ install: { process_started_at: null } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'db')
  assert.deepEqual(pyCallsOf(h, 'active-account')[0]!.args, ['active-account', '4321'])
})

test('步骤 4 使用操作主窗口实际版本，不复用其他进程或混合记录的版本', async () => {
  const h = makeHarness()
  h.deps.detectInstallFn = async opts => opts?.processPid
    ? { ...INSTALL, version: '9.9.9.9999' }
    : INSTALL // 即使初次枚举 PID 相同，也要对该 PID 定向核对 FileVersion。
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'ui')
  assert.equal(r.mode_decision.version, '9.9.9.9999')
  assert.equal(h.loadKeyCalls.length, 0)
  assert.equal(pyCallsOf(h, 'keyscan').length, 0)
})

test('扫描仅加入同版本候选；复用缓存密钥时不探测其他 PID', async () => {
  for (const cached of [false, true]) {
    const seen: number[] = []
    const h = makeHarness({ pids: [4322, 4323, 4324],
      ...(cached ? { storedKey: { keyHex: KEY_HEX, savedAt: 'x' } } : {}) })
    h.deps.detectInstallFn = async opts => {
      if (!opts?.processPid) return INSTALL
      seen.push(opts.processPid)
      return { ...INSTALL, pid: opts.processPid,
        ...(opts.processPid === 4322 ? { version: '9.9.9.9999' } : {}),
        ...(opts.processPid === 4323 ? { version_source: null } : {}) }
    }
    const r = await runLocaldbDoctor({}, h.deps)
    assert.equal(r.mode_decision.mode, 'db')
    if (cached) assert.deepEqual(seen, [4321])
    else {
      assert.deepEqual(seen, [4321, 4322, 4323, 4324])
      assert.equal(pyCallsOf(h, 'keyscan')[0]!.args[2], '4321,4324')
    }
  }
})

test('步骤 4 版本不在矩阵（unknown）→ ui(compat)', async () => {
  const h = makeHarness({ install: { version: '9.9.9.9999' } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'ui')
  assert.equal(r.mode_decision.kind, 'compat')
  assert.equal(step(r, 4)!.kind, 'compat')
  assert.ok(step(r, 4)!.detail.includes('9.9.9.9999'))
  assert.equal(h.states[0]!.kind, 'compat')
  // 指纹/密钥阶段不应触达
  assert.equal(h.snapshotCalls.length, 0)
})

test('步骤 4 版本 broken → ui(compat)；stale 视为可用继续深检', async () => {
  const broken = makeHarness({
    matrix: { schema_version: 1, entries: { '5.0.11.6029': { ...MATRIX.entries['5.0.11.6029']!, status: 'broken' } } },
  })
  const rb = await runLocaldbDoctor({}, broken.deps)
  assert.equal(rb.mode_decision.kind, 'compat')
  assert.ok(step(rb, 4)!.detail.includes('broken'))

  const stale = makeHarness({
    matrix: { schema_version: 1, entries: { '5.0.11.6029': { ...MATRIX.entries['5.0.11.6029']!, status: 'stale' } } },
  })
  const rs = await runLocaldbDoctor({}, stale.deps)
  assert.equal(rs.mode_decision.mode, 'db')
  assert.ok(step(rs, 4)!.detail.includes('stale'))
})

test('步骤 5 指纹不匹配 → ui(compat)', async () => {
  const h = makeHarness({ fingerprint: { match: false, detail: '魔数不匹配：期望 b3df88da44b8102f，实际 deadbeefdeadbeef' } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.kind, 'compat')
  assert.equal(step(r, 5)!.kind, 'compat')
  assert.ok(step(r, 5)!.detail.includes('魔数'))
})

test('步骤 6 keyscan 未命中（KEY_NOT_FOUND）→ ui(transient) + keyscan_result 日志', async () => {
  const h = makeHarness({ keyscanPayload: { ok: false, error: 'KEY_NOT_FOUND', pids_tried: 2, elapsed_ms: 60000 } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'ui')
  assert.equal(r.mode_decision.kind, 'transient')
  assert.equal(step(r, 6)!.kind, 'transient')
  assert.ok(step(r, 6)!.detail.includes('KEY_NOT_FOUND'))
  assert.equal(h.states[0]!.kind, 'transient')
  const events = h.events.filter((e) => e.type === 'keyscan_result')
  assert.equal(events.length, 1)
  assert.equal(events[0]!.reason, 'KEY_NOT_FOUND')
  // 快照阶段不应触达
  assert.equal(h.snapshotCalls.length, 0)
})

test('步骤 6 keyscan 桥级失败 → ui；tasklist 空时仍可用已确认主窗口 PID', async () => {
  const bridge = makeHarness({ keyscanBridgeFail: '本地库驱动执行超时（>600s），已终止子进程' })
  const rb = await runLocaldbDoctor({}, bridge.deps)
  assert.equal(rb.mode_decision.kind, 'transient')
  assert.ok(step(rb, 6)!.detail.includes('超时'))

  const noPid = makeHarness({ pids: [], install: { pid: null } })
  const rn = await runLocaldbDoctor({}, noPid.deps)
  assert.equal(rn.mode_decision.mode, 'db')
  assert.equal(pyCallsOf(noPid, 'keyscan')[0]!.args[2], '4321')
})

test('步骤 6 扫描命中但 saveKey 失败 → ui(transient)', async () => {
  const h = makeHarness({ saveKeyOk: false })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.kind, 'transient')
  assert.ok(step(r, 6)!.detail.includes('keystore 保存失败'))
})

test('步骤 7 可信快照失败（wal-header-changed）→ 有限重试后仍失败 ui(transient)，工作目录仍被清理', async () => {
  const h = makeHarness({
    snapshotResult: { ok: false, reason: 'wal-header-changed', detail: 'WAL 头变化（捕获窗口内发生 reset）' },
  })
  const r = await runLocaldbDoctor({ snapshotRetryBackoffMs: 1 }, h.deps)
  assert.equal(r.mode_decision.kind, 'transient')
  assert.equal(step(r, 7)!.kind, 'transient')
  assert.ok(step(r, 7)!.detail.includes('wal-header-changed'))
  assert.equal(h.snapshotCalls.length, 3) // §3.3「整体重试（N 次小退避）」：3 次尝试后仍失败才定论
  assert.equal(h.cleanups, 1) // 失败路径 finally 清理
  assert.equal(pyCallsOf(h, 'decrypt').length, 0)
})

test('步骤 7 快照瞬时失败重试自愈：前两次 db-changed 第三次成功 → mode=db，只解密最终快照', async () => {
  const h = makeHarness()
  let captureCalls = 0
  const baseFn = h.deps.captureSnapshotFn!
  h.deps.captureSnapshotFn = async (srcDb, srcWal, workDir) => {
    captureCalls++
    if (captureCalls <= 2) {
      return { ok: false, reason: 'db-changed', detail: `主库大小变化（第 ${captureCalls} 次瞬时竞争）` }
    }
    return baseFn(srcDb, srcWal, workDir)
  }
  const r = await runLocaldbDoctor({ snapshotRetryBackoffMs: 1 }, h.deps)
  assert.equal(r.mode_decision.mode, 'db') // 重试成功不改变结论（快照瞬时失败不进 ui）
  assert.equal(captureCalls, 3)
  assert.equal(pyCallsOf(h, 'decrypt').length, 1) // 只对最终成功的那次快照解密
  assert.ok(step(r, 7)!.ok)
})

test('步骤 7 snapshotMaxAttempts=1 → 单次语义不重试（协议参数可收窄）', async () => {
  const h = makeHarness({
    snapshotResult: { ok: false, reason: 'no-wal', detail: 'WAL 已消失' },
  })
  const r = await runLocaldbDoctor({ snapshotMaxAttempts: 1, snapshotRetryBackoffMs: 1 }, h.deps)
  assert.equal(r.mode_decision.kind, 'transient')
  assert.equal(h.snapshotCalls.length, 1)
})

test('步骤 7 重试前重新判断 WAL 在位（评审 v6）：首次捕获失败后源 WAL 消失 → 重试走无 WAL 捕获成功', async () => {
  const h = makeHarness()
  let existsCalls = 0
  // fs 钩子：首次判定 WAL 在位；捕获失败后退避重试时 WAL 已消失（窗口内 checkpoint+delete）
  h.deps.existsFn = () => {
    existsCalls++
    return existsCalls <= 1
  }
  let captureCalls = 0
  const baseFn = h.deps.captureSnapshotFn!
  h.deps.captureSnapshotFn = async (srcDb, srcWal, workDir) => {
    captureCalls++
    h.snapshotCalls.push({ srcDb, srcWal, workDir })
    if (captureCalls === 1) {
      return { ok: false, reason: 'no-wal', detail: '传入 WAL 路径但捕获窗口内已消失' }
    }
    return baseFn(srcDb, srcWal, workDir) // 第二次 srcWal=null → 合法无 WAL 捕获
  }
  const r = await runLocaldbDoctor({ snapshotRetryBackoffMs: 1 }, h.deps)
  assert.equal(r.mode_decision.mode, 'db')
  assert.equal(captureCalls, 2)
  assert.equal(h.snapshotCalls[0]!.srcWal, `${DATADIR.db_paths!.message}-wal`, '首次尝试按 WAL 在位传路径')
  assert.equal(h.snapshotCalls[1]!.srcWal, null, '重试必须重新判定并进入无 WAL 分支，而非连续 no-wal 失败')
  assert.equal(pyCallsOf(h, 'decrypt')[0]!.args[2], '-') // 无 WAL 解密以 "-" 占位
})

test('步骤 7 解密失败（INTEGRITY_FAILED）→ ui(transient)', async () => {
  const h = makeHarness({ decryptPayload: { ok: false, error: 'INTEGRITY_FAILED', pages: 104, integrity: '*** in database ***' } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.kind, 'transient')
  assert.equal(step(r, 7)!.kind, 'transient')
  assert.ok(step(r, 7)!.detail.includes('INTEGRITY_FAILED'))
  assert.equal(h.cleanups, 1)
})

test('步骤 7 无 WAL 时以 "-" 占位传 decrypt；有 WAL 时传快照路径', async () => {
  const noWal = makeHarness({ walExists: false })
  await runLocaldbDoctor({}, noWal.deps)
  const d1 = pyCallsOf(noWal, 'decrypt')[0]!
  assert.equal(d1.args[2], '-')
  assert.equal(noWal.snapshotCalls[0]!.srcWal, null)

  const withWal = makeHarness({ walExists: true })
  await runLocaldbDoctor({}, withWal.deps)
  const d2 = pyCallsOf(withWal, 'decrypt')[0]!
  assert.ok(d2.args[2]!.endsWith('message.db-wal'))
  assert.equal(withWal.snapshotCalls[0]!.srcWal, `${DATADIR.db_paths!.message}-wal`)
})

test('步骤 8 SCHEMA_UNSUPPORTED → ui(compat)；其他查询失败 → ui(transient)（评审 v6）', async () => {
  // 协议字段用生产者真实错误码（py 侧对缺表/缺列的稳定分类，见 tests/py SchemaUnsupportedTest）
  const schema = makeHarness({
    queryPayload: { ok: false, error: 'SCHEMA_UNSUPPORTED', detail: '解密库 schema 缺少查询所需表/列：no such table: message_table' },
  })
  const rs = await runLocaldbDoctor({}, schema.deps)
  assert.equal(rs.mode_decision.kind, 'compat')
  assert.equal(step(rs, 8)!.kind, 'compat')
  assert.ok(step(rs, 8)!.detail.includes('SCHEMA_UNSUPPORTED'))

  const other = makeHarness({ queryPayload: { ok: false, error: 'DB_UNREADABLE', detail: '解密库只读打开失败' } })
  const ro = await runLocaldbDoctor({}, other.deps)
  assert.equal(ro.mode_decision.kind, 'transient')
  assert.equal(step(ro, 8)!.kind, 'transient')

  const bridge = makeHarness({ queryBridgeFail: '驱动未输出 LOCALDB_JSON 行（exit=1）' })
  const rb = await runLocaldbDoctor({}, bridge.deps)
  assert.equal(rb.mode_decision.kind, 'transient')
})

// ---------- 密钥安全 / 清理 / 就绪块 ----------

test('密钥安全：key_hex 不出现在报告 JSON、任何日志事件、任何步骤 detail', async () => {
  const h = makeHarness()
  const r = await runLocaldbDoctor({}, h.deps)
  const reportJson = JSON.stringify(r)
  assert.ok(!reportJson.includes(KEY_HEX), 'key_hex 不得进入报告')
  const eventsJson = JSON.stringify(h.events)
  assert.ok(!eventsJson.includes(KEY_HEX), 'key_hex 不得进入日志事件')
  for (const s of r.steps) assert.ok(!s.detail.includes(KEY_HEX))
  // decrypt 的 stdin 通道正常携带密钥（内存/管道流转是合法通道）
  assert.equal(pyCallsOf(h, 'decrypt')[0]!.opts?.stdin, KEY_HEX)
})

test('工作目录 finally 清理：成功与失败路径都调用 cleanup；清理失败记 db_fallback 日志', async () => {
  const ok = makeHarness()
  await runLocaldbDoctor({}, ok.deps)
  assert.equal(ok.cleanups, 1)
  assert.equal(ok.events.filter((e) => e.type === 'db_fallback').length, 0)

  const failCleanup = makeHarness()
  const baseCreate = failCleanup.deps.createWorkDirFn!
  failCleanup.deps.createWorkDirFn = () => {
    const w = baseCreate()
    return { ...w!, cleanup: () => (failCleanup.cleanups++, false) }
  }
  await runLocaldbDoctor({}, failCleanup.deps)
  const fb = failCleanup.events.filter((e) => e.type === 'db_fallback')
  assert.equal(fb.length, 1)
  assert.equal(fb[0]!.reason, 'cleanup_failed')
  assert.equal(fb[0]!.stage, 'cleanup')
})

test('工作目录不可定位 → 步骤 7 ui(transient)', async () => {
  const h = makeHarness({ workDirMissing: true })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.kind, 'transient')
  assert.ok(step(r, 7)!.detail.includes('工作目录不可定位'))
})

test('启动清扫：步骤 1 前调用 sweepArtifacts 恰一次，且先于安装检测（1.13 接线）', async () => {
  const h = makeHarness()
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'db')
  assert.equal(h.sweepCalls, 1)
  assert.deepEqual(h.order[0], 'sweep')
  assert.ok(h.order.includes('install'))
  // 无失败条目 → 不产 db_fallback 日志
  assert.equal(h.events.filter((e) => e.type === 'db_fallback').length, 0)
  // 引导失败路径同样先清扫（清扫不依赖深检阶段）
  const guided = makeHarness({ loginState: 'need_login' })
  await runLocaldbDoctor({}, guided.deps)
  assert.equal(guided.sweepCalls, 1)
})

test('启动清扫失败不阻断：failed/抛错 → 记 db_fallback(cleanup/sweep_failed)，深检照常 mode=db', async () => {
  const failed = makeHarness({ sweepFailed: true })
  const rf = await runLocaldbDoctor({}, failed.deps)
  assert.equal(rf.mode_decision.mode, 'db') // 清扫失败不影响深检结论
  assert.equal(rf.steps.length, 8)
  assert.ok(rf.steps.every((s) => s.ok))
  const fb = failed.events.filter((e) => e.type === 'db_fallback')
  assert.equal(fb.length, 1)
  assert.equal(fb[0]!.stage, 'cleanup')
  assert.equal(fb[0]!.reason, 'sweep_failed')
  assert.ok(fb[0]!.detail!.includes('swept=0'))

  const thrown = makeHarness({ sweepThrow: 'sweep boom' })
  const rt = await runLocaldbDoctor({}, thrown.deps)
  assert.equal(rt.mode_decision.mode, 'db')
  const fb2 = thrown.events.filter((e) => e.type === 'db_fallback')
  assert.equal(fb2.length, 1)
  assert.equal(fb2[0]!.stage, 'cleanup')
  assert.equal(fb2[0]!.reason, 'sweep_failed')
  assert.ok(fb2[0]!.detail!.includes('sweep boom'))
})

test('UI 就绪块独立：交互会话失败不阻断本地库深检，双就绪分开呈现（§2）', async () => {
  const h = makeHarness({ env: { interactive_session: false } })
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.ui_ready.ready, false) // UI 块：非交互会话 → 未就绪
  assert.equal(r.ui_ready.interactive_session, false)
  assert.equal(r.localdb_ready, true) // 本地库块：不受交互会话门禁影响
  assert.equal(r.mode_decision.mode, 'db')
})

test('UI 探测抛错 → 就绪块兜底 false，深检不受影响', async () => {
  const h = makeHarness()
  h.deps.probeEnvironmentFn = async () => {
    throw new Error('env boom')
  }
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.ui_ready.ready, false)
  assert.equal(r.mode_decision.mode, 'db')
})

test('步骤 elapsed 计时：注入时钟下每步 elapsed_ms ≥ 0 且 checked_at 为 ISO', async () => {
  const h = makeHarness()
  const r = await runLocaldbDoctor({}, h.deps)
  for (const s of r.steps) assert.ok(s.elapsed_ms >= 0)
  assert.ok(!Number.isNaN(Date.parse(r.mode_decision.checked_at)))
})

test('state 写盘失败不阻断结论：state_saved=false，guidance 写明「启用未完成」且不得出现「已启用」（评审 v6/v7）', async () => {
  const h = makeHarness()
  h.deps.writeLocalStateFn = () => false
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.mode_decision.mode, 'db')
  assert.equal(r.state_saved, false, '报告必须带 state_saved=false 字段')
  // 用户可见提示：不得只靠内部日志——guidance 必须写明保存失败与后续回退
  const gs = r.guidance ?? []
  assert.ok(
    gs.some((g) => g.includes('本地库状态保存失败') && g.includes('启用未完成') && g.includes('回退')),
    `guidance 应含用户可见提示：${JSON.stringify(r.guidance)}`,
  )
  // 评审 v7：能力检测通过（db 模式检测成功）但保存失败 → 只表达能力通过+启用未完成
  assert.ok(
    gs.some((g) => g.includes('能力检测通过') && g.includes('启用未完成')),
    `guidance 应表达能力检测通过但启用未完成：${JSON.stringify(r.guidance)}`,
  )
  // 评审 v7 禁令：「已启用」与「启用未完成」不得同时出现；state_saved=false 时不得出现「已启用」
  const hasEnabled = gs.some((g) => g.includes('已启用'))
  const hasUnfinished = gs.some((g) => g.includes('启用未完成'))
  assert.ok(!(hasEnabled && hasUnfinished), `「已启用」与「启用未完成」不得并存：${JSON.stringify(r.guidance)}`)
  assert.equal(hasEnabled, false, 'state_saved=false 时 guidance 不得声称「已启用」')
  const decided = h.events.find((e) => e.type === 'mode_decided')!
  assert.ok(decided.detail!.includes('state_write=failed'))
})

test('state 写盘成功 → state_saved=true，无保存失败提示', async () => {
  const h = makeHarness()
  const r = await runLocaldbDoctor({}, h.deps)
  assert.equal(r.state_saved, true)
  assert.ok(!(r.guidance ?? []).some((g) => g.includes('状态保存失败')))
})

// ---------- CLI 接线 ----------

function captureConsole(fn: () => Promise<number>): Promise<{ code: number; out: string }> {
  const chunks: string[] = []
  const orig = console.log
  console.log = (...args: unknown[]) => {
    chunks.push(args.map(String).join(' '))
  }
  return fn().then(
    (code) => {
      console.log = orig
      return { code, out: chunks.join('\n') }
    },
    (err) => {
      console.log = orig
      throw err
    },
  )
}

const CLI_ENV = { platform: 'win32' as const, sessionName: 'Console', execFileFn: async () => ({ stdout: 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' }) }

test('CLI doctor --localdb：输出双就绪块 + 步骤列表 + 模式结论；--json 含 localdb 全字段', async () => {
  const h = makeHarness()
  const text = await captureConsole(() =>
    doctorCommand({ env: CLI_ENV, localdb: true, localdbDeps: h.deps, localdbStateDir: null }),
  )
  assert.equal(text.code, 0) // 深检结论 db → 0
  assert.ok(text.out.includes('本地库就绪'))
  assert.ok(text.out.includes('UI 就绪'))
  assert.ok(text.out.includes('mode=db'))
  assert.ok(text.out.includes('1. 企业微信安装检测'))
  assert.ok(text.out.includes('8. 查询冒烟'))

  const json = await captureConsole(() =>
    doctorCommand({ json: true, env: CLI_ENV, localdb: true, localdbDeps: makeHarness().deps, localdbStateDir: null }),
  )
  assert.equal(json.code, 0)
  const parsed = JSON.parse(json.out) as { checks: unknown[]; localdb: LocaldbDoctorReport }
  assert.ok(Array.isArray(parsed.checks))
  assert.equal(parsed.localdb.mode_decision.mode, 'db')
  assert.equal(parsed.localdb.steps.length, 8)
})

test('CLI doctor --localdb：深检结论 ui → 退出 0 + 醒目提示；引导失败（未登录）→ 退出 1', async () => {
  const uiRun = await captureConsole(() =>
    doctorCommand({ env: CLI_ENV, localdb: true, localdbDeps: makeHarness({ keyscanPayload: { ok: false, error: 'KEY_NOT_FOUND', pids_tried: 2, elapsed_ms: 1 } }).deps, localdbStateDir: null }),
  )
  assert.equal(uiRun.code, 0) // 深检结论 ui 也算诊断完成
  assert.ok(uiRun.out.includes('⚠️ 本地库通道未就绪'))
  assert.ok(uiRun.out.includes('kind=transient'))

  const guidance = await captureConsole(() =>
    doctorCommand({ env: CLI_ENV, localdb: true, localdbDeps: makeHarness({ loginState: 'need_login' }).deps, localdbStateDir: null }),
  )
  assert.equal(guidance.code, 1) // 引导阶段失败 → 1
  assert.ok(guidance.out.includes('登录'))
})

test('CLI doctor（无 --localdb）：state 缺失时尾部建议运行 --localdb', async () => {
  const { readLocalState } = await import('../src/platform/localdb/matrix.js')
  const stateMissing = readLocalState('Z:\\definitely-not-exists\\dir') === null
  const text = await captureConsole(() => doctorCommand({ env: CLI_ENV, localdbStateDir: 'Z:\\definitely-not-exists\\dir' }))
  assert.equal(text.code, 0)
  if (stateMissing) {
    assert.ok(text.out.includes('doctor --localdb'))
  }
})
