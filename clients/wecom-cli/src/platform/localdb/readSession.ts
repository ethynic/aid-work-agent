/** P1b 单次本地库读取；与 doctor 分离，复用只读原语和产物生命周期。
 * 密钥获取/快照解密段已提取为共享 helper（./access.js，P2 任务 3.1，unread 复用同段），
 * 本文件保留业务语义：resolve 定位消歧、messages 游标、transient 运行时提升。 */
import { basename } from 'node:path'
import { CancelledError, CodedOperationError, type ErrorCode, type OpContext } from '../../operations/types.js'
import type { OperationOutcome } from '../../operations/context.js'
import type { TargetRefIdentity } from '../targetRef.js'
import type { LocalDbState } from './matrix.js'
import { readLocalState, writeLocalState } from './matrix.js'
import {
  admitLocaldbVersion,
  fileIdentity,
  hash,
  prepareDecryptedDbs,
  unavailable,
} from './access.js'
export { LOCALDB_FALLBACK_CODES } from './access.js'
import type { LocaldbAccessDeps } from './access.js'
import { runLocaldbPy } from './driver.js'
import { logLocaldbEvent, type LocaldbLogEvent } from './log.js'
import { checkCancelled, probeCurrentAccount, type CurrentAccount } from './identity.js'
import { decodeCursor, encodeCursor, normalizeMessages, type RawMessageRow } from './messages.js'
import { hasExternalWechatHint } from './contactScope.js'

export interface LocaldbReadArgs {
  name: string
  target?: TargetRefIdentity
  limit?: number
  cursor?: string
  sinceDays?: number
  /** 强制通道覆盖落盘选择；不绕过实际版本矩阵。 */
  forced?: boolean
}

export interface LocaldbReadDeps extends LocaldbAccessDeps {
  probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
  readStateFn?: () => LocalDbState | null
  writeStateFn?: (state: LocalDbState) => boolean
  /** 测试/定制布局均需显式传给状态与密钥存储，不能误写真实用户目录。 */
  stateDir?: string | null
}

function cursorError(): CodedOperationError {
  return new CodedOperationError('CURSOR_UNSUPPORTED', '游标与当前账号、会话或数据库代际不一致，请重新读取建立基线')
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
  const admission = await admitLocaldbVersion(ctx, current, state, { forced: args.forced }, deps)
  const paths = current.dataDir.db_paths!
  const prepared = await prepareDecryptedDbs(
    { message: paths.message, user: paths.user, session: paths.session },
    {
      ctx, current, version: admission.version, expected: admission.expected, algorithm: admission.algorithm,
      state, getState, putState, workPrefix: 'read-session',
      tolerateSessionFailure: args.target?.type === 'contact', deps,
    },
  )
  const { work, sourceId } = prepared
  let outcome: OperationOutcome | null = null
  let failure: unknown
  let failed = false

  try {
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
      throw unavailable('本地库查询未完成: ' + JSON.stringify(value).slice(0, 300))
    }
    const query = (params: string[]) => call(['query', '--workdir', work.dir, ...params]).then(requireQuery)
    const meta = await query(['--task', 'context', '--account', current.account])
    if (typeof meta.self_user_id !== 'string' || meta.self_user_id !== current.account || !Number.isSafeInteger(meta.schema_version)) {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人 sender_id 域身份未确认')
    }
    const generation = hash([sourceId, meta.schema_version])
    if (previous && previous.generation !== generation) throw cursorError()
    const name = args.name.trim().replace(/@微信$/, '').trim()
    const externalOnly = hasExternalWechatHint(args.name, args.target?.type, args.target?.subtitle)
    const resolved = await query(['--task', 'resolve', '--name', name, '--account', meta.self_user_id,
      ...(externalOnly ? ['--external-only'] : [])])
    const sources = resolved.sources as Record<string, unknown> | undefined
    const required = args.target?.type === 'contact' || externalOnly ? ['user_table']
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
      if (externalOnly && (row.source !== 'user_table' || row.contact_scope !== 'external')) {
        throw new CodedOperationError('VERSION_UNSUPPORTED', '外部联系人身份来源不完整，不能按同名目标读取')
      }
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
    const params = ['--task', 'messages', '--conv', target.conversation_id, '--limit', String(args.limit ?? 100), '--account', current.account]
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
    if (getFileId(paths.message!) !== sourceId) throw new CodedOperationError('SNAPSHOT_UNSTABLE', '读取期间源库被替换，结果未返回')
    checkCancelled(ctx.signal)
    const cursor = encodeCursor({ account: current.account, conversationId: target.conversation_id, generation, lastMessageId: lastId, anchor })
    outcome = { message: `已通过本地库读取会话消息 ${rows.length} 条`, data: {
      channel: 'local-db', target: args.name, title: target.name, navigated: false,
      conversation_id: target.conversation_id, messages: normalizeMessages(rows as RawMessageRow[], meta.self_user_id),
      cursor, resolved_target: { name: target.name, section: target.type === 'group' ? '群聊' : '联系人', subtitle: '' },
    } }
    const latest = getState()
    if (latest?.mode === 'ui' && latest.kind === 'transient' && latest.checked_at === state?.checked_at) {
      const saved = putState({ ...latest, mode: 'db', kind: undefined, version: admission.version, checked_at: new Date(now()).toISOString(), reason: 'runtime_read_ok', scan_failure: undefined })
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
    if (!work.cleanup()) {
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
