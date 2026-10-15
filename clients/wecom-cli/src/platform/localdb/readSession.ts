/** P1b 单次本地库读取；与 doctor 分离，复用只读原语和产物生命周期。 */
import { createHash } from 'node:crypto'
import { existsSync, statSync, rmSync } from 'node:fs'
import { basename, join } from 'node:path'
import { CancelledError, CodedOperationError, type ErrorCode, type OpContext } from '../../operations/types.js'
import type { OperationOutcome } from '../../operations/context.js'
import type { TargetRefIdentity } from '../targetRef.js'
import { detectInstall, type DetectInstallDeps, type InstallInfo } from './version.js'
import { detectRunning, type RunningInfo } from './running.js'
import { loadMatrix, lookupVersion, readLocalState, writeLocalState, type LocalDbState, type VersionMatrix } from './matrix.js'
import { checkDbFingerprint, type DbFingerprintExpected, type DbFingerprintResult } from './fingerprint.js'
import { loadKey, saveKey, clearKey, type KeyBinding, type StoredKey } from './keystore.js'
import { captureSnapshot, type CaptureSnapshotResult } from './snapshot.js'
import { createLocaldbWorkDir, sweepArtifacts, type LocaldbWorkDir } from './cleanup.js'
import { runLocaldbPy, type LocaldbPyResult, type RunLocaldbPyOpts } from './driver.js'
import { logLocaldbEvent, type LocaldbLogEvent } from './log.js'
import { checkCancelled, probeCurrentAccount, type CurrentAccount } from './identity.js'
import { decodeCursor, encodeCursor, normalizeMessages, type RawMessageRow } from './messages.js'

export const LOCALDB_FALLBACK_CODES: ReadonlySet<string> = new Set([
  'VERSION_UNSUPPORTED', 'KEY_SCAN_FAILED', 'SNAPSHOT_UNSTABLE', 'LOCALDB_UNAVAILABLE',
])

export interface LocaldbReadArgs {
  name: string
  target?: TargetRefIdentity
  limit?: number
  cursor?: string
  sinceDays?: number
  /** 强制通道覆盖落盘选择；不绕过实际版本矩阵。 */
  forced?: boolean
}

export interface LocaldbReadDeps {
  probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
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
  readStateFn?: () => LocalDbState | null
  writeStateFn?: (state: LocalDbState) => boolean
  logFn?: (event: LocaldbLogEvent) => boolean
  now?: () => number
  /** 测试/定制布局均需显式传给状态与密钥存储，不能误写真实用户目录。 */
  stateDir?: string | null
  keyDir?: string | null
  workBaseDir?: string | null
  snapshotRetryMs?: number
}

const hash = (value: unknown): string => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const unavailable = (message: string): CodedOperationError => new CodedOperationError('LOCALDB_UNAVAILABLE', message)

/** 源文件身份稳定于普通写入，替换/重新创建则变化；绝不使用复制品的文件身份。 */
function fileIdentity(path: string): string {
  try {
    const info = statSync(path, { bigint: true })
    if (!info.isFile() || info.ino === 0n || info.birthtimeNs === 0n) throw unavailable('源库稳定文件身份不可得')
    return `${info.dev}:${info.ino}:${info.birthtimeNs}`
  } catch {
    throw unavailable('源库稳定文件身份不可得')
  }
}

function cursorError(): CodedOperationError {
  return new CodedOperationError('CURSOR_UNSUPPORTED', '游标与当前账号、会话或数据库代际不一致，请重新读取建立基线')
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

export async function readLocaldbSession(
  args: LocaldbReadArgs,
  ctx: OpContext,
  current: CurrentAccount,
  deps: LocaldbReadDeps = {},
): Promise<OperationOutcome> {
  const now = deps.now ?? Date.now
  const log = deps.logFn ?? logLocaldbEvent
  const getState = deps.readStateFn ?? (() => readLocalState(deps.stateDir))
  const putState = deps.writeStateFn ?? (state => writeLocalState(state, deps.stateDir))
  const getFileId = deps.fileIdentityFn ?? fileIdentity
  const runPy = deps.runPyFn ?? runLocaldbPy
  const accountProbe = deps.probeAccountFn ?? probeCurrentAccount
  const state = getState()
  checkCancelled(ctx.signal)
  const decoded = args.cursor === undefined ? null : decodeCursor(args.cursor)
  if (decoded && (!decoded.ok || decoded.account !== current.account)) throw cursorError()
  const previous = decoded?.ok ? decoded : null
  log({ type: 'db_attempt', stage: 'read-session' })
  const install = await (deps.detectInstallFn ?? detectInstall)({ processPid: current.windowPid })
  checkCancelled(ctx.signal)
  const version = install.version
  const entry = lookupVersion((deps.loadMatrixFn ?? loadMatrix)(), version)
  if (install.pid !== current.windowPid || install.version_source !== 'process' || !version ||
      (entry.status !== 'validated' && entry.status !== 'stale') ||
      entry.entry?.algorithm !== 'wxaes128cbc-nonlegacy' ||
      typeof entry.entry.db_magic !== 'string' || typeof entry.entry.page_size !== 'number' ||
      (!args.forced && state?.version && state.version !== version)) {
    throw new CodedOperationError('VERSION_UNSUPPORTED', '当前操作主窗口版本不满足本地库准入，请运行 doctor --localdb 核对')
  }
  const expected = { db_magic: entry.entry.db_magic, page_size: entry.entry.page_size }
  const paths = current.dataDir.db_paths!
  const sourceDb = paths.message!
  const binding: KeyBinding = { user_id: current.account, data_dir: current.dataDir.dir!, algorithm: entry.entry.algorithm }
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
  let outcome: OperationOutcome | null = null
  let failure: unknown
  let failed = false

  const call = async (params: string[], input?: string): Promise<Record<string, unknown>> => {
    checkCancelled(ctx.signal)
    const result = await runPy(params, { signal: ctx.signal, ...(input === undefined ? {} : { stdin: input }) })
    checkCancelled(ctx.signal) // runLocaldbPy 已等待 close，随后才允许 finally 清理。
    if (!result.ok || !result.data) throw unavailable('本地库子进程未完成，请检查 Python 环境')
    return result.data
  }
  const requireQuery = (value: Record<string, unknown>): Record<string, unknown> => {
    if (value.ok === true) return value
    if (value.error === 'CURSOR_STALE') throw cursorError()
    if (value.error === 'IDENTITY_UNCONFIRMED') {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '账号目录与 user_table.id 的本人身份映射未确认')
    }
    if (value.error === 'SCHEMA_UNSUPPORTED') throw new CodedOperationError('VERSION_UNSUPPORTED', '本地库 schema 不支持当前读取')
    throw unavailable('本地库查询未完成')
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
    work = (deps.workDirFn ?? (() => createLocaldbWorkDir('read-session', deps.workBaseDir)))()
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
        const params = ['decrypt', snapshot.dbSnapshot, snapshot.walSnapshot ?? '-', work.dir]
        let decrypted = await call(params, key)
        if (logical === 'message' && decrypted.error === 'KEY_VERIFY_FAILED' && existing !== null) {
          (deps.clearKeyFn ?? (() => clearKey(deps.keyDir)))()
          key = await scan()
          decrypted = await call(params, key)
        }
        if (decrypted.ok !== true) {
          if (decrypted.error === 'SNAPSHOT_UNSTABLE') throw new CodedOperationError('SNAPSHOT_UNSTABLE', '快照内容不可信')
          throw unavailable('本地库解密或完整性检查未通过')
        }
      } catch (err) {
        checkCancelled(ctx.signal)
        if (logical !== 'session' || args.target?.type !== 'contact' ||
            !(err instanceof CodedOperationError) || !LOCALDB_FALLBACK_CODES.has(err.code)) throw err
        // contact已有message中的S会话时不依赖session；空S仍由resolve报告来源不足。
        // 失败解密可能已创建文件，不能把半结果留给后续query。
        try {
          for (const file of ['session.db.dec.db', 'session.dec.db']) rmSync(join(work.dir, file), { force: true })
        } catch { throw unavailable('可选会话库的无效产物未能清理') }
        log({ type: 'db_fallback', stage: 'optional-session', reason: err.code })
      }
    }
    const query = (params: string[]) => call(['query', '--workdir', work!.dir, ...params]).then(requireQuery)
    const meta = await query(['--task', 'context', '--account', current.account])
    if (typeof meta.self_user_id !== 'string' || meta.self_user_id !== current.account || !Number.isSafeInteger(meta.schema_version)) {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人 sender_id 域身份未确认')
    }
    const generation = hash([sourceId, meta.schema_version])
    if (previous && previous.generation !== generation) throw cursorError()
    const name = args.name.trim().replace(/@微信$/, '').trim()
    const resolved = await query(['--task', 'resolve', '--name', name, '--account', meta.self_user_id])
    const sources = resolved.sources as Record<string, unknown> | undefined
    const required = args.target?.type === 'contact' ? ['user_table']
      : args.target?.type === 'group' ? ['conversation_table'] : ['user_table', 'conversation_table']
    if (!sources || required.some(source => sources[source] !== 'ok')) {
      if (required.some(source => sources?.[source] === 'schema-unknown')) {
        throw new CodedOperationError('VERSION_UNSUPPORTED', '会话定位所需 schema 尚不支持，不能完整消歧')
      }
      throw unavailable('会话定位所需来源不完整，不能确认查无目标或唯一目标')
    }
    const candidates = new Map<string, { name: string; conversation_id: string; type: string }>()
    const unresolvedUsers = new Set<string>()
    for (const raw of Array.isArray(resolved.candidates) ? resolved.candidates : []) {
      if (!raw || typeof raw !== 'object') continue
      const row = raw as Record<string, unknown>
      const id = row.conversation_id
      if (row.source === 'user_table' && typeof row.user_id === 'string' && id === null && args.target?.type !== 'group') {
        unresolvedUsers.add(row.user_id)
      }
      // session的S标题可能是缓存旧名；联系人命名只认user，session仅提供R群名。
      if (typeof id !== 'string' ||
          !(row.source === 'user_table' && id.startsWith('S:')) &&
          !(row.source === 'conversation_table' && id.startsWith('R:'))) continue
      if (id.startsWith('S:') && !id.slice(2).split('_').includes(meta.self_user_id)) continue
      if (args.target?.type === 'contact' && !id.startsWith('S:')) continue
      if (args.target?.type === 'group' && !id.startsWith('R:')) continue
      candidates.set(id, { name: typeof row.name === 'string' ? row.name : name, conversation_id: id, type: id.startsWith('R:') ? 'group' : 'contact' })
    }
    if (candidates.size === 0 && unresolvedUsers.size > 0 && sources.conversation_table !== 'ok') {
      if (sources.conversation_table === 'schema-unknown') throw new CodedOperationError('VERSION_UNSUPPORTED', '没有消息会话，且 session schema 无法用于核实实际会话')
      throw unavailable('没有消息会话，且 session 来源不可用，不能确认查无会话')
    }
    if (candidates.size + unresolvedUsers.size > 1) throw new CodedOperationError('TARGET_AMBIGUOUS', '本地库存在多个同名目标，请进一步明确目标', 'none', {
      candidates: [...candidates.values(), ...[...unresolvedUsers].map(() => ({ name, conversation_id: null, type: 'contact' }))],
    })
    const target = [...candidates.values()][0]
    if (!target) throw new CodedOperationError('TARGET_NOT_FOUND', '完整检索后未找到已识别的目标会话')
    if (previous && previous.conversationId !== target.conversation_id) throw cursorError()
    const params = ['--task', 'messages', '--conv', target.conversation_id, '--limit', String(args.limit ?? 100)]
    if (previous) params.push('--after-id', String(previous.lastMessageId), '--anchor', previous.anchor)
    if (args.sinceDays !== undefined) params.push('--since', String(Math.max(0, Math.floor(now() / 1000) - args.sinceDays * 86400)))
    const messages = await query(params)
    const rows = messages.messages
    const lastId = messages.next_cursor_id
    const anchor = messages.next_anchor
    if (!Array.isArray(rows) || !rows.every(row => row && Number.isSafeInteger(row.message_id) && row.message_id >= 0) ||
        !Number.isSafeInteger(lastId) || typeof lastId !== 'number' || lastId < 0 || typeof anchor !== 'string' ||
        !(lastId === 0 ? anchor === '' : /^[0-9a-f]{64}$/.test(anchor)) ||
        (rows.length > 0 && lastId !== rows.reduce((max, row) => Math.max(max, row.message_id), 0)) ||
        (rows.length === 0 && lastId !== (previous?.lastMessageId ?? 0))) throw cursorError()
    const stillCurrent = await accountProbe(ctx)
    checkCancelled(ctx.signal)
    if (stillCurrent.account !== current.account || stillCurrent.windowPid !== current.windowPid ||
        stillCurrent.dataDir.dir !== current.dataDir.dir) throw new CodedOperationError('IDENTITY_UNCONFIRMED', '读取期间当前账号发生变化，结果未返回')
    if (getFileId(sourceDb) !== sourceId) throw new CodedOperationError('SNAPSHOT_UNSTABLE', '读取期间源库被替换，结果未返回')
    checkCancelled(ctx.signal)
    const cursor = encodeCursor({ account: current.account, conversationId: target.conversation_id, generation, lastMessageId: lastId, anchor })
    outcome = { message: `已通过本地库读取会话消息 ${rows.length} 条`, data: {
      channel: 'local-db', target: args.name, title: target.name, navigated: false,
      conversation_id: target.conversation_id, messages: normalizeMessages(rows as RawMessageRow[], meta.self_user_id),
      cursor, resolved_target: { name: target.name, section: target.type === 'group' ? '群聊' : '联系人', subtitle: '' },
    } }
    const latest = getState()
    if (latest?.mode === 'ui' && latest.kind === 'transient' && latest.checked_at === state?.checked_at) {
      const saved = putState({ ...latest, mode: 'db', kind: undefined, version, checked_at: new Date(now()).toISOString(), reason: 'runtime_read_ok', scan_failure: undefined })
      outcome.data!.state_saved = saved
      if (!saved) outcome.data!.hint = '本次本地库读取成功，运行时模式提升未保存'
      log({ type: 'mode_decided', reason: saved ? 'runtime_promoted' : 'state_write_failed' })
    } else if (latest?.scan_failure) {
      putState({ ...latest, scan_failure: undefined })
    }
    log({ type: 'db_success', stage: 'read-session' })
  } catch (err) {
    failure = err
    failed = true
  } finally {
    if (work && !work.cleanup()) {
      const data = { cleanup_failed: [{ dir: basename(work.dir), reason: 'remove_failed' }] }
      log({ type: 'db_fallback', stage: 'cleanup', reason: 'remove_failed' })
      if (failed) {
        const code: ErrorCode = failure instanceof CodedOperationError ? failure.code : failure instanceof CancelledError ? 'CANCELLED' : 'LOCALDB_UNAVAILABLE'
        failure = new CodedOperationError(code, failure instanceof Error ? failure.message : '本地库读取失败', 'none', {
          ...(failure instanceof CodedOperationError ? failure.data : {}), ...data,
        })
      } else if (outcome) Object.assign(outcome.data!, data)
    }
  }
  if (failed) throw failure
  return outcome!
}
