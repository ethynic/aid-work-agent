/**
 * 受信配置检查工具单测（代理任务 P2；P1 契约 §2 已冻结 schema，测试即契约断言）。
 *
 * 覆盖：
 * - wecom_config_doctor：深检结论 db → data 结构与就绪字段；深检结论 ui（诊断成功但
 *   localdb_ready=false）→ data 如实返回；引导阶段失败（未登录）→ success=false + steps +
 *   guidance 结构化返回；身份可确认 → self_user_id 存在；身份不可确认 → 字段缺省且不报错；
 * - wecom_config_calibrate：成功 → 双点坐标 + 屏幕上下文 + self_user_id 且 data 不含
 *   cache_path；身份预核失败 → IDENTITY_UNCONFIRMED；驱动失败 → INTERNAL_ERROR；
 * - 工具注册：两工具进 MCP 工具定义清单与 OPERATIONS 注册表，annotations 语义正确
 *   （doctor 只读幂等 / calibrate 写类），无 CLI 动词（registry-only，沿
 *   wecom_session_directory 页面专用先例）。
 *
 * doctor 全 mock（沿 localdb-doctor.test.ts 的组件注入模式，零真实进程/文件依赖；
 * runDoctorChecks 的 env/localAppData 走 DoctorCommandOptions 既有 seam）。
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { existsSync } from 'node:fs'
import { DRIVER_PATH } from '../src/cli/commands/calibrate.js'
import {
  createWecomConfigCalibrateOperation,
  createWecomConfigDoctorOperation,
  CALIBRATE_DRIVER_PATH,
} from '../src/operations/configCheck.js'
import { CodedOperationError, type OperationResult, type OpContext } from '../src/operations/types.js'
import { getOperationEntry, OPERATIONS } from '../src/operations/registry.js'
import { TOOL_DEFS, TOOL_NAMES } from '../src/mcp/toolDefs.js'
import { jsonSchemaOf } from '../src/mcp/manifest.js'
import type { LocaldbDoctorDeps, LocaldbDoctorReport } from '../src/platform/localdb/doctorLocaldb.js'
import type { DoctorWorkDir } from '../src/platform/localdb/doctorLocaldb.js'
import type { InstallInfo } from '../src/platform/localdb/version.js'
import type { RunningInfo } from '../src/platform/localdb/running.js'
import type { DataDirInfo } from '../src/platform/localdb/datadir.js'
import type { LocalDbState, VersionMatrix } from '../src/platform/localdb/matrix.js'
import type { DbFingerprintExpected, DbFingerprintResult } from '../src/platform/localdb/fingerprint.js'
import type { CaptureSnapshotResult } from '../src/platform/localdb/snapshot.js'
import type { CurrentAccount } from '../src/platform/localdb/identity.js'
import type { LocaldbPyResult, RunLocaldbPyOpts } from '../src/platform/localdb/driver.js'
import type { EnvironmentProbe } from '../src/platform/environment.js'

// ---------- 固定夹具（与 localdb-doctor.test.ts 同源的精简版） ----------

const KEY_HEX = 'ab12cd34ef56ab12cd34ef56ab12cd34' // 32 hex（测试夹具，非真实密钥）
const ACCOUNT = '1688850000000001'

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
  process_started_at: 1_760_000_000_000,
  install_dir: 'C:\\Program Files (x86)\\WXWork\\5.0.11.6029',
  upgrade_signals: [],
}

const RUNNING: RunningInfo = { running: true, pids: [4321], probe: 'ok' }

const DATADIR: DataDirInfo = {
  source: 'cef-log',
  root: 'C:\\Users\\t\\OneDrive\\文档\\WXWork',
  user_id: ACCOUNT,
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

const ENV_PROBE: EnvironmentProbe = {
  platform_ok: true,
  platform: 'win32',
  interactive_session: true,
  session_name: 'Console',
  powershell_available: true,
  powershell_path: 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe',
  wecom_running: true,
  wecom_processes: 2,
}

const FINGERPRINT_OK: DbFingerprintResult = {
  match: true,
  detail: '魔数与明文页头均匹配',
  observed: { magic: 'b3df88da44b8102f', plaintext_header: '100002020004020 20'.replace(' ', '') },
}

const CURRENT: CurrentAccount = { account: ACCOUNT, windowPid: 4321, dataDir: DATADIR }

const pyResult = (payload: Record<string, unknown>): LocaldbPyResult => ({ ok: true, data: payload, raw_last_line: null })

/** doctor 深检全 mock 依赖（默认八步全过 → mode=db；overrides 覆盖失败组合） */
function makeDoctorDeps(
  over: {
    loginState?: 'online' | 'need_login' | 'offline'
    fingerprint?: Partial<DbFingerprintResult>
    env?: Partial<EnvironmentProbe>
  } = {},
): LocaldbDoctorDeps {
  return {
    detectInstallFn: async () => INSTALL,
    detectRunningFn: async () => RUNNING,
    probeLoginStateFn: async () => ({ login_state: over.loginState ?? 'online', source: 'localdb-login-driver', window_pid: 4321 }),
    locateDataDirFn: async () => DATADIR,
    loadMatrixFn: () => MATRIX,
    lookupVersionFn: (matrix: VersionMatrix | null, version: string | null | undefined) => {
      const entry = matrix !== null && typeof version === 'string' ? matrix.entries[version] : undefined
      return entry ? { entry, status: 'validated' as const } : { entry: null, status: 'unknown' as const }
    },
    checkDbFingerprintFn: (_dbPath: string, _expected: DbFingerprintExpected) => ({ ...FINGERPRINT_OK, ...over.fingerprint }),
    loadKeyFn: async () => ({ keyHex: KEY_HEX, savedAt: '2026-10-14T00:00:00.000Z' }),
    saveKeyFn: async () => true,
    clearKeyFn: () => true,
    runLocaldbPyFn: async (args: string[], _opts?: RunLocaldbPyOpts) => {
      const mode = args[0]
      if (mode === 'active-account') return pyResult({ ok: true, pid: 4321, message_db: DATADIR.db_paths!.message })
      if (mode === 'decrypt') return pyResult({ ok: true, pages: 104, wal_frames: 3, stale_tail_frames: 0, integrity: 'ok', out_db: 'X\\message.dec.db' })
      if (mode === 'query') return pyResult({ ok: true, task: 'conversations', conversations: [{ conversation_id: 'c1', count: 42 }] })
      throw new Error(`unexpected py mode: ${String(mode)}`)
    },
    captureSnapshotFn: async (_srcDb: string, srcWal: string | null, workDir: string) =>
      ({
        ok: true,
        dbSnapshot: `${workDir}\\message.db`,
        walSnapshot: srcWal === null ? null : `${workDir}\\message.db-wal`,
        capture: { h0: 'aa', h1: 'aa', sizes: { db_size: 425984, wal_size: 32768 } },
      }) as CaptureSnapshotResult,
    sweepArtifactsFn: () => ({ swept: [], skipped: [], failed: [] }),
    probeEnvironmentFn: async () => ({ ...ENV_PROBE, ...over.env }),
    writeLocalStateFn: (_state: LocalDbState) => true,
    logLocaldbEventFn: () => true,
    createWorkDirFn: (): DoctorWorkDir => ({ dir: 'W:\\art\\doctor_run1', run_id: 'doctor_run1', cleanup: () => true }),
    existsFn: () => true,
    now: () => new Date(1_700_000_000_000),
  }
}

/** runDoctorChecks 的 env 注入（Console 交互会话 + PowerShell 可用 + 企微运行中） */
const ENV_INJECT = {
  platform: 'win32' as NodeJS.Platform,
  sessionName: 'Console',
  execFileFn: async (file: string) => ({
    stdout:
      file === 'where.exe'
        ? 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'
        : '"WXWork.exe","123"',
  }),
}

const noopCtx = (): OpContext => ({ signal: new AbortController().signal, progress: () => {} })

async function runDoctorTool(deps: Parameters<typeof createWecomConfigDoctorOperation>[0]): Promise<OperationResult> {
  return createWecomConfigDoctorOperation(deps).execute({}, noopCtx())
}

const localdbOf = (r: OperationResult) => r.data.localdb as LocaldbDoctorReport

// ---------- wecom_config_doctor ----------

test('doctor 深检结论 db：data 结构与就绪字段齐备，self_user_id 附带', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-cfg-doctor-db-'))
  try {
    const r = await runDoctorTool({ env: ENV_INJECT, localAppData: home, localdbDeps: makeDoctorDeps(), probeAccountFn: async () => CURRENT })
    assert.equal(r.success, true)
    assert.equal(r.code, 'OK')
    assert.equal(r.effect, 'none')
    assert.equal(r.data.schema_version, 1)
    assert.equal(r.data.self_user_id, ACCOUNT)
    assert.equal(r.data.success, true)
    assert.ok(Array.isArray(r.data.checks) && r.data.checks.length > 0)
    const readiness = r.data.runtime_readiness as { ready: boolean; reason?: string }
    assert.equal(readiness.ready, true)
    const localdb = localdbOf(r)
    assert.equal(localdb.success, true)
    assert.equal(localdb.localdb_ready, true)
    assert.equal(localdb.mode_decision.mode, 'db')
    assert.equal(localdb.state_saved, true)
    assert.equal(localdb.steps.length, 8)
    assert.ok(Array.isArray(localdb.guidance) && localdb.guidance.length > 0)
    // 契约密钥安全：报告不携带 key_hex
    assert.equal(JSON.stringify(r.data).includes(KEY_HEX), false)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('doctor 深检结论 ui：诊断完成（success=true）但 localdb_ready=false 如实返回', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-cfg-doctor-ui-'))
  try {
    const r = await runDoctorTool({
      env: ENV_INJECT,
      localAppData: home,
      localdbDeps: makeDoctorDeps({ fingerprint: { match: false, detail: '库指纹不匹配' } }),
      probeAccountFn: async () => CURRENT,
    })
    // 诊断成功（对应 CLI 退出 0），本地库未就绪是服务端拒绝创建检查的依据，如实透出
    assert.equal(r.success, true)
    const localdb = localdbOf(r)
    assert.equal(localdb.success, true)
    assert.equal(localdb.localdb_ready, false)
    assert.equal(localdb.mode_decision.mode, 'ui')
    assert.equal(localdb.mode_decision.kind, 'compat')
    assert.equal(localdb.state_saved, true)
    assert.ok(localdb.steps.some((s) => !s.ok))
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('doctor 引导阶段失败（未登录）：success=false + steps + guidance 结构化返回，不抛 invocation 错误', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-cfg-doctor-login-'))
  try {
    const r = await runDoctorTool({
      env: ENV_INJECT,
      localAppData: home,
      localdbDeps: makeDoctorDeps({ loginState: 'need_login' }),
      probeAccountFn: async () => CURRENT,
    })
    assert.equal(r.success, true, '工具不因诊断内容失败而变成 invocation 错误')
    assert.equal(r.code, 'OK')
    const localdb = localdbOf(r)
    assert.equal(localdb.success, false)
    assert.equal(localdb.localdb_ready, false)
    assert.equal(localdb.mode_decision.mode, 'unset')
    assert.equal(localdb.state_saved, false)
    assert.ok(localdb.steps.length > 0)
    assert.ok(localdb.guidance !== undefined && localdb.guidance.length > 0)
    assert.match(localdb.mode_decision.reason, /引导未完成/)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('doctor 身份不可确认：self_user_id 缺省且工具不报错', async () => {
  const home = mkdtempSync(join(tmpdir(), 'wc-cfg-doctor-id-'))
  try {
    const r = await runDoctorTool({
      env: ENV_INJECT,
      localAppData: home,
      localdbDeps: makeDoctorDeps(),
      probeAccountFn: async () => {
        throw new CodedOperationError('IDENTITY_UNCONFIRMED', '无法唯一确认当前操作主窗口与账号')
      },
    })
    assert.equal(r.success, true)
    assert.equal(r.code, 'OK')
    assert.equal('self_user_id' in r.data, false, '身份不可确认时字段缺省')
    assert.equal(localdbOf(r).localdb_ready, true)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

// ---------- wecom_config_calibrate ----------

/** 驱动原始结果（含本机 cache_path——契约规定不进入 data） */
const DRIVER_RAW = {
  title_point: { x: 1310, y: 218 },
  input_point: { x: 1310, y: 976 },
  width: 2560,
  height: 1440,
  dpi: 96,
  cache_path: 'C:\\Users\\t\\AppData\\Local\\AidWorkAgent\\wecom-cli\\send-points.json',
}

function makeCalibrateDeps(over: {
  probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
  verifyIdentityFn?: (current: CurrentAccount, ctx: OpContext) => Promise<string>
  runDriverFn?: (opts: { script: string; args?: string[]; signal?: AbortSignal }) => Promise<Record<string, unknown>>
  prepareArtifactDirFn?: () => string | null
} = {}) {
  return {
    probeAccountFn: over.probeAccountFn ?? (async () => CURRENT),
    verifyIdentityFn: over.verifyIdentityFn ?? (async () => '测试本人'),
    runDriverFn:
      over.runDriverFn ??
      (async (opts) => {
        assert.equal(opts.script, CALIBRATE_DRIVER_PATH)
        assert.deepEqual(opts.args, ['-SelfName', '测试本人', '-ArtifactDir', 'W:\\art\\calib'])
        return DRIVER_RAW
      }),
    prepareArtifactDirFn: over.prepareArtifactDirFn ?? (() => 'W:\\art\\calib'),
  }
}

async function runCalibrateTool(deps: ReturnType<typeof makeCalibrateDeps>): Promise<OperationResult> {
  return createWecomConfigCalibrateOperation(deps).execute({}, noopCtx())
}

test('calibrate 成功：双点坐标 + 屏幕上下文 + self_user_id，data 不含 cache_path', async () => {
  const r = await runCalibrateTool(makeCalibrateDeps())
  assert.equal(r.success, true)
  assert.equal(r.code, 'OK')
  assert.equal(r.message, '企微标题与输入区双点标定完成')
  assert.equal(r.effect, 'applied', '写类效果：标定完成=applied')
  assert.equal(r.data.schema_version, 1)
  assert.equal(r.data.self_user_id, ACCOUNT)
  assert.deepEqual(r.data.title_point, { x: 1310, y: 218 })
  assert.deepEqual(r.data.input_point, { x: 1310, y: 976 })
  assert.equal(r.data.width, 2560)
  assert.equal(r.data.height, 1440)
  assert.equal(r.data.dpi, 96)
  assert.equal('cache_path' in r.data, false, '驱动原始结果中的 cache_path 不得进入 data')
  assert.equal(JSON.stringify(r.data).includes('send-points.json'), false)
})

test('calibrate 身份预核失败：IDENTITY_UNCONFIRMED，未执行驱动', async () => {
  let driverCalls = 0
  const r = await runCalibrateTool(
    makeCalibrateDeps({
      probeAccountFn: async () => {
        throw new CodedOperationError('IDENTITY_UNCONFIRMED', '无法唯一确认当前操作主窗口与账号')
      },
      runDriverFn: async () => {
        driverCalls++
        return DRIVER_RAW
      },
    }),
  )
  assert.equal(r.success, false)
  assert.equal(r.code, 'IDENTITY_UNCONFIRMED')
  assert.equal(r.effect, 'none', '未发出标定动作，effect=none')
  assert.equal(driverCalls, 0, '身份预核失败不执行驱动')
})

test('calibrate 本人显示名未确认：IDENTITY_UNCONFIRMED', async () => {
  const r = await runCalibrateTool(
    makeCalibrateDeps({
      verifyIdentityFn: async () => {
        throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本地库本人显示名未确认，未执行坐标标定')
      },
    }),
  )
  assert.equal(r.success, false)
  assert.equal(r.code, 'IDENTITY_UNCONFIRMED')
})

test('calibrate 驱动失败：INTERNAL_ERROR，effect=unknown（动作已发出、结果不可知）', async () => {
  const r = await runCalibrateTool(
    makeCalibrateDeps({
      runDriverFn: async () => {
        throw new CodedOperationError('INTERNAL_ERROR', '驱动脚本异常退出（exit=1）')
      },
    }),
  )
  assert.equal(r.success, false)
  assert.equal(r.code, 'INTERNAL_ERROR')
  assert.equal(r.effect, 'unknown')
})

test('calibrate 标定记录目录不可定位：INTERNAL_ERROR', async () => {
  const r = await runCalibrateTool(makeCalibrateDeps({ prepareArtifactDirFn: () => null }))
  assert.equal(r.success, false)
  assert.equal(r.code, 'INTERNAL_ERROR')
})

// ---------- 工具注册 ----------

test('两工具进 MCP 工具定义清单与 OPERATIONS 注册表，annotations 语义正确', () => {
  for (const name of ['wecom_config_doctor', 'wecom_config_calibrate']) {
    assert.ok(TOOL_NAMES.includes(name), `${name} 应出现在 TOOL_DEFS`)
    const def = TOOL_DEFS.find((t) => t.name === name)!
    assert.deepEqual(Object.keys(def.zodShape), [], '无输入参数')
    assert.equal(jsonSchemaOf(def).type, 'object')
    // 注册表 fail-loud 守卫：getOperationEntry 不抛即 toolDefs ↔ registry 未漂移
    const entry = getOperationEntry(name)
    assert.equal(entry.operation.name, name)
  }
  const doctor = TOOL_DEFS.find((t) => t.name === 'wecom_config_doctor')!.annotations
  assert.equal(doctor.readOnlyHint, true, 'doctor 只读诊断')
  assert.equal(doctor.idempotentHint, true)
  const calibrate = TOOL_DEFS.find((t) => t.name === 'wecom_config_calibrate')!.annotations
  assert.equal(calibrate.readOnlyHint, false, 'calibrate 写类')
  assert.equal(calibrate.idempotentHint, false)
  // cli.write 元数据与注解一致（calibrate 驱动企微 UI，CLI 场景需互斥提示）
  assert.equal(OPERATIONS.wecom_config_doctor!.cli.write, false)
  assert.equal(OPERATIONS.wecom_config_calibrate!.cli.write, true)
})

test('calibrate 驱动脚本路径有效且与 CLI calibrate 命令同源', () => {
  // CLI calibrate 命令（src/cli/commands/calibrate.ts）的 DRIVER_PATH 在编译后解析为
  // 同一物理文件；两处入口必须同源，避免驱动脚本路径漂移
  assert.equal(CALIBRATE_DRIVER_PATH, DRIVER_PATH)
  assert.equal(existsSync(CALIBRATE_DRIVER_PATH), true)
})
