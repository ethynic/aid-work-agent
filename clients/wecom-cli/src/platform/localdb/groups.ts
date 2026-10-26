/** 群列表和按时间查询，独立于 messages 的入库水位游标。 */
import { basename } from 'node:path'
import { CancelledError, CodedOperationError, type ErrorCode, type OpContext } from '../../operations/types.js'
import type { OperationOutcome } from '../../operations/context.js'
import { admitLocaldbVersion, fileIdentity, hash, prepareDecryptedDbs, unavailable } from './access.js'
import { decodeGroupCursor, encodeGroupCursor, groupCursorError } from './groupCursor.js'
import { readLocalState, writeLocalState } from './matrix.js'
import { checkCancelled, probeCurrentAccount, type CurrentAccount } from './identity.js'
import { runLocaldbPy } from './driver.js'
import { normalizeMessages, type RawMessageRow } from './messages.js'
import type { LocaldbReadDeps } from './readSession.js'

export interface GroupQueryArgs { query?: string; limit: number; targetName?: string; from?: number; to?: number; poll?: boolean; cursor?: string }
interface Group { name: string; conversation_id: string }
interface GroupMention {
  kind: 'user' | 'all'
  user_id: string
  name: string | null
  name_source: 'message' | 'user_table' | 'unresolved'
}
interface GroupHistoryRow extends RawMessageRow {
  sender_name: string | null
  sender_name_source: 'user_table' | 'unresolved'
  mentions: GroupMention[]
  mentions_complete: boolean
}

function validMention(value: unknown): value is GroupMention {
  if (!value || typeof value !== 'object') return false
  const row = value as GroupMention
  return (row.kind === 'user' || row.kind === 'all') && typeof row.user_id === 'string' && /^\d+$/.test(row.user_id) &&
    (row.kind === 'all' ? row.user_id === '0' && row.name === '所有人' : row.user_id !== '0') &&
    (row.name_source === 'unresolved' ? row.name === null :
      (row.name_source === 'message' || row.name_source === 'user_table') && typeof row.name === 'string' && !!row.name.trim())
}

export async function queryLocaldbGroups(
  args: GroupQueryArgs, ctx: OpContext, current: CurrentAccount, deps: LocaldbReadDeps = {},
): Promise<OperationOutcome> {
  checkCancelled(ctx.signal)
  const getState = deps.readStateFn ?? (() => readLocalState(deps.stateDir))
  const putState = deps.writeStateFn ?? (value => writeLocalState(value, deps.stateDir))
  const state = getState()
  const admission = await admitLocaldbVersion(ctx, current, state, { forced: true }, deps)
  const paths = current.dataDir.db_paths!
  if (!paths.message || !paths.user || !paths.session) throw unavailable('群查询所需本地库不完整')
  const getFileId = deps.fileIdentityFn ?? fileIdentity
  const sourcePaths = [paths.message, paths.user, paths.session]
  const sourceIds = sourcePaths.map(path => getFileId(path))
  const { work, sourceId } = await prepareDecryptedDbs(paths, {
    ctx, current, ...admission, state, getState, putState, workPrefix: 'groups',
    tolerateSessionFailure: false, deps,
  })
  let outcome: OperationOutcome | undefined
  let failure: unknown
  let failed = false
  try {
    const query = async (params: string[]): Promise<Record<string, unknown>> => {
      checkCancelled(ctx.signal)
      const result = await (deps.runPyFn ?? runLocaldbPy)(['query', '--workdir', work.dir, ...params], { signal: ctx.signal })
      checkCancelled(ctx.signal)
      if (!result.ok || !result.data) throw unavailable('群查询子进程未完成')
      const data = result.data
      if (data.ok !== true) {
        if (data.error === 'CURSOR_STALE') throw groupCursorError()
        if (data.error === 'CONTENT_TOO_LARGE') throw new CodedOperationError('CONTENT_UNAVAILABLE', '单条群消息超出轮询内容上限，已停止且未推进游标；请人工查看原消息')
        if (data.error === 'SCHEMA_UNSUPPORTED') throw new CodedOperationError('VERSION_UNSUPPORTED', '群查询所需本地库 schema 尚不支持')
        if (data.error === 'IDENTITY_UNCONFIRMED') throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人身份映射未确认')
        throw unavailable('群查询未完成，请运行 doctor --localdb 核对')
      }
      return data
    }
    const meta = await query(['--task', 'context', '--account', current.account])
    if (meta.self_user_id !== current.account || !Number.isSafeInteger(meta.schema_version)) {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '本人 sender_id 域身份未确认')
    }
    const common = { channel: 'local-db', scope: 'current_account_local_synced', limit: args.limit }
    if (args.targetName === undefined) {
      const data = await query(['--task', 'groups', '--limit', String(args.limit),
        ...(args.query === undefined ? [] : ['--name', args.query])])
      if (!Array.isArray(data.groups) || data.count !== data.groups.length || data.groups.length > args.limit ||
          typeof data.has_more !== 'boolean' || data.groups.some((row: Group) =>
            !row || typeof row.name !== 'string' || !row.name.trim() ||
            typeof row.conversation_id !== 'string' || !row.conversation_id.startsWith('R:'))) {
        throw unavailable('群列表返回格式不完整')
      }
      outcome = { message: `已通过本地库列出 ${data.groups.length} 个群${data.has_more ? '（还有更多）' : ''}`,
        data: { ...common, query: args.query ?? null, groups: data.groups, count: data.count, has_more: data.has_more } }
    } else {
      const previous = args.cursor === undefined ? undefined : decodeGroupCursor(args.cursor, args.targetName)
      const generation = hash([sourceId, meta.schema_version])
      if (previous && (previous.account !== current.account || previous.generation !== generation)) throw groupCursorError()
      const resolved = await query(['--task', 'resolve', '--name', args.targetName, '--account', current.account])
      const sources = resolved.sources as Record<string, unknown> | undefined
      if (sources?.conversation_table !== 'ok') {
        if (sources?.conversation_table === 'schema-unknown') throw new CodedOperationError('VERSION_UNSUPPORTED', '群名定位 schema 尚不支持')
        throw unavailable('群名来源不可用，无法完整定位')
      }
      const groups = new Map<string, Group>()
      for (const row of Array.isArray(resolved.candidates) ? resolved.candidates : []) {
        if (row?.source === 'conversation_table' && typeof row.conversation_id === 'string' &&
            row.conversation_id.startsWith('R:') && row.name === args.targetName) groups.set(row.conversation_id, row)
      }
      if (groups.size > 1) throw new CodedOperationError('TARGET_AMBIGUOUS', '本地库存在多个同名群，无法唯一定位', 'none',
        { candidates: [...groups.values()].map(({ name, conversation_id }) => ({ name, conversation_id })) })
      const group = [...groups.values()][0]
      if (!group) throw new CodedOperationError('TARGET_NOT_FOUND', '本机已同步群中没有该完整群名')
      if (previous && previous.conversationId !== group.conversation_id) throw groupCursorError()
      const data = await query(['--task', args.poll ? 'group-poll' : 'history', '--conv', group.conversation_id, '--limit', String(args.limit),
        ...(previous ? ['--after-id', String(previous.lastMessageId), '--anchor', previous.anchor] : []),
        ...(args.from === undefined ? [] : ['--since', String(args.from)]),
        ...(args.to === undefined ? [] : ['--until', String(args.to)])])
      const rows = data.messages as GroupHistoryRow[]
      if (!Array.isArray(rows) || data.count !== rows.length || rows.length > args.limit || typeof data.has_more !== 'boolean' ||
          typeof data.naming_incomplete !== 'boolean' ||
          !Number.isSafeInteger(data.invalid_time_count) || (data.invalid_time_count as number) < 0 ||
          rows.some(row => !row || !Number.isSafeInteger(row.message_id) || row.message_id < 0 ||
            (row.sender_name_source === 'user_table' ? typeof row.sender_name !== 'string' || !row.sender_name.trim() :
              row.sender_name_source !== 'unresolved' || row.sender_name !== null) ||
            typeof row.mentions_complete !== 'boolean' || !Array.isArray(row.mentions) || !row.mentions.every(validMention) ||
            row.conversation_id !== group.conversation_id || !Number.isSafeInteger(row.send_time) || row.send_time! < 0 ||
            row.send_time! > 253402271999 || (args.from !== undefined && row.send_time! < args.from) ||
            (args.to !== undefined && row.send_time! > args.to))) throw unavailable('群消息返回格式不完整')
      const byId = new Map(rows.map(row => [row.message_id, row]))
      const messages = normalizeMessages(rows, current.account).map(message => {
        const row = byId.get(message.msg_id)!
        return { ...message, sender_id: row.sender_id ?? null, send_time: row.send_time,
          sender_name: row.sender_name, sender_name_source: row.sender_name_source,
          mentions: row.mentions, mentions_complete: row.mentions_complete,
          time: new Date((row.send_time! + 8 * 3600) * 1000).toISOString().slice(0, 19).replace('T', ' ') }
      }).sort((a, b) => a.send_time! - b.send_time! || a.msg_id - b.msg_id)
      if (args.poll) {
        const lastId = data.next_cursor_id
        const anchor = data.next_anchor
        const incomingIds = rows.map(row => row.message_id)
        if (!Number.isSafeInteger(lastId) || (lastId as number) < 0 || typeof anchor !== 'string' ||
            ((lastId as number) === 0 ? anchor !== '' : !/^[a-f0-9]{64}$/.test(anchor)) ||
            lastId !== (rows.length ? Math.max(...incomingIds) : previous?.lastMessageId ?? 0) ||
            incomingIds.some((id, index) => id <= (index ? incomingIds[index - 1]! : previous?.lastMessageId ?? 0))) throw groupCursorError()
        outcome = { message: previous ? `群增量消息 ${messages.length} 条` : '已建立群轮询基线，历史仅作上下文', data: {
          ...common, name: group.name, conversation_id: group.conversation_id, self_user_id: current.account,
          context_messages: previous ? [] : messages,
          new_messages: previous ? messages.sort((a, b) => a.msg_id - b.msg_id) : [],
          cursor: encodeGroupCursor({ account: current.account, conversationId: group.conversation_id,
            generation, lastMessageId: lastId as number, anchor }, args.targetName),
          has_more: data.has_more, naming_incomplete: data.naming_incomplete, order: 'message_id_asc', navigated: false,
        } }
      } else outcome = { message: `已通过本地库读取群消息 ${messages.length} 条，按时间升序${data.has_more ? '（范围内还有更早消息）' : ''}`,
        data: { ...common, name: group.name, conversation_id: group.conversation_id, messages, count: messages.length,
          has_more: data.has_more, invalid_time_count: data.invalid_time_count, naming_incomplete: data.naming_incomplete,
          order: 'send_time_asc', selection: 'latest',
          from_time: args.from ?? null, to_time: args.to ?? null, timezone: 'Asia/Shanghai', navigated: false } }
    }
    const after = await (deps.probeAccountFn ?? probeCurrentAccount)(ctx)
    checkCancelled(ctx.signal)
    if (after.account !== current.account || after.windowPid !== current.windowPid || after.dataDir.dir !== current.dataDir.dir) {
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '群查询期间账号发生变化，结果未返回')
    }
    if (sourceId !== sourceIds[0] || sourcePaths.some((path, index) => getFileId(path) !== sourceIds[index])) {
      throw new CodedOperationError('SNAPSHOT_UNSTABLE', '群查询期间源库被替换，结果未返回')
    }
    checkCancelled(ctx.signal)
  } catch (err) { failed = true; failure = err }
  finally {
    if (!work.cleanup()) {
      const data = { cleanup_failed: [{ dir: basename(work.dir), reason: 'remove_failed' }] }
      if (failed) {
        const code: ErrorCode = failure instanceof CodedOperationError ? failure.code : failure instanceof CancelledError ? 'CANCELLED' : 'LOCALDB_UNAVAILABLE'
        failure = new CodedOperationError(code, failure instanceof Error ? failure.message : '群查询失败', 'none', {
          ...(failure instanceof CodedOperationError ? failure.data : {}), ...data,
        })
      } else Object.assign(outcome!.data!, data)
    }
  }
  if (failed) throw failure
  return outcome!
}
