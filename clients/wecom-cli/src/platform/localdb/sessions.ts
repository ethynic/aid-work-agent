/** Three-tool DB read facade. Locators identify conversations; they grant no authority. */
import { basename } from 'node:path'
import { CodedOperationError, CancelledError, type ErrorCode, type OpContext } from '../../operations/types.js'
import type { OperationOutcome } from '../../operations/context.js'
import { admitLocaldbVersion, fileIdentity, hash, prepareDecryptedDbs, unavailable } from './access.js'
import { readLocalState, writeLocalState } from './matrix.js'
import { checkCancelled, probeCurrentAccount, type CurrentAccount } from './identity.js'
import { runLocaldbPy } from './driver.js'
import { normalizeMessages, type RawMessageRow } from './messages.js'
import type { LocaldbReadDeps } from './readSession.js'

export const SESSION_READ_LIMIT = 50
export const SESSION_RESULT_BYTES = 16 * 1024
export interface AttachmentRead { message_id: number; offset?: number; max_bytes?: number }
export interface SessionReadArgs { sessions?: string[]; cursor?: string; limit?: number; single?: boolean; metadataOnly?: boolean; attachment?: AttachmentRead }
interface Locator { account: string; conversation_id: string }
interface Cursor { account: string; generation: string; single: boolean; scope: string[] | null; state: Record<string, unknown>;
  targets?: ({ conversation_id: string } | { name: string })[] | null; requested?: string[] | null }

function decode(value: string, prefix: string): unknown {
  try { return JSON.parse(Buffer.from(value.slice(prefix.length), 'base64url').toString('utf8')) }
  catch { throw new CodedOperationError('INVALID_ARGUMENT', '会话标识或游标格式无效') }
}
export function sessionLocator(account: string, conversationId: string): string {
  return 'ws1.' + Buffer.from(JSON.stringify({ account, conversation_id: conversationId })).toString('base64url')
}
export function parseSession(value: string, account: string): { conversation_id: string } | { name: string } {
  if (!value.startsWith('ws1.')) return { name: value.trim() }
  const locator = decode(value, 'ws1.') as Locator
  if (!locator || locator.account !== account || typeof locator.conversation_id !== 'string' ||
      !/^[RS]:.+/.test(locator.conversation_id) || locator.conversation_id.length > 512) {
    throw new CodedOperationError('IDENTITY_UNCONFIRMED', '会话标识不属于当前账号或目标无效')
  }
  return { conversation_id: locator.conversation_id }
}
export function validateSessionRead(args: SessionReadArgs): string | null {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return '参数必须是对象'
  if (args.sessions !== undefined && (!Array.isArray(args.sessions) || !args.sessions.length || args.sessions.length > 50 ||
      args.sessions.some(value => typeof value !== 'string' || !value.trim() || value.length > (value.startsWith('ws1.') ? 2048 : 100)))) return 'sessions须为1–50个会话名称或读取返回的会话标识'
  if (args.single && args.sessions?.length !== 1) return '单会话读取须提供一个session'
  if (args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > SESSION_READ_LIMIT)) return 'limit须为1–50的整数（整个响应批次）'
  if (args.cursor !== undefined && (typeof args.cursor !== 'string' || !args.cursor.startsWith('wb1.') || args.cursor.length > 16384)) return 'cursor须为原读取返回的游标'
  if (args.attachment !== undefined) {
    const a = args.attachment
    if (!args.single || args.cursor !== undefined || args.metadataOnly) return '附件读取只用于单会话，不接受cursor或纯定位参数'
    if (!a || typeof a !== 'object' || Array.isArray(a) || Object.keys(a).some(k => !['message_id', 'offset', 'max_bytes'].includes(k)) ||
      !Number.isSafeInteger(a.message_id) || a.message_id < 1 ||
      (a.offset !== undefined && (!Number.isSafeInteger(a.offset) || a.offset < 0)) ||
      (a.max_bytes !== undefined && (!Number.isInteger(a.max_bytes) || a.max_bytes < 1 || a.max_bytes > 8192))) return 'attachment须含正整数message_id，可选offset>=0与max_bytes=1..8192'
  }
  return null
}

export async function readBusinessSessions(args: SessionReadArgs, ctx: OpContext, deps: LocaldbReadDeps = {}): Promise<OperationOutcome> {
  const current = await (deps.probeAccountFn ?? probeCurrentAccount)(ctx)
  const previous = args.cursor ? decode(args.cursor, 'wb1.') as Cursor : undefined
  let targets = args.sessions?.map(value => parseSession(value, current.account))
  if (previous) {
    const equal = (a: string[], b: string[]) => a.length === b.length && [...a].sort().every((value, i) => value === [...b].sort()[i])
    if (args.sessions && previous.scope === null || args.sessions && previous.scope &&
        !equal(args.sessions, previous.requested ?? []) &&
        !(targets!.every(target => 'conversation_id' in target) &&
          equal(targets!.map(target => (target as { conversation_id: string }).conversation_id), previous.scope))) {
      throw new CodedOperationError('CURSOR_UNSUPPORTED', '原游标目标与当前会话不一致，未重建基线')
    }
    // A cursor keeps its original stable targets even when names are reused or one DB row is temporarily absent.
    targets = previous.targets ?? previous.scope?.map(conversation_id => ({ conversation_id })) ?? undefined
  }
  const getState = deps.readStateFn ?? (() => readLocalState(deps.stateDir))
  const putState = deps.writeStateFn ?? (value => writeLocalState(value, deps.stateDir))
  const state = getState()
  const admission = await admitLocaldbVersion(ctx, current, state, { forced: true }, deps)
  const paths = current.dataDir.db_paths!
  if (!paths.message || !paths.user || !paths.session) throw unavailable('会话读取所需本地库不完整')
  const getFileId = deps.fileIdentityFn ?? fileIdentity
  const sourcePaths = [paths.message, paths.user, paths.session]
  const identities = sourcePaths.map(path => getFileId(path))
  const { work, sourceId } = await prepareDecryptedDbs(paths, {
    ctx, current, ...admission, state, getState, putState, workPrefix: 'read-session', tolerateSessionFailure: false, deps,
  })
  let outcome: OperationOutcome | undefined
  let failure: unknown
  try {
    const runPy = deps.runPyFn ?? runLocaldbPy
    const meta = await runPy(['query', '--workdir', work.dir, '--task', 'context', '--account', current.account], { signal: ctx.signal })
    if (!meta.ok || meta.data?.ok !== true || meta.data.self_user_id !== current.account || !Number.isSafeInteger(meta.data.schema_version)) {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '当前账号与本地消息身份未确认')
    }
    const generation = hash([sourceId, meta.data.schema_version])
    if (previous && (previous.account !== current.account || previous.generation !== generation || previous.single !== !!args.single ||
        !previous.state || typeof previous.state !== 'object')) throw new CodedOperationError('CURSOR_UNSUPPORTED', '原游标的账号、消息库或读取范围已变化；保留原进度后核对')
    let limit = args.limit ?? SESSION_READ_LIMIT
    for (;;) {
      const raw = await runPy(['sessions', '--workdir', work.dir, '--account', current.account], {
        signal: ctx.signal, stdin: JSON.stringify({ targets, state: previous?.state, limit: args.attachment ? 1 : limit,
          metadata_only: args.metadataOnly, attachment: args.attachment, account_dir: current.dataDir.dir }),
      })
      checkCancelled(ctx.signal)
      const data = raw.data
      if (!raw.ok || !data || data.ok !== true) {
        const code = data?.error === 'CURSOR_STALE' ? 'CURSOR_UNSUPPORTED' : data?.error === 'IDENTITY_UNCONFIRMED' ? 'IDENTITY_UNCONFIRMED'
          : data?.error === 'SCHEMA_UNSUPPORTED' ? 'VERSION_UNSUPPORTED' : data?.error === 'TARGET_AMBIGUOUS' ? 'TARGET_AMBIGUOUS'
            : data?.error === 'TARGET_NOT_FOUND' ? 'TARGET_NOT_FOUND' : data?.error === 'BAD_ARGS' ? 'INVALID_ARGUMENT' : 'LOCALDB_UNAVAILABLE'
        throw new CodedOperationError(code, '本地库会话读取未完成，原游标未推进；请按原因恢复后继续', 'none',
          { stage: 'read', cause: data?.error ?? 'DRIVER_UNAVAILABLE', recovery_action: 'retry_original_read' })
      }
      const selected = data.selected_sessions as string[] | undefined
      if (args.attachment) {
        const id = selected?.[0]
        if (!id) {
          const failed = data.failed as { code: ErrorCode }[] | undefined
          throw new CodedOperationError(failed?.[0]?.code ?? 'TARGET_NOT_FOUND', '附件目标会话尚未定位', 'none',
            { stage: 'resolve', recovery_action: 'resolve_original_session' })
        }
        if (!data.attachment || typeof data.attachment !== 'object') throw unavailable('附件读取未返回原消息结果')
        outcome = { message: '已读取目标消息附件状态', data: { channel: 'local-db', navigated: false,
          session: sessionLocator(current.account, id), attachment: data.attachment } }
        break
      }
      const sessions = (data.sessions as { conversation_id: string; messages: RawMessageRow[]; name: string | null; kind: string }[]).map(row => {
        const byId = new Map(row.messages.map(message => [message.message_id, message as RawMessageRow & { mentions?: unknown[]; mentions_complete?: boolean; sender_name_source?: string; baseline?: boolean }]))
        return { ...row, session: sessionLocator(current.account, row.conversation_id),
          messages: normalizeMessages(row.messages, current.account).map(message => ({ ...message,
            mentions: byId.get(message.msg_id)?.mentions ?? [], mentions_complete: byId.get(message.msg_id)?.mentions_complete ?? false,
            sender_name_source: byId.get(message.msg_id)?.sender_name_source ?? 'unresolved',
            baseline: byId.get(message.msg_id)?.baseline ?? data.baseline === true })) }
      })
      const encodeCursor = (cursor: Cursor) => 'wb1.' + Buffer.from(JSON.stringify(cursor)).toString('base64url')
      const failed = data.failed as { target?: { conversation_id: string } | { name: string }; code: ErrorCode; stage?: string }[]
      const scope = targets ? [...new Set([...(previous?.scope ?? []), ...(selected ?? sessions.map(row => row.conversation_id))])] : null
      const cursor = encodeCursor({ account: current.account, generation, single: !!args.single, scope,
        targets: targets ? [...(selected ?? []).map(conversation_id => ({ conversation_id })),
          ...failed.filter(row => row.stage === 'resolve' && row.target).map(row => row.target!)] : null,
        requested: previous?.requested ?? args.sessions ?? null, state: data.state as Record<string, unknown> })
      const pending = (data.pending_sessions as { conversation_id: string; unknown_messages?: {
        msg_id: number; sender_id: string; recovery_point: { after_id: number; anchor: string } }[] }[]).map(row => ({ ...row,
        session: sessionLocator(current.account, row.conversation_id),
        unknown_messages: (row.unknown_messages ?? []).map(({ recovery_point, ...message }) => ({ ...message,
          recovery_cursor: encodeCursor({ account: current.account, generation, single: true, scope: [row.conversation_id],
            targets: [{ conversation_id: row.conversation_id }], state: { ...recovery_point,
              start_fence: (data.state as Record<string, unknown>).start_fence, retry: {}, unresolved: {}, context_after: null } }) })) }))
      const result: Record<string, unknown> = { channel: 'local-db', scope: 'current_account_local_synced', navigated: false,
        self_user_id: current.account, baseline: data.baseline, history_truncated: data.history_truncated,
        sessions, pending_sessions: pending, failed: data.failed, cursor, has_more: data.has_more }
      if (args.single) {
        delete result.sessions
        const row = sessions[0]
        const details = (data.session_details as { conversation_id: string; name: string | null; kind: string; peer_platform: string }[] | undefined)?.[0]
        if (!row && failed.length && (!args.metadataOnly || failed[0]?.stage === 'resolve')) throw new CodedOperationError(failed[0]!.code, '目标会话读取未完成；请核对目标或原消息', 'none', { ...failed[0], recovery_action: 'resolve_original_session' })
        if (!row && !selected?.length) throw new CodedOperationError('TARGET_NOT_FOUND', '本机已同步会话中没有唯一目标')
        const id = row?.conversation_id ?? selected![0]!
        Object.assign(result, { session: sessionLocator(current.account, id), conversation_id: id,
          title: details?.name ?? row?.name ?? null, kind: details?.kind ?? row?.kind, peer_platform: details?.peer_platform ?? 'unknown',
          messages: row?.messages ?? [], new_messages: row?.messages.filter(message => !message.baseline) ?? [] })
      }
      if (args.metadataOnly) {
        for (const key of ['cursor', 'messages', 'new_messages', 'pending_sessions', 'sessions', 'has_more', 'baseline', 'history_truncated']) delete result[key]
        result.resolve_only = true
      }
      if (Buffer.byteLength(JSON.stringify(result), 'utf8') <= SESSION_RESULT_BYTES - 512) {
        outcome = { message: args.metadataOnly ? '已定位目标会话' : args.single ? '已读取目标会话' : `已读取${sessions.length}个会话，失败项单列`, data: result }
        break
      }
      if (limit === 1) throw new CodedOperationError('CONTENT_UNAVAILABLE', '单个会话结果或恢复进度超出响应大小；原游标未推进', 'none', { stage: 'read', recovery_action: 'inspect_original_message' })
      limit = Math.max(1, Math.floor(limit / 2))
    }
    const after = await (deps.probeAccountFn ?? probeCurrentAccount)(ctx)
    if (after.account !== current.account || after.windowPid !== current.windowPid || after.dataDir.dir !== current.dataDir.dir) throw new CodedOperationError('IDENTITY_UNCONFIRMED', '读取期间当前账号发生变化，未返回结果')
    if (sourceId !== identities[0] || sourcePaths.some((path, index) => getFileId(path) !== identities[index])) throw new CodedOperationError('SNAPSHOT_UNSTABLE', '读取期间源库发生替换，原游标未推进')
    checkCancelled(ctx.signal)
  } catch (error) { failure = error }
  finally {
    if (!work.cleanup()) {
      const data = { cleanup_failed: [{ dir: basename(work.dir), reason: 'remove_failed' }] }
      if (failure) failure = new CodedOperationError(failure instanceof CodedOperationError ? failure.code : failure instanceof CancelledError ? 'CANCELLED' : 'LOCALDB_UNAVAILABLE',
        failure instanceof Error ? failure.message : '会话读取失败', 'none', { ...(failure instanceof CodedOperationError ? failure.data : {}), ...data })
      else Object.assign(outcome!.data!, data)
    }
  }
  if (failure) throw failure
  return outcome!
}
