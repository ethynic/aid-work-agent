/**
 * 本地库共享访问段（P2 任务 3.1；read-session 与 unread 两执行器共用）：
 *   1. 版本准入（矩阵 validated/stale + algorithm/db_magic/page_size 严格度，forced 只覆盖
 *      落盘状态选择、不绕过实际矩阵）；
 *   2. 密钥获取（keystore 命中优先 + keyscan 负缓存，绑定账号/目录/版本/PID 集/doctor epoch）
 *      与可信快照解密段（sweep → workdir 两段发布 → 逐逻辑库指纹/快照/解密，message/user
 *      必需、session 可选容忍；KEY_VERIFY_FAILED 重扫一次）。
 *
 * 手术式提取自 readSession.ts（P1b 实现原样搬移，行为不变）；unread.ts 复用同一段保证
 * 身份/密钥/快照安全语义与 read-session 完全一致（设计 §4.2）。查询任务（context/resolve/
 * messages/last-per-conv）属各执行器业务语义，不进本模块。
 */
import { createHash } from 'node:crypto'
import { existsSync, rmSync, statSync } from 'node:fs'
import { basename, join } from 'node:path'
import { CodedOperationError, CancelledError, type ErrorCode, type OpContext } from '../../operations/types.js'
import { detectInstall, type DetectInstallDeps, type InstallInfo } from './version.js'
import { detectRunning, type RunningInfo } from './running.js'
import { loadMatrix, lookupVersion, type LocalDbState, type VersionMatrix } from './matrix.js'
import { checkDbFingerprint, type DbFingerprintExpected, type DbFingerprintResult } from './fingerprint.js'
import { loadKey, saveKey, clearKey, type KeyBinding, type StoredKey } from './keystore.js'
import { captureSnapshot, type CaptureSnapshotResult } from './snapshot.js'
import { createLocaldbWorkDir, sweepArtifacts, type LocaldbWorkDir } from './cleanup.js'
import { runLocaldbPy, type LocaldbPyResult, type RunLocaldbPyOpts } from './driver.js'
import { logLocaldbEvent, type LocaldbLogEvent } from './log.js'
import { checkCancelled, type CurrentAccount } from './identity.js'

export const LOCALDB_FALLBACK_CODES: ReadonlySet<string> = new Set([
  'VERSION_UNSUPPORTED', 'KEY_SCAN_FAILED', 'SNAPSHOT_UNSTABLE', 'LOCALDB_UNAVAILABLE',
])

/** 共享段依赖（LocaldbReadDeps 的超集公共部分；readSession/unread 的 deps 对象直接传入） */
export interface LocaldbAccessDeps {
  detectInstallFn?: (deps?: DetectInstallDeps) => Promise<InstallInfo>
  detectRunningFn?: () => Promise<RunningInfo>
  loadMatrixFn?: () => VersionMatrix | null
  fingerprintFn?: (path: string, expected: DbFingerprintExpected) => DbFingerprintResult
  loadKeyFn?: (binding: KeyBinding) => Promise<StoredKey | null>
  saveKeyFn?: (key: string, binding: KeyBinding) => Promise<boolean>
  clearKeyFn?: () => boolean
  runPyFn?: (args: string[], opts?: RunLocaldbPyOpts) => Promise<LocaldbPyResult>
  captureFn?: (db: string, wal: string | null, dir: string) => Promise<CaptureSnapshotResult>
  workDirFn?: () => LocaldbWorkDir | null
  fileIdentityFn?: (path: string) => string
  logFn?: (event: LocaldbLogEvent) => boolean
  now?: () => number
  /** 测试/定制布局均需显式传给状态与密钥存储，不能误写真实用户目录 */
  keyDir?: string | null
  workBaseDir?: string | null
  snapshotRetryMs?: number
}

export const hash = (value: unknown): string => createHash('sha256').update(JSON.stringify(value)).digest('hex')
export const unavailable = (message: string): CodedOperationError => new CodedOperationError('LOCALDB_UNAVAILABLE', message)

/** 源文件身份稳定于普通写入，替换/重新创建则变化；绝不使用复制品的文件身份。 */
export function fileIdentity(path: string): string {
  try {
    const info = statSync(path, { bigint: true })
    if (!info.isFile() || info.ino === 0n || info.birthtimeNs === 0n) throw unavailable('源库稳定文件身份不可得')
    return `${info.dev}:${info.ino}:${info.birthtimeNs}`
  } catch {
    throw unavailable('源库稳定文件身份不可得')
  }
}

/** 可中断的短退避；没有子进程运行时才使用，不能提前放走仍占用产物的子进程。 */
async function backoff(ms: number, signal: AbortSignal): Promise<void> {
  checkCancelled(signal)
  await new Promise<void>((resolve, reject) => {
    const aborted = () => { clearTimeout(timer); reject(new CancelledError()) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', aborted); resolve() }, ms)
    signal.addEventListener('abort', aborted, { once: true })
  })
}

// ---------- 版本准入（readSession 原判定搬移；forced 只覆盖落盘状态选择，不绕过实际矩阵） ----------

export interface VersionAdmission {
  version: string
  expected: { db_magic: string; page_size: number }
  /** 算法标识（准入已保证 'wxaes128cbc-nonlegacy'；密钥绑定按原值透传） */
  algorithm: string
}

export async function admitLocaldbVersion(
  ctx: OpContext,
  current: CurrentAccount,
  state: LocalDbState | null,
  opts: { forced?: boolean } = {},
  deps: LocaldbAccessDeps = {},
): Promise<VersionAdmission> {
  const install = await (deps.detectInstallFn ?? detectInstall)({ processPid: current.windowPid })
  checkCancelled(ctx.signal)
  const version = install.version
  const entry = lookupVersion((deps.loadMatrixFn ?? loadMatrix)(), version)
  if (install.pid !== current.windowPid || install.version_source !== 'process' || !version ||
      (entry.status !== 'validated' && entry.status !== 'stale') ||
      entry.entry?.algorithm !== 'wxaes128cbc-nonlegacy' ||
      typeof entry.entry.db_magic !== 'string' || typeof entry.entry.page_size !== 'number' ||
      (!opts.forced && state?.version && state.version !== version)) {
    throw new CodedOperationError('VERSION_UNSUPPORTED', '当前操作主窗口版本不满足本地库准入，请运行 doctor --localdb 核对')
  }
  return {
    version,
    expected: { db_magic: entry.entry.db_magic, page_size: entry.entry.page_size },
    algorithm: entry.entry.algorithm,
  }
}

// ---------- 密钥获取 + 可信快照解密段（sweep → workdir → 逐逻辑库指纹/快照/解密） ----------

export interface PreparedDecryptedDbs {
  work: LocaldbWorkDir
  /** message 源库捕获前稳定文件身份（调用方如需读后复核/游标代际使用） */
  sourceId: string
}

export interface PrepareDecryptedParams {
  ctx: OpContext
  current: CurrentAccount
  version: string
  expected: { db_magic: string; page_size: number }
  /** 矩阵条目 algorithm（准入已保证 'wxaes128cbc-nonlegacy'；密钥绑定按原值透传） */
  algorithm: string
  /** 落盘状态（keyscan 负缓存 context 绑定 checked_at；缺省视为 unset） */
  state: LocalDbState | null
  getState: () => LocalDbState | null
  putState: (state: LocalDbState) => boolean
  /** 产物目录名前缀（两段发布；各执行器传自己的语义名：read-session/unread/watch/send-*） */
  workPrefix: 'read-session' | 'unread' | 'watch' | 'send-baseline' | 'send-verify'
  /**
   * 可选 session 库失败是否容忍跳过（readSession：仅 contact 目标——已有实际 S 会话时不依赖
   * session，group/typeless 需 session 核实则传播；unread：恒容忍——focus 单目标解析失败进
   * failed 部分返回，不拖垮整个快照，能力不足由 resolve sources 报告）。
   */
  tolerateSessionFailure: boolean
  deps: LocaldbAccessDeps
}

/**
 * 密钥（keystore 命中优先，keyscan 负缓存）+ 三逻辑库可信快照解密；原 readSession.ts
 * 实现原样搬移。message/user 必需；session 可选（失败解密产物清理后容忍跳过）。
 */
export async function prepareDecryptedDbs(
  paths: { message: string | null; user: string | null; session: string | null },
  params: PrepareDecryptedParams,
): Promise<PreparedDecryptedDbs> {
  const { ctx, current, version, expected, state, getState, putState, workPrefix, deps } = params
  const log = deps.logFn ?? logLocaldbEvent
  const now = deps.now ?? Date.now
  const getFileId = deps.fileIdentityFn ?? fileIdentity
  const runPy = deps.runPyFn ?? runLocaldbPy
  const sourceDb = paths.message!
  const binding: KeyBinding = { user_id: current.account, data_dir: current.dataDir.dir!, algorithm: params.algorithm }
  let sourceId: string
  let existing: StoredKey | null
  try {
    sourceId = getFileId(sourceDb)
    existing = await (deps.loadKeyFn ?? (b => loadKey({ binding: b, dir: deps.keyDir })))(binding)
  } catch {
    checkCancelled(ctx.signal)
    throw unavailable('源库或密钥存储暂时不可用')
  }
  checkCancelled(ctx.signal)
  let key = existing?.keyHex ?? null
  let work: LocaldbWorkDir | null = null

  const call = async (params: string[], input?: string): Promise<Record<string, unknown>> => {
    checkCancelled(ctx.signal)
    const result = await runPy(params, { signal: ctx.signal, ...(input === undefined ? {} : { stdin: input }) })
    checkCancelled(ctx.signal) // runLocaldbPy 已等待 close，随后才允许 finally 清理。
    if (!result.ok || !result.data) throw unavailable('本地库子进程未完成，请检查 Python 环境')
    return result.data
  }
  const scan = async (): Promise<string> => {
    const running = await (deps.detectRunningFn ?? detectRunning)()
    checkCancelled(ctx.signal)
    const candidates = [...new Set([current.windowPid, ...running.pids])]
    const context = hash([current.account, current.dataDir.dir, version, candidates.slice().sort((a, b) => a - b), state?.checked_at])
    const negative = getState()?.scan_failure as { context?: unknown; until?: unknown } | undefined
    if (negative?.context === context && typeof negative.until === 'number' && negative.until > now()) {
      throw new CodedOperationError('KEY_SCAN_FAILED', '最近一次密钥扫描未命中，正在等待重试；可重跑 doctor --localdb')
    }
    const pids = [current.windowPid]
    for (const pid of candidates) {
      if (pid === current.windowPid) continue
      const other = await (deps.detectInstallFn ?? detectInstall)({ processPid: pid })
      checkCancelled(ctx.signal)
      if (other.pid === pid && other.version_source === 'process' && other.version === version) pids.push(pid)
    }
    const result = await runPy(['keyscan', '--pids', pids.join(','), '--db', sourceDb], { signal: ctx.signal, timeoutMs: 600_000 })
    checkCancelled(ctx.signal)
    const value = result.data
    const hit = result.ok && value?.ok === true && typeof value.key_hex === 'string' && /^[0-9a-f]{32}$/i.test(value.key_hex)
    log({ type: 'keyscan_result', reason: hit ? 'hit' : 'scan_failed' })
    const latest = getState()
    let cacheSaved = true
    if (latest?.checked_at === state?.checked_at) {
      cacheSaved = putState({ ...(latest ?? { mode: 'unset' }), scan_failure: hit ? undefined : { context, until: now() + 600_000 } })
      if (!cacheSaved) log({ type: 'db_fallback', stage: 'scan-cache', reason: 'state_write_failed' })
    }
    if (!hit) throw new CodedOperationError('KEY_SCAN_FAILED', `本地库密钥扫描失败${cacheSaved ? '' : '，重试节流状态未保存'}`, 'none', { scan_cache_saved: cacheSaved })
    const found = value!.key_hex as string
    if (!await (deps.saveKeyFn ?? ((k, b) => saveKey({ keyHex: k, binding: b, dir: deps.keyDir })))(found, binding)) {
      // 当前调用已有经过页1验证的密钥，可继续；不谎称后续进程可复用。
      log({ type: 'db_success', stage: 'keystore', reason: 'key_save_failed' })
    }
    return found
  }
  try {
    const swept = sweepArtifacts(deps.workBaseDir)
    if (swept.failed.length > 0) log({ type: 'db_fallback', stage: 'cleanup', reason: 'sweep_failed', detail: `failed=${swept.failed.length}` })
    work = (deps.workDirFn ?? (() => createLocaldbWorkDir(workPrefix, deps.workBaseDir)))()
    if (!work) throw unavailable('无法创建本地库临时产物目录')
    const logicals = ['message', 'user', 'session'] as const
    for (const logical of logicals) {
      const path = paths[logical]
      if (!path) {
        if (logical === 'session') continue // resolve 根据必要来源区分能力缺失，不能冒充查无目标。
        throw unavailable('读取所需的账号库尚不可用')
      }
      try {
        const fingerprint = (deps.fingerprintFn ?? checkDbFingerprint)(path, expected)
        if (!fingerprint.match) throw new CodedOperationError('VERSION_UNSUPPORTED', '本地库指纹与当前版本矩阵不一致')
        let snapshot: CaptureSnapshotResult | null = null
        for (let attempt = 0; attempt < 3; attempt++) {
          checkCancelled(ctx.signal)
          snapshot = await (deps.captureFn ?? captureSnapshot)(path, existsSync(`${path}-wal`) ? `${path}-wal` : null, join(work.dir, 'snapshot'))
          checkCancelled(ctx.signal)
          if (snapshot.ok) break
          if (attempt < 2) await backoff(deps.snapshotRetryMs ?? 300, ctx.signal)
        }
        if (!snapshot?.ok) throw new CodedOperationError('SNAPSHOT_UNSTABLE', '本地库快照在有限重试后仍不稳定')
        if (logical === 'message' && getFileId(sourceDb) !== sourceId) throw new CodedOperationError('SNAPSHOT_UNSTABLE', '捕获期间源库被替换')
        if (key === null) key = await scan()
        const decryptParams = ['decrypt', snapshot.dbSnapshot, snapshot.walSnapshot ?? '-', work.dir]
        let decrypted = await call(decryptParams, key)
        if (logical === 'message' && decrypted.error === 'KEY_VERIFY_FAILED' && existing !== null) {
          (deps.clearKeyFn ?? (() => clearKey(deps.keyDir)))()
          key = await scan()
          decrypted = await call(decryptParams, key)
        }
        if (decrypted.ok !== true) {
          if (decrypted.error === 'SNAPSHOT_UNSTABLE') throw new CodedOperationError('SNAPSHOT_UNSTABLE', '快照内容不可信')
          throw unavailable('本地库解密或完整性检查未通过')
        }
      } catch (err) {
        checkCancelled(ctx.signal)
        if (logical !== 'session' || !params.tolerateSessionFailure ||
            !(err instanceof CodedOperationError) || !LOCALDB_FALLBACK_CODES.has(err.code)) throw err
        // contact已有message中的S会话时不依赖session；空S仍由resolve报告来源不足。
        // 失败解密可能已创建文件，不能把半结果留给后续query。
        try {
          for (const file of ['session.db.dec.db', 'session.dec.db']) rmSync(join(work.dir, file), { force: true })
        } catch { throw unavailable('可选会话库的无效产物未能清理') }
        log({ type: 'db_fallback', stage: 'optional-session', reason: err.code })
      }
    }
    return { work, sourceId }
  } catch (err) {
    // 本段失败时工作目录必须清理（执行器自身的 finally 只覆盖其成功拿到 work 之后的阶段）；
    // 清理失败按 readSession 原语义重建错误并附 cleanup_failed，不掩盖原错误码。
    if (work && !work.cleanup()) {
      log({ type: 'db_fallback', stage: 'cleanup', reason: 'remove_failed' })
      const code: ErrorCode = err instanceof CodedOperationError ? err.code : err instanceof CancelledError ? 'CANCELLED' : 'LOCALDB_UNAVAILABLE'
      throw new CodedOperationError(code, err instanceof Error ? err.message : '本地库读取失败', 'none', {
        ...(err instanceof CodedOperationError ? err.data : {}), cleanup_failed: [{ dir: basename(work.dir), reason: 'remove_failed' }],
      })
    }
    throw err
  }
}

