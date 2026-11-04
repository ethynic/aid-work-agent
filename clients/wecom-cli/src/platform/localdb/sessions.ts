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
/** 日期模式每页默认条数（设计 §4.1：每页总数默认 100，预算不足可更少并续页）。 */
export const DAY_READ_LIMIT = 100
export interface AttachmentRead { message_id: number; offset?: number; max_bytes?: number }
export interface SessionReadArgs { sessions?: string[]; cursor?: string; limit?: number; single?: boolean; metadataOnly?: boolean; attachment?: AttachmentRead;
  date?: string; timezone?: string; include_context?: boolean }
interface Locator { account: string; conversation_id: string }
interface Cursor { account: string; generation: string; single: boolean; scope: string[] | null; state: Record<string, unknown>;
  targets?: ({ conversation_id: string } | { name: string })[] | null; requested?: string[] | null }
/** 日期模式游标（wd1）：绑定账号/代次/会话/UTC 日期范围/背景复合边界/源消息上界/首轮快照与页尾键。 */
interface DayCursor { account: string; generation: string; conversation_id: string; date: string; tz: string;
  include_context: boolean; start: number; end: number; context_start: number | null;
  context_msg_id: number | null; max_msg_id: number; snapshot_at: number;
  last_send_time: number; last_msg_id: number }

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
  // 日期模式（设计 §4.1，评审 P1-2）：显式 date 或 wd1 日期游标均按日期模式校验
  // （标准续页只传 cursor）；仅单会话；与附件/纯定位分开使用。
  const dayMode = args.date !== undefined
    || (typeof args.cursor === 'string' && args.cursor.startsWith('wd1.'))
  if (dayMode) {
    if (!args.single || !args.sessions?.[0]?.startsWith('ws1.')) return '日期模式只用于稳定会话标识的单会话读取'
    if (args.date !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(args.date)) return 'date须为YYYY-MM-DD'
    if (args.timezone !== undefined && (typeof args.timezone !== 'string' || !args.timezone.length || args.timezone.length > 64)) return 'timezone须为IANA时区名'
    if (args.include_context !== undefined && typeof args.include_context !== 'boolean') return 'include_context须为布尔值'
    if (args.attachment !== undefined || args.metadataOnly) return '日期模式与附件/纯定位分开使用'
    if (args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > DAY_READ_LIMIT)) return `limit须为1–${DAY_READ_LIMIT}的整数`
    if (args.cursor !== undefined) {
      if (typeof args.cursor !== 'string' || !args.cursor.startsWith('wd1.') || args.cursor.length > 16384) return '日期模式只接受日期读取返回的游标（wd1.）'
    }
    return null
  }
  if (args.timezone !== undefined || args.include_context !== undefined) return 'timezone/include_context仅在日期模式可用'
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

/** 正文降级占位（评审三 P2-6）：仅替换超大正文/引用，保留已知身份与小型事实。 */
export function dayPlaceholderMessage(
  placeholder: ReturnType<typeof normalizeMessages>[number],
): Record<string, unknown> {
  // 合成占位正文不可信（评审四 P2-2）：parse_trusted=false 沿服务端 UPSERT
  // 规则保留已保存的可信原文，避免占位文案覆盖真实聊天记录。
  return { ...placeholder,
    parse_trusted: false,
    text: '[该条内容超出单页大小限制，本页返回占位记录并继续后续消息]',
    unreadable_reason: 'MESSAGE_OVER_RESULT_BUDGET' }
}

/**
 * 日期模式读取（设计 §4.1/§4.2）：单会话按 [start,end) 半开 UTC 日期范围
 * keyset 分页；include_context=true 首次带该日零点前最近 100 条背景，与当日
 * 消息按原时间共同分页。首轮固定源消息上界与 snapshot_at，续页/恢复不重取。
 * 日期历史不推进增量（wb1）游标，也不产生自动回复候选。
 */
export async function readSessionDay(
  args: { session: string; date?: string; timezone?: string; include_context?: boolean; limit?: number; cursor?: string },
  ctx: OpContext, deps: LocaldbReadDeps = {},
): Promise<OperationOutcome> {
  const current = await (deps.probeAccountFn ?? probeCurrentAccount)(ctx)
  const target = parseSession(args.session, current.account)
  if (!('conversation_id' in target)) throw new CodedOperationError('INVALID_ARGUMENT', '日期模式需要稳定会话标识（ws1.）')
  const conversationId = target.conversation_id
  const previous = args.cursor ? decode(args.cursor, 'wd1.') as DayCursor : undefined
  if (previous && (previous.account !== current.account || previous.conversation_id !== conversationId)) {
    throw new CodedOperationError('CURSOR_UNSUPPORTED', '日期游标与当前账号或会话不一致，请重新按日期读取')
  }
  if (previous && args.date !== undefined && args.date !== previous.date) {
    throw new CodedOperationError('CURSOR_UNSUPPORTED', '续页日期与原游标范围不一致；省略参数以继承原范围')
  }
  if (previous && args.timezone !== undefined && args.timezone !== previous.tz) {
    throw new CodedOperationError('CURSOR_UNSUPPORTED', '续页时区与原游标范围不一致；省略参数以继承原范围')
  }
  if (previous && args.include_context !== undefined && args.include_context !== previous.include_context) {
    throw new CodedOperationError('CURSOR_UNSUPPORTED', '续页不能改变背景范围；省略参数以继承原值')
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
    if (previous && previous.generation !== generation) {
      throw new CodedOperationError('CURSOR_UNSUPPORTED', '原游标的账号、消息库或读取范围已变化；保留原进度后核对')
    }
    let limit = args.limit ?? DAY_READ_LIMIT
    for (;;) {
      const params = ['query', '--workdir', work.dir, '--account', current.account,
        '--task', 'day-messages', '--conv', conversationId, '--limit', String(limit)]
      if (previous) {
        params.push('--start', String(previous.start), '--end', String(previous.end),
          '--max-msg-id', String(previous.max_msg_id))
        // 复合背景边界成对传递；零点前无消息时驱动不返回背景下界（两者皆空），
        // 续页省略背景参数、include_context 仍继承（评审二 P1-1）。
        if (previous.context_start !== null && previous.context_msg_id !== null) {
          params.push('--context-start', String(previous.context_start))
          params.push('--context-msg-id', String(previous.context_msg_id))
        }
        if (previous.include_context) params.push('--include-context')
        params.push('--after-send-time', String(previous.last_send_time), '--after-msg-id', String(previous.last_msg_id))
      } else {
        params.push('--date', args.date!)
        if (args.timezone) params.push('--tz', args.timezone)
        if (args.include_context) params.push('--include-context')
      }
      const raw = await runPy(params, { signal: ctx.signal })
      checkCancelled(ctx.signal)
      const data = raw.data
      if (!raw.ok || !data || data.ok !== true) {
        const code = data?.error === 'CURSOR_STALE' ? 'CURSOR_UNSUPPORTED' : data?.error === 'IDENTITY_UNCONFIRMED' ? 'IDENTITY_UNCONFIRMED'
          : data?.error === 'SCHEMA_UNSUPPORTED' ? 'VERSION_UNSUPPORTED' : data?.error === 'BAD_ARGS' ? 'INVALID_ARGUMENT' : 'LOCALDB_UNAVAILABLE'
        throw new CodedOperationError(code, '日期读取未完成；请按原因恢复后重试', 'none',
          { stage: 'read', cause: data?.error ?? 'DRIVER_UNAVAILABLE', recovery_action: 'retry_original_read' })
      }
      const rows = (Array.isArray(data.messages) ? data.messages : []) as (RawMessageRow & {
        mentions?: unknown[]; mentions_complete?: boolean; sender_name_source?: string })[]
      const byId = new Map(rows.map(row => [row.message_id, row]))
      const base = previous ?? {
        account: current.account, generation, conversation_id: conversationId, date: args.date!,
        tz: args.timezone ?? 'Asia/Shanghai', include_context: args.include_context === true,
        start: data.start_utc as number, end: data.end_utc as number,
        context_start: (data.context_start ?? null) as number | null,
        context_msg_id: (data.context_msg_id ?? null) as number | null,
        max_msg_id: data.max_message_id as number, snapshot_at: data.snapshot_at as number,
        last_send_time: 0, last_msg_id: 0,
      }
      const encodedSize = (value: unknown) => Buffer.byteLength(JSON.stringify(value), 'utf8')
      const decorate = (message: ReturnType<typeof normalizeMessages>[number]) => ({ ...message,
        mentions: byId.get(message.msg_id)?.mentions ?? [], mentions_complete: byId.get(message.msg_id)?.mentions_complete ?? false,
        sender_name_source: byId.get(message.msg_id)?.sender_name_source ?? 'unresolved' })
      // 评审 P1-3：日期模式的取数、归一后顺序、预算裁剪和游标推进必须使用同一
      // 复合键 (send_time, msg_id)——normalizeMessages 的展示排序（同秒按
      // server_id）会破坏 keyset 一致性，这里按 SQL 键重排（仅日期模式）。
      const normalizedAll = normalizeMessages(rows, current.account).map(decorate)
        .sort((a, b) => (a.send_time ?? 0) - (b.send_time ?? 0) || a.msg_id - b.msg_id)
      // 预算内装多少条（设计 §4.3）：按实际编码后的整页大小减少本页消息数；
      // 因页预算未返回的正常消息不推进页尾键；单条超预算输出占位记录并推进该条。
      const pageBudget = SESSION_RESULT_BYTES - 4096
      let used = 0
      let keep = 0
      let placeholder: ReturnType<typeof normalizeMessages>[number] | null = null
      for (const message of normalizedAll) {
        const size = encodedSize(message)
        if (used + size > pageBudget) {
          if (keep === 0) placeholder = message
          break
        }
        used += size
        keep += 1
      }
      let messages: Record<string, unknown>[]
      let lastReturned: { msg_id: number; send_time?: number } | null = null
      if (placeholder !== null) {
        messages = [dayPlaceholderMessage(placeholder)]
        lastReturned = placeholder
      } else {
        messages = normalizedAll.slice(0, keep)
        lastReturned = keep > 0 ? normalizedAll[keep - 1]! : null
      }
      const truncatedByBudget = placeholder !== null || keep < normalizedAll.length
      const hasMore = truncatedByBudget || data.has_more === true
      const cursor: DayCursor = { ...base,
        last_send_time: lastReturned ? (lastReturned.send_time ?? base.start) : base.last_send_time,
        last_msg_id: lastReturned ? lastReturned.msg_id : base.last_msg_id }
      const readWindow = { start_utc: base.start, end_utc: base.end, snapshot_at: base.snapshot_at,
        ...(base.context_start !== null ? { context_start_utc: base.context_start,
          ...(base.context_msg_id !== null ? { context_start_msg_id: base.context_msg_id } : {}) } : {}) }
      const result: Record<string, unknown> = { channel: 'local-db', scope: 'current_account_local_synced', navigated: false,
        self_user_id: current.account, source_generation: generation,
        session: sessionLocator(current.account, conversationId), conversation_id: conversationId,
        date: base.date, timezone: base.tz, include_context: base.include_context,
        read_window: readWindow, messages, has_more: hasMore,
        cursor: 'wd1.' + Buffer.from(JSON.stringify(cursor)).toString('base64url'),
        ...(data.naming_incomplete === true ? { naming_incomplete: true } : {}),
        ...(data.invalid_time_count ? { invalid_time_count: data.invalid_time_count } : {}) }
      if (encodedSize(result) <= SESSION_RESULT_BYTES - 512 || limit === 1 || messages.length === 0) {
        outcome = { message: `已读取会话 ${base.date} 消息 ${messages.length} 条（背景范围见 read_window）`, data: result }
        break
      }
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
          source_generation: generation,
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
        self_user_id: current.account, source_generation: generation,
        baseline: data.baseline, history_truncated: data.history_truncated,
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
        for (const key of ['cursor', 'messages', 'new_messages', 'pending_sessions', 'sessions', 'has_more', 'baseline', 'history_truncated', 'source_generation']) delete result[key]
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
