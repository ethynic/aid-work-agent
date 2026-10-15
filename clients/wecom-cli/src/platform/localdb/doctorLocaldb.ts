/**
 * 1.11 doctor 深检编排（设计 §5 逐字遵循；深检步骤 n ↔ 组件 Cn 一一对应）。
 *
 * 两阶段语义（§5）：
 * - 引导阶段（步骤 1-2）：不产出模式——失败即终态报告（success=false，
 *   mode_decision.mode='unset'），不写 localdb-state.json、不产 mode_decided 日志
 *   （"保持 unset" = 不做任何 mode 字段变更，§2 状态转移表）；
 * - 深检阶段（步骤 3-8）：每次必产出 db/ui 之一；kind 按错误事实分类（评审 v5）：
 *   目录暂缺=transient、版本不在矩阵/指纹漂移/已确认 schema 不支持=compat、
 *   密钥扫描/快照解密/其他查询失败=transient。全过 → mode=db；
 *   结论写 localdb-state.json（{mode,kind?,reason,version,checked_at}）+ mode_decided 日志。
 *
 * 密钥安全（§8）：key_hex 只在进程内存与 runLocaldbPy stdin 管道流转——不进 steps[].detail、
 * 不进日志、不进报告（runLocaldbPy 返回的 raw_last_line 可能含协议载荷如 key_hex，
 * 绝不写入任何输出；keyscan 命中后的持久化走 saveKey 组件）。
 * 「先复用后扫描」（§5 步骤 6 / §3.4）：loadKey(binding) 命中直接用（页 1 先知核验在
 * decrypt 内）；decrypt 返回 KEY_VERIFY_FAILED 才认定缓存密钥失效——clearKey → keyscan →
 * saveKey → 重试解密一次（仅页 1 核验明确失败才删密钥；快照失败不删、不归因密钥；
 * keyscan 命中的密钥自带页 1 验证，不重试）。
 *
 * 临时工作目录（§7）：快照/解密产物位于 <artifacts>/localdb/doctor_<ts>_<pid>/
 * （两段发布：暂存目录写 owner.json 后原子 rename，最终名目录 owner 恒在，兼容 1.13
 * sweepArtifacts 的 owner 存活语义；残留暂存目录由清扫按内嵌 pid 死活兜底，评审 v7），
 * finally 清理——成功失败都删；清理失败记 db_fallback(stage=cleanup) 日志。深检开始时
 * （步骤 1 之前）先对同一 localdb 产物子树做一次启动遗留清扫（owner 存活校验，不误删并行
 * 调用的活动产物；清扫失败同样只记 db_fallback(stage=cleanup,reason=sweep_failed)，不阻断）。
 * run_id 子目录语义已就位，取消路径（AbortSignal）接线留组合任务（driver.ts kill 语义）。
 *
 * 退出码语义（CLI doctor --localdb 注释同）：doctor 是诊断命令——深检结论 ui 也算
 * "成功完成检查"（success=true，退出 0 但醒目提示）；success=false 仅表示引导阶段失败
 * （未安装/未登录/登录态无法确认），即检查本身没能完成。
 *
 * deps 全组件可注入（每一步失败组合可模拟，测试零真实进程/文件依赖）。
 */
import { existsSync, mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'
import { detectInstall, type InstallInfo, type DetectInstallDeps } from './version.js'
import { detectRunning, type RunningInfo } from './running.js'
import { locateDataDir, type DataDirInfo, type LocateDataDirDeps } from './datadir.js'
import {
  loadMatrix,
  lookupVersion,
  writeLocalState,
  type LocalDbState,
  type MatrixEntry,
  type VersionLookupResult,
  type VersionMatrix,
} from './matrix.js'
import { checkDbFingerprint, type DbFingerprintExpected, type DbFingerprintResult } from './fingerprint.js'
import { captureSnapshot, type CaptureSnapshotResult } from './snapshot.js'
import { clearKey, loadKey, saveKey, type KeyBinding, type StoredKey } from './keystore.js'
import { probeLoginState, type LoginProbeResult } from './login.js'
import { logLocaldbEvent, type LocaldbLogEvent } from './log.js'
import { localdbArtifactsDir, sweepArtifacts, OWNER_FILE_NAME } from './cleanup.js'
import { runLocaldbPy, type LocaldbPyResult, type RunLocaldbPyOpts } from './driver.js'
import { probeEnvironment, type EnvironmentProbe } from '../environment.js'

/** 深检失败分类（§2：compat=兼容性明确失败，transient=临时不可用可自愈） */
export type LocaldbDoctorKind = 'compat' | 'transient'

/** 单个深检步骤记录（kind 仅深检阶段失败时有值；引导阶段失败不产出 kind） */
export interface LocaldbDoctorStep {
  step: number
  name: string
  ok: boolean
  kind?: LocaldbDoctorKind
  detail: string
  elapsed_ms: number
}

/** UI/OCR 就绪块（独立于本地库就绪；§2 双就绪分开报告，本地库不继承交互会话门禁） */
export interface LocaldbUiReadiness {
  ready: boolean
  platform_ok: boolean
  /** null = 非 win32 无法判定 */
  interactive_session: boolean | null
  powershell_available: boolean
}

/** 模式结论（与 localdb-state.json 同 shape；引导阶段失败时 mode='unset' 且不落盘） */
export interface LocaldbModeDecision {
  mode: 'db' | 'ui' | 'unset'
  kind?: LocaldbDoctorKind
  reason: string
  version: string | null
  checked_at: string
}

export interface LocaldbDoctorReport {
  /** 诊断命令自身是否完成：false 仅限引导阶段失败（未安装/未登录），深检结论 ui 仍为 true */
  success: boolean
  /** 本地库通道就绪 = mode_decision.mode === 'db' */
  localdb_ready: boolean
  ui_ready: LocaldbUiReadiness
  steps: LocaldbDoctorStep[]
  mode_decision: LocaldbModeDecision
  /**
   * localdb-state.json 本次结论是否写入成功（评审 v6）：引导阶段未写状态 → false；
   * 深检结论写入失败 → false 且 guidance 必须写明「启用未完成」。
   */
  state_saved: boolean
  guidance?: string[]
}

/** 深检临时工作目录（§7：finally 清理；owner.json 兼容 1.13 sweepArtifacts） */
export interface DoctorWorkDir {
  dir: string
  run_id: string
  /** 删除整个 run 目录；true=已删除或本就不存在，false=清理失败（调用方记日志） */
  cleanup(): boolean
}

export type CreateWorkDirFn = () => DoctorWorkDir | null

/**
 * 默认工作目录实现（评审 v7 两段发布）：<workBaseDir?>/doctor_<ts>_<pid>/，内写 owner.json
 * {pid, started_at}（1.13 sweepArtifacts 的存活判定依据——本进程存活期间另一调用的清扫会
 * 跳过本目录）。发布协议：先建暂存目录 <final名>.creating-<pid>-<ts> 并在其中写 owner.json，
 * 再原子 rename 为最终名——最终名目录自出现起 owner 恒在，并行清扫的「owner ENOENT=从未
 * 有主即删」不会撞上 mkdir 与写 owner 之间的空窗（旧实现两步分离可被并行清扫误删活动目录，
 * 评审 v7）；残留暂存目录由 sweepArtifacts 按名字内嵌 pid 死活清扫（cleanup.ts）。
 * workBaseDir 三态：undefined → localdbArtifactsDir()（环境变量定位）；null → 不可用（返回
 * null，深检步骤 7 按 transient 失败）；字符串 → 显式注入根（测试/定制布局）。
 */
export function defaultCreateWorkDir(workBaseDir?: string | null): DoctorWorkDir | null {
  const base = workBaseDir === undefined ? localdbArtifactsDir() : workBaseDir
  if (!base) return null
  const nowMs = Date.now()
  const runId = `doctor_${nowMs}_${process.pid}`
  const dir = join(base, runId)
  const staging = `${dir}.creating-${process.pid}-${nowMs}`
  try {
    mkdirSync(staging, { recursive: true })
    writeFileSync(
      join(staging, OWNER_FILE_NAME),
      `${JSON.stringify({ pid: process.pid, started_at: new Date(nowMs).toISOString() }, null, 2)}\n`,
      'utf8',
    )
    renameSync(staging, dir) // 原子发布：最终名出现时 owner.json 必已完整
  } catch {
    // 发布失败：清掉暂存目录（best-effort），不留半成品；清理失败可忽略——暂存目录带本
    // 存活 pid 标记，后续清扫按 pid 死活兜底
    try {
      rmSync(staging, { recursive: true, force: true })
    } catch {
      // 忽略（见上）
    }
    return null
  }
  return {
    dir,
    run_id: runId,
    cleanup(): boolean {
      try {
        rmSync(dir, { recursive: true, force: true })
        return true
      } catch {
        return false
      }
    },
  }
}

export interface LocaldbDoctorOptions {
  /** keyscan 子进程超时（分钟级内存扫描，缺省 10 分钟；慢机/大内存可放宽） */
  keyscanTimeoutMs?: number
  /** keyscan --max-region-bytes 透传（缺省用 py 侧 DEFAULT_MAX_REGION_BYTES） */
  keyscanMaxRegionBytes?: number
  /** decrypt/query 子进程超时（缺省 DEFAULT_LOCALDB_PY_TIMEOUT_MS） */
  pyTimeoutMs?: number
  /**
   * 可信快照捕获总尝试次数（缺省 SNAPSHOT_MAX_ATTEMPTS=3）。
   * §3.3 捕获协议步骤 3：「任一不满足 → 整体重试（N 次小退避）」——快照捕获组件是单次语义，
   * 有限重试归本编排层（operation 层）；WXWork 活跃写库期间 db-changed/wal-shrunk 属瞬时事件，
   * 不重试会把可自愈的瞬时竞争误判为 ui(transient)。
   */
  snapshotMaxAttempts?: number
  /** 快照重试退避毫秒（缺省 SNAPSHOT_RETRY_BACKOFF_MS=300，§2 秒级短退避；测试可注入 1ms） */
  snapshotRetryBackoffMs?: number
}

export interface LocaldbDoctorDeps {
  // ---- 组件注入（缺省为真实实现；测试全 mock） ----
  detectInstallFn?: (deps?: DetectInstallDeps) => Promise<InstallInfo>
  detectRunningFn?: () => Promise<RunningInfo>
  probeLoginStateFn?: () => Promise<LoginProbeResult>
  locateDataDirFn?: (deps: LocateDataDirDeps) => Promise<DataDirInfo>
  loadMatrixFn?: () => VersionMatrix | null
  lookupVersionFn?: (matrix: VersionMatrix | null, version: string | null | undefined) => VersionLookupResult
  checkDbFingerprintFn?: (dbPath: string, expected: DbFingerprintExpected) => DbFingerprintResult
  loadKeyFn?: (binding: KeyBinding) => Promise<StoredKey | null>
  saveKeyFn?: (keyHex: string, binding: KeyBinding) => Promise<boolean>
  clearKeyFn?: () => boolean
  runLocaldbPyFn?: (args: string[], opts?: RunLocaldbPyOpts) => Promise<LocaldbPyResult>
  captureSnapshotFn?: (srcDb: string, srcWal: string | null, workDir: string) => Promise<CaptureSnapshotResult>
  /** 启动遗留清扫（缺省 sweepArtifacts；baseDir 与 workBaseDir 同源定位 localdb 产物子树） */
  sweepArtifactsFn?: () => { swept: string[]; skipped: string[]; failed: Array<{ dir: string; error: string }> }
  probeEnvironmentFn?: () => Promise<EnvironmentProbe>
  writeLocalStateFn?: (state: LocalDbState) => boolean
  logLocaldbEventFn?: (event: LocaldbLogEvent) => boolean
  createWorkDirFn?: CreateWorkDirFn
  /** 快照前 WAL 在位判定（缺省 existsSync） */
  existsFn?: (path: string) => boolean
  // ---- 目录注入 ----
  /** localdb-state.json 目录（undefined=环境变量定位；null=模拟不可定位） */
  stateDir?: string | null
  /** logs/localdb.log 目录（undefined=环境变量定位） */
  logDir?: string | null
  /** 深检产物根（undefined=localdbArtifactsDir()；null=不可定位） */
  workBaseDir?: string | null
  /** 时钟注入（checked_at 与 elapsed 计时来源；缺省系统时钟） */
  now?: () => Date
}

/** 步骤函数的统一出参（内部） */
interface StepOutcome {
  ok: boolean
  kind?: LocaldbDoctorKind
  detail: string
}

/** 可信快照捕获总尝试次数（含首次；§3.3「整体重试（N 次小退避）」，评审 v6 §2 快照类短退避） */
export const SNAPSHOT_MAX_ATTEMPTS = 3
/** 快照重试退避（毫秒；§2 秒级短退避量级，不进长负缓存） */
export const SNAPSHOT_RETRY_BACKOFF_MS = 300

/** 秒级短退避 sleep（快照重试间；不响应取消——深检无取消语义，runLocaldbPy 内部有 kill 兜底） */
const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * 矩阵条目进入深检后续步骤的必需字段（validated 契约由 matrix.ts 降级保证；
 * stale 条目不经降级校验，在此自校验——缺字段按 compat 失败，不猜不扫）。
 */
function pickEntryFields(entry: MatrixEntry): { db_magic: string; page_size: number; algorithm: string } | null {
  const dbMagic = entry['db_magic']
  const pageSize = entry['page_size']
  const algorithm = entry['algorithm']
  if (typeof dbMagic !== 'string' || dbMagic.length === 0) return null
  if (typeof pageSize !== 'number' || !Number.isInteger(pageSize) || pageSize <= 0) return null
  if (typeof algorithm !== 'string' || algorithm.length === 0) return null
  return { db_magic: dbMagic, page_size: pageSize, algorithm }
}

/**
 * doctor 深检入口：两阶段八步，报告含双就绪块 + 全步骤 + 模式结论 + 引导文案。
 * 全程不抛出（组件层 best-effort；步骤函数意外异常按该步 transient 失败归并）。
 */
export async function runLocaldbDoctor(
  opts: LocaldbDoctorOptions = {},
  deps: LocaldbDoctorDeps = {},
): Promise<LocaldbDoctorReport> {
  const now = deps.now ?? (() => new Date())
  const log = deps.logLocaldbEventFn ?? ((event: LocaldbLogEvent) => logLocaldbEvent(event, { dir: deps.logDir }))
  const writeState = deps.writeLocalStateFn ?? ((state: LocalDbState) => writeLocalState(state, deps.stateDir))
  const runPy = deps.runLocaldbPyFn ?? ((args: string[], o?: RunLocaldbPyOpts) => runLocaldbPy(args, o))
  const steps: LocaldbDoctorStep[] = []
  const guidance: string[] = []
  let installVersion: string | null = null
  let install: InstallInfo | null = null
  let login: LoginProbeResult | null = null

  // ---- UI 就绪块（独立于本地库门禁；probeEnvironment best-effort，失败全 false 不阻断深检） ----
  let env: EnvironmentProbe = {
    platform_ok: false,
    platform: process.platform,
    interactive_session: null,
    session_name: null,
    powershell_available: false,
    powershell_path: null,
    wecom_running: false,
    wecom_processes: 0,
  }
  try {
    env = await (deps.probeEnvironmentFn ?? (() => probeEnvironment()))()
  } catch {
    // 探测失败保持兜底值：UI 就绪块照常呈现（ready=false），不影响本地库深检
  }
  const uiReady: LocaldbUiReadiness = {
    platform_ok: env.platform_ok,
    interactive_session: env.interactive_session,
    powershell_available: env.powershell_available,
    ready: env.platform_ok && env.interactive_session === true && env.powershell_available,
  }

  /** 单步执行 + 计时 + 记录（意外异常归并为该步 transient 失败，不中断整个 doctor） */
  const runStep = async (step: number, name: string, fn: () => Promise<StepOutcome>): Promise<StepOutcome> => {
    const t0 = now().getTime()
    let outcome: StepOutcome
    try {
      outcome = await fn()
    } catch (err) {
      outcome = {
        ok: false,
        kind: 'transient',
        detail: `步骤执行异常：${err instanceof Error ? err.message : String(err)}`,
      }
    }
    steps.push({
      step,
      name,
      ok: outcome.ok,
      ...(outcome.kind !== undefined ? { kind: outcome.kind } : {}),
      detail: outcome.detail,
      elapsed_ms: now().getTime() - t0,
    })
    return outcome
  }

  /** 引导阶段终态：不写状态、不产 mode_decided 日志（引导阶段不产出模式，§5） */
  const concludeGuidance = (reason: string): LocaldbDoctorReport => ({
    success: false,
    localdb_ready: false,
    ui_ready: uiReady,
    steps,
    mode_decision: { mode: 'unset', reason, version: installVersion, checked_at: now().toISOString() },
    state_saved: false,
    guidance: [...guidance],
  })

  /** 深检终态：写 localdb-state.json + mode_decided 日志（必产出 db/ui，§5） */
  const concludeDeep = (failure: StepOutcome | undefined): LocaldbDoctorReport => {
    const allOk = failure === undefined
    const failedStep = allOk ? undefined : steps.find((s) => !s.ok)
    const mode: 'db' | 'ui' = allOk ? 'db' : 'ui'
    const kind = allOk ? undefined : (failedStep?.kind ?? 'transient')
    const reason = allOk
      ? '深检全链路通过（目录/版本/指纹/密钥/快照解密/查询冒烟）'
      : `深检未通过：步骤${failedStep?.step} ${failedStep?.name}——${(failure?.detail ?? '').slice(0, 120)}`
    const decision: LocaldbModeDecision = {
      mode,
      ...(kind !== undefined ? { kind } : {}),
      reason,
      version: installVersion,
      checked_at: now().toISOString(),
    }
    const stateWritten = writeState(decision as LocalDbState)
    log({
      type: 'mode_decided',
      reason,
      detail: `mode=${mode}${kind !== undefined ? ` kind=${kind}` : ''} version=${installVersion ?? 'unknown'} state_write=${stateWritten ? 'ok' : 'failed'}`,
    })
    if (!stateWritten) {
      // 评审 v6：状态保存失败必须用户可见——不得让调用方误以为「已启用」已持久化
      //（后续运行读不到 state 会按 unset 处理，实际行为回退 UI）。
      guidance.push(
        '本地库状态保存失败，启用未完成，后续调用可能回退 UI：请检查 localdb-state.json 所在目录写权限后重跑 aid-wecom doctor --localdb',
      )
    }
    guidance.push(
      allOk
        ? stateWritten
          ? '本地库通道已启用（mode=db）：业务读取优先走本地库，运行时失败自动回退 UI'
          : // 评审 v7：保存失败时不得再输出「已启用」——能力检测通过 ≠ 启用完成，
            // 否则与上一条「启用未完成」自相矛盾（评审 v6 复现的报告矛盾）
            '本地库能力检测通过（db 模式检测成功），但本地库状态保存失败：启用未完成，后续调用可能回退 UI'
        : kind === 'compat'
          ? '本地库通道结论 ui（兼容性失败）：业务操作恒走 UI 通道；需 wecom-cli 矩阵/方案更新后重跑 doctor'
          : '本地库通道结论 ui（临时不可用）：业务操作暂回退 UI 通道，可稍后重跑 aid-wecom doctor --localdb 自愈',
    )
    return {
      success: true,
      localdb_ready: allOk,
      ui_ready: uiReady,
      steps,
      mode_decision: decision,
      state_saved: stateWritten,
      guidance,
    }
  }

  // ========== 启动清扫（§7 遗留清扫：步骤 1 之前清理上次运行可能遗留的产物目录） ==========
  // owner 存活语义保证不误删另一存活调用的活动产物（1.13 sweepArtifacts）；
  // 清扫失败（删除失败/异常）只记 db_fallback(stage=cleanup) 日志，不阻断深检（§7：清理
  // 失败记日志并在结果报告，不失败）。
  try {
    const sweep = (
      deps.sweepArtifactsFn ?? (() => sweepArtifacts(deps.workBaseDir === undefined ? undefined : deps.workBaseDir))
    )()
    if (sweep.failed.length > 0) {
      log({
        type: 'db_fallback',
        stage: 'cleanup',
        reason: 'sweep_failed',
        detail:
          `遗留清扫删除失败 ${sweep.failed.length} 个（swept=${sweep.swept.length} skipped=${sweep.skipped.length}）：` +
          sweep.failed.map((f) => basename(f.dir)).join(','),
      })
    }
  } catch (err) {
    log({
      type: 'db_fallback',
      stage: 'cleanup',
      reason: 'sweep_failed',
      detail: `遗留清扫异常：${err instanceof Error ? err.message : String(err)}`.slice(0, 160),
    })
  }

  // ========== 引导阶段（步骤 1-2：失败即终态，不产出模式） ==========

  // 步骤 1：安装检测（运行进程/exe 路径/版本；C1）
  {
    const outcome = await runStep(1, '企业微信安装检测', async () => {
      install = await (deps.detectInstallFn ?? (() => detectInstall()))()
      installVersion = install.version
      if (!install.installed) {
        return { ok: false, detail: '未检测到企业微信（运行进程与默认安装目录均未命中）' }
      }
      return {
        ok: true,
        detail: `运行进程命中（PID ${install.pid ?? '未知'}）${install.version ? `，版本 ${install.version}` : '，版本未知'}`,
      }
    })
    if (!outcome.ok) {
      guidance.push(
        '未检测到企业微信安装或运行',
        '请安装并启动企业微信（至少完成一次登录）后重试：aid-wecom doctor --localdb',
      )
      return concludeGuidance('引导未完成：企业微信未安装或未运行')
    }
  }
  // 闭包内赋值不被 TS 控制流跟踪，此处用 unknown 中转断言（运行时步骤 1 必已赋值）
  let assertedInstall = install as unknown as InstallInfo

  // 步骤 2：登录态（轻量登录探测原语，不带交互桌面会话门禁；1.10）
  {
    const outcome = await runStep(2, '登录态检测', async () => {
      login = await (deps.probeLoginStateFn ?? (() => probeLoginState()))()
      switch (login.login_state) {
        case 'online':
          return { ok: true, detail: '已登录（online）' }
        case 'need_login':
          return { ok: false, detail: '企业微信未登录（检测到登录二维码窗口）' }
        case 'offline':
          return { ok: false, detail: '企业微信未运行或无主窗口（offline）' }
        default:
          return { ok: false, detail: '登录态探测失败（驱动无结果），无法确认已登录' }
      }
    })
    if (!outcome.ok) {
      guidance.push('请启动并登录企业微信（未登录时可用 probe 二维码）后重试：aid-wecom doctor --localdb')
      return concludeGuidance(`引导未完成：${outcome.detail}`)
    }
  }

  // ========== 深检阶段（步骤 3-8：必产出 db/ui + kind；任一步失败即 concludeDeep） ==========

  // 步骤 3：只查询操作主窗口 PID 的当前文件句柄；日志候选不提供身份证明。
  let dataDir!: DataDirInfo
  {
    const outcome = await runStep(3, '数据目录定位', async () => {
      const currentLogin = login as unknown as LoginProbeResult
      if (!Number.isInteger(currentLogin.window_pid) || currentLogin.window_pid === null || currentLogin.window_pid <= 0) {
        return { ok: false, kind: 'transient' as const,
          detail: `identity-unconfirmed：操作主窗口 PID 未确认（${currentLogin.window_error ?? 'PID_UNAVAILABLE'}）` }
      }
      const result = await runPy(['active-account', String(currentLogin.window_pid)], { timeoutMs: opts.pyTimeoutMs })
      const payload = result.data
      if (!result.ok || payload?.ok !== true || payload.pid !== currentLogin.window_pid || typeof payload.message_db !== 'string') {
        const reason = typeof payload?.error === 'string' ? payload.error : 'HANDLE_QUERY_FAILED'
        return { ok: false, kind: 'transient' as const, detail: `identity-unconfirmed：无法确认主窗口进程当前使用的唯一账号库（${reason}）` }
      }
      dataDir = await (deps.locateDataDirFn ?? locateDataDir)({
        activeAccount: { pid: currentLogin.window_pid, message_db: payload.message_db },
      })
      if (!dataDir.dir || !dataDir.user_id || !dataDir.db_paths?.message ||
          dataDir.identity.identity_confirmed !== true || dataDir.identity.process_pid !== currentLogin.window_pid) {
        return { ok: false, kind: 'transient' as const, detail: 'identity-unconfirmed：主窗口进程 message.db 路径未能确认账号目录' }
      }
      return { ok: true, detail: `数据目录命中（来源 ${dataDir.source}，主窗口进程身份已确认），message.db 在位` }
    })
    if (!outcome.ok) {
      guidance.push('无法确认当前操作主窗口使用的唯一账号库，本地库通道结论 ui，禁止注入密钥材料；请确认企业微信已登录且主窗口可明确定位后重跑 aid-wecom doctor --localdb')
      return concludeDeep(outcome)
    }
  }

  // 步骤 4：版本 vs 矩阵（以运行进程 executable 的 FileVersion 为准；C4）
  let matrixEntry: MatrixEntry | null = null
  {
    const outcome = await runStep(4, '版本矩阵校验', async () => {
      const windowPid = (login as unknown as LoginProbeResult).window_pid!
      assertedInstall = await (deps.detectInstallFn ?? detectInstall)({ processPid: windowPid })
      installVersion = assertedInstall.version
      if (assertedInstall.pid !== windowPid || assertedInstall.version_source !== 'process' || !assertedInstall.version) {
        return { ok: false, kind: 'compat' as const, detail: '操作主窗口进程版本不可确认，禁止硬扫' }
      }
      const matrix = deps.loadMatrixFn ? deps.loadMatrixFn() : loadMatrix()
      const lookup = (deps.lookupVersionFn ?? lookupVersion)(matrix, assertedInstall.version)
      if (lookup.status === 'validated' || lookup.status === 'stale') {
        if (lookup.entry === null || pickEntryFields(lookup.entry) === null) {
          return { ok: false, kind: 'compat' as const, detail: '矩阵条目字段不全（db_magic/page_size/algorithm），无法继续深检' }
        }
        matrixEntry = lookup.entry
        const staleNote = lookup.status === 'stale' ? '（stale，待复核）' : ''
        return { ok: true, detail: `版本 ${assertedInstall.version} 在矩阵${staleNote}` }
      }
      return lookup.status === 'broken'
        ? { ok: false, kind: 'compat' as const, detail: `版本 ${assertedInstall.version ?? '未知'} 在矩阵但 status=broken：确认不可用` }
        : { ok: false, kind: 'compat' as const, detail: `版本 ${assertedInstall.version ?? '未知'} 不在版本矩阵（unknown）：未知版本禁止硬扫，需维护者扩展矩阵` }
    })
    if (!outcome.ok) return concludeDeep(outcome)
  }
  // 闭包内赋值不被 TS 控制流跟踪，此处用 unknown 中转断言（运行时步骤 4 必已校验）
  const entryFields = pickEntryFields(matrixEntry as unknown as MatrixEntry)
  if (entryFields === null) {
    // 不可达（步骤 4 已校验）；防御性兜底防 TS 收窄歧义
    return concludeDeep({ ok: false, kind: 'compat', detail: '矩阵条目字段不全' })
  }

  // 步骤 5：指纹预检（库头魔数 + 明文页头；C5）
  const dbPath = dataDir.db_paths!.message!
  {
    const outcome = await runStep(5, '数据库指纹预检', async () => {
      const fp = await (deps.checkDbFingerprintFn ?? ((p: string, e: DbFingerprintExpected) =>
        Promise.resolve(checkDbFingerprint(p, e))))(dbPath, {
        db_magic: entryFields.db_magic,
        page_size: entryFields.page_size,
      })
      return fp.match
        ? { ok: true, detail: '库指纹匹配（魔数 + 明文页头）' }
        : { ok: false, kind: 'compat' as const, detail: fp.detail }
    })
    if (!outcome.ok) return concludeDeep(outcome)
  }

  // 步骤 6/7 共享：密钥与工作目录状态
  const binding: KeyBinding = {
    user_id: dataDir.user_id!,
    data_dir: dataDir.dir!,
    algorithm: entryFields.algorithm,
  }
  let keyHex: string | null = null
  let keySource: 'keystore' | 'keyscan' | null = null
  let step6Index = -1
  let decDir = '' // 步骤 7 内记录解密产物目录，步骤 8 查询冒烟使用（步骤 7 失败不会到达步骤 8）

  /** 只在真正需要扫描时验证多 PID 候选的实际版本；主窗口进程优先。 */
  const attemptKeyScan = async (): Promise<StepOutcome> => {
    const run = await (deps.detectRunningFn ?? (() => detectRunning()))()
    const pids = [assertedInstall.pid!]
    let skipped = 0
    for (const pid of new Set(run.pids)) {
      if (pid === assertedInstall.pid) continue
      const info = await (deps.detectInstallFn ?? detectInstall)({ processPid: pid })
      if (info.pid === pid && info.version_source === 'process' && info.version === assertedInstall.version) pids.push(pid)
      else skipped++
    }
    const args = ['keyscan', '--pids', pids.join(','), '--db', dbPath]
    if (opts.keyscanMaxRegionBytes !== undefined) args.push('--max-region-bytes', String(opts.keyscanMaxRegionBytes))
    const scan = await runPy(args, { timeoutMs: opts.keyscanTimeoutMs ?? 600_000 })
    const data = scan.ok ? scan.data : null
    const scanError = scan.ok ? String(data?.['error'] ?? 'UNKNOWN') : (scan.error ?? 'BRIDGE_ERROR')
    const hit =
      scan.ok && data !== null && data['ok'] === true && typeof data['key_hex'] === 'string' &&
      /^[0-9a-fA-F]{32}$/.test(data['key_hex'])
    // keyscan_result 事件（§2 五类事件之一）：只记命中与否与候选数/耗时，不含 key_hex
    log({
      type: 'keyscan_result',
      reason: hit ? 'hit' : scanError,
      detail: `pids=${pids.length} skipped_unverified=${skipped} elapsed_ms=${typeof data?.['elapsed_ms'] === 'number' ? data['elapsed_ms'] : 'unknown'}`,
    })
    if (!hit) {
      return { ok: false, kind: 'transient', detail: `内存扫描未命中（${scanError}）：本次兜底 UI，可重跑 doctor 或等运行时自愈` }
    }
    const hitKey = String(data['key_hex']) // 已过 32-hex 正则校验
    keyHex = hitKey
    keySource = 'keyscan'
    const saved = await (deps.saveKeyFn ?? ((k: string, b: KeyBinding) => saveKey({ keyHex: k, binding: b })))(
      hitKey,
      binding,
    )
    if (!saved) {
      return { ok: false, kind: 'transient', detail: '内存扫描命中但 keystore 保存失败（DPAPI）' }
    }
    return {
      ok: true,
      detail:
        `内存扫描命中（PID ${typeof data['pid'] === 'number' ? data['pid'] : '未知'}` +
        `，扫描 ${typeof data['scanned_bytes'] === 'number' ? data['scanned_bytes'] : '?'} 字节` +
        `，耗时 ${typeof data['elapsed_ms'] === 'number' ? data['elapsed_ms'] : '?'}ms），密钥已存 keystore`,
    }
  }

  // 步骤 6：密钥（先复用 keystore 缓存；无效/缺失才 keyscan——全链路唯一进程内存接触步骤）。
  // 评审 v6：身份未确认（步骤 3 identity-unconfirmed）时本步骤不执行——不向未确认目录注入
  // 密钥材料（keystore 命中或 keyscan 产出的密钥都可能解出历史账号数据）。
  {
    const outcome = await runStep(6, '密钥获取（keystore 复用 / 内存扫描）', async () => {
      const stored = await (deps.loadKeyFn ?? ((b: KeyBinding) => loadKey({ binding: b })))(binding)
      if (stored !== null) {
        keyHex = stored.keyHex
        keySource = 'keystore'
        return { ok: true, detail: 'keystore 缓存密钥命中（绑定账号/目录/算法一致；页 1 核验在解密步骤进行）' }
      }
      return await attemptKeyScan()
    })
    step6Index = steps.length - 1
    if (!outcome.ok) return concludeDeep(outcome)
  }

  // 步骤 7+8：工作目录生命周期覆盖两步（步骤 8 查询冒烟消费步骤 7 的解密产物；
  // §7 成功失败都删——清理时点在最后一个消费者之后，finally 保证失败路径同样清理）
  let work: DoctorWorkDir | null = null
  let deepFailure: StepOutcome | undefined
  try {
    // 步骤 7：可信快照 + 整库解密 + PRAGMA integrity_check（§3.3 / C7）
    deepFailure = await runStep(7, '可信快照 + 整库解密 + 完整性校验', async () => {
      work = deps.createWorkDirFn ? deps.createWorkDirFn() : defaultCreateWorkDir(deps.workBaseDir)
      if (work === null) {
        return { ok: false, kind: 'transient' as const, detail: '产物工作目录不可定位（LOCALAPPDATA 缺失或创建失败）' }
      }
      const existsFn = deps.existsFn ?? existsSync
      let walPath = existsFn(`${dbPath}-wal`) ? `${dbPath}-wal` : null
      // §3.3 步骤 3「任一不满足 → 整体重试（N 次小退避）」：捕获组件是单次语义，重试归本编排层。
      // WXWork 活跃写库时 db-changed/wal-shrunk 是瞬时竞争，有限重试后仍失败才按 ui(transient) 结论。
      // 评审 v6：每次尝试前重新判断源 WAL 在位——窗口内 checkpoint+delete 后 WAL 永久消失是
      // 合法状态，重试必须能进入「无 WAL」捕获分支，而不是带着旧路径连续 no-wal 失败。
      const capture =
        deps.captureSnapshotFn ??
        ((d: string, w: string | null, wd: string) => captureSnapshot(d, w, wd))
      const maxAttempts = Math.max(1, opts.snapshotMaxAttempts ?? SNAPSHOT_MAX_ATTEMPTS)
      const backoffMs = opts.snapshotRetryBackoffMs ?? SNAPSHOT_RETRY_BACKOFF_MS
      let snap: CaptureSnapshotResult = await capture(dbPath, walPath, work.dir)
      for (let attempt = 1; !snap.ok && attempt < maxAttempts; attempt++) {
        await sleep(backoffMs)
        walPath = existsFn(`${dbPath}-wal`) ? `${dbPath}-wal` : null
        snap = await capture(dbPath, walPath, work.dir)
      }
      if (!snap.ok) {
        return { ok: false, kind: 'transient' as const, detail: `可信快照失败（${snap.reason}）：${snap.detail}` }
      }
      decDir = join(work.dir, 'dec')
      const decryptArgs = () => ['decrypt', snap.dbSnapshot, snap.walSnapshot ?? '-', decDir]
      let dec = await runPy(decryptArgs(), { stdin: keyHex ?? '', timeoutMs: opts.pyTimeoutMs })
      let result = describeDecrypt(dec)
      // 缓存密钥页 1 先知核验明确失败 → 密钥失效：clearKey → keyscan → saveKey → 重试一次
      //（§3.4：仅页 1 核验明确失败才删密钥；快照失败不删、不归因密钥）
      if (isDecryptError(dec, 'KEY_VERIFY_FAILED') && keySource === 'keystore') {
        ;(deps.clearKeyFn ?? (() => clearKey(deps.stateDir)))()
        const rescan = await attemptKeyScan()
        if (!rescan.ok) {
          steps[step6Index] = {
            ...steps[step6Index]!,
            ok: false,
            kind: 'transient',
            detail: `缓存密钥页 1 核验失败已清除，重扫未命中：${rescan.detail}`,
          }
          return { ok: false, kind: 'transient' as const, detail: `解密失败（缓存密钥失效且重扫未果）：${rescan.detail}` }
        }
        steps[step6Index] = { ...steps[step6Index]!, ok: true, detail: `缓存密钥页 1 核验失败已清除；${rescan.detail}` }
        dec = await runPy(decryptArgs(), { stdin: keyHex ?? '', timeoutMs: opts.pyTimeoutMs })
        result = describeDecrypt(dec)
        if (result.ok) result = { ...result, detail: `${result.detail}（缓存密钥失效后重扫重试通过）` }
      }
      return result
    })

    // 步骤 8：查询冒烟（conversations 计数；C8）。schema 类失败=compat，其他=transient（评审 v5）
    if (deepFailure.ok) {
      deepFailure = await runStep(8, '查询冒烟（conversations 计数）', async () => {
        const q = await runPy(['query', '--workdir', decDir, '--task', 'conversations'], {
          timeoutMs: opts.pyTimeoutMs,
        })
        if (q.ok && q.data !== null && q.data['ok'] === true) {
          const convs = Array.isArray(q.data['conversations']) ? (q.data['conversations'] as unknown[]) : []
          return { ok: true, detail: `查询冒烟通过：${convs.length} 个会话` }
        }
        const err = q.ok ? String(q.data?.['error'] ?? 'UNKNOWN') : (q.error ?? 'BRIDGE_ERROR')
        const detail = q.ok && typeof q.data?.['detail'] === 'string' ? q.data['detail'] : ''
        return {
          ok: false,
          // 评审 v6：SCHEMA_UNSUPPORTED 是 py 侧对真实协议缺表/缺列的稳定分类（sqlite
          // OperationalError "no such table/column"）→ compat；其他查询失败=transient。
          kind: err === 'SCHEMA_UNSUPPORTED' ? ('compat' as const) : ('transient' as const),
          detail: `查询冒烟失败（${err}）${detail ? `：${detail}` : ''}`,
        }
      })
    }
  } finally {
    // work 在闭包内赋值，TS CFA 不跟踪——unknown 中转断言（运行时步骤 7 已尝试创建）
    const created = work as unknown as DoctorWorkDir | null
    if (created !== null && !created.cleanup()) {
      log({ type: 'db_fallback', stage: 'cleanup', reason: 'cleanup_failed', detail: basename(created.dir) })
    }
  }
  return concludeDeep(deepFailure.ok ? undefined : deepFailure)

  // ---------- 内部辅助 ----------

  /** decrypt 结果 → 步骤出参（桥级失败/业务失败/integrity 非 ok 都归 transient） */
  function describeDecrypt(dec: LocaldbPyResult): StepOutcome {
    if (dec.ok && dec.data !== null && dec.data['ok'] === true && dec.data['integrity'] === 'ok') {
      return {
        ok: true,
        detail:
          `快照主库解密 ${typeof dec.data['pages'] === 'number' ? dec.data['pages'] : '?'} 页` +
          `（WAL ${typeof dec.data['wal_frames'] === 'number' ? dec.data['wal_frames'] : '?'} 帧）` +
          `，integrity=${dec.data['integrity']}`,
      }
    }
    const err = dec.ok ? String(dec.data?.['error'] ?? 'UNKNOWN') : (dec.error ?? 'BRIDGE_ERROR')
    const detail = dec.ok && typeof dec.data?.['detail'] === 'string' ? dec.data['detail'] : ''
    return { ok: false, kind: 'transient', detail: `解密失败（${err}）${detail ? `：${detail}` : ''}` }
  }

  function isDecryptError(dec: LocaldbPyResult, code: string): boolean {
    return dec.ok && dec.data !== null && dec.data['ok'] !== true && dec.data['error'] === code
  }
}
