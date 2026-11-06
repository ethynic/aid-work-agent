import { basename } from 'node:path'
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OpContext } from './types.js'
import type { WecomOperation } from './types.js'
import type { OperationOutcome } from './context.js'
import { admitLocaldbVersion, fileIdentity, hash, prepareDecryptedDbs } from '../platform/localdb/access.js'
import { readLocalState, writeLocalState } from '../platform/localdb/matrix.js'
import { checkCancelled, probeCurrentAccount } from '../platform/localdb/identity.js'
import { runLocaldbPy } from '../platform/localdb/driver.js'
import { parseSession, sessionLocator } from '../platform/localdb/sessions.js'
import type { LocaldbReadDeps } from '../platform/localdb/readSession.js'

export interface DirectoryArgs { mode: 'list' | 'preview' | 'resolve' | 'search'; query?: string; type?: 'contact' | 'group' | 'any'; cursor?: string; limit?: number; session?: string; sessions?: string[] }
interface DirectoryCursor { account: string; generation: string; query: string; after: string }

function validate(args: DirectoryArgs): string | null {
  if (!args || typeof args !== 'object' || Array.isArray(args) || !['list', 'preview', 'resolve', 'search'].includes(args.mode)) return 'mode须为list、preview、resolve或search'
  if (Object.keys(args).some(key => !['mode', 'query', 'type', 'cursor', 'limit', 'session', 'sessions'].includes(key))) return '目录参数包含未知字段'
  if (args.mode === 'list') {
    if (args.session !== undefined || args.sessions !== undefined || args.type !== undefined || args.query !== undefined && (typeof args.query !== 'string' || args.query.length > 100) ||
        args.cursor !== undefined && (typeof args.cursor !== 'string' || !args.cursor.startsWith('wdir1.') || args.cursor.length > 4096) ||
        args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > 50)) return '目录查询参数无效'
  } else if (args.mode === 'search') {
    if (args.session !== undefined || args.sessions !== undefined || args.cursor !== undefined ||
        typeof args.query !== 'string' || !args.query.trim() || args.query.length > 100 ||
        args.type !== undefined && !['contact', 'group', 'any'].includes(args.type) ||
        args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > 50)) return '搜索参数无效'
  } else if (args.mode === 'preview' && (args.sessions !== undefined || args.query !== undefined || args.cursor !== undefined || args.type !== undefined || typeof args.session !== 'string' || !args.session.startsWith('ws1.') ||
             args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > 3))) return '预览需要稳定会话标识，最多3条'
  else if (args.mode === 'resolve' && (args.session !== undefined || args.query !== undefined || args.cursor !== undefined || args.limit !== undefined || args.type !== undefined ||
           !Array.isArray(args.sessions) || args.sessions.length < 1 || args.sessions.length > 50 ||
           args.sessions.some(value => typeof value !== 'string' || !value.startsWith('ws1.') || value.length > 2048))) return '核验需要1–50个稳定会话标识'
  return null
}

function decodeCursor(value: string): DirectoryCursor {
  try { return JSON.parse(Buffer.from(value.slice(6), 'base64url').toString('utf8')) as DirectoryCursor }
  catch { throw new CodedOperationError('CURSOR_UNSUPPORTED', '目录游标无效，请重新打开添加会话') }
}

export async function readSessionDirectory(args: DirectoryArgs, ctx: OpContext, deps: LocaldbReadDeps = {}): Promise<OperationOutcome> {
  const current = await (deps.probeAccountFn ?? probeCurrentAccount)(ctx)
  const previous = args.cursor ? decodeCursor(args.cursor) : undefined
  const target = args.mode === 'preview' ? parseSession(args.session!, current.account) : undefined
  const targets = args.mode === 'resolve' ? args.sessions!.map(value => parseSession(value, current.account)) : undefined
  if (target && !('conversation_id' in target)) throw new CodedOperationError('INVALID_ARGUMENT', '预览需要稳定会话标识')
  const getState = deps.readStateFn ?? (() => readLocalState(deps.stateDir))
  const putState = deps.writeStateFn ?? (value => writeLocalState(value, deps.stateDir))
  const state = getState()
  const admission = await admitLocaldbVersion(ctx, current, state, { forced: true }, deps)
  const paths = current.dataDir.db_paths!
  if (!paths.message || !paths.user || !paths.session) throw new CodedOperationError('LOCALDB_UNAVAILABLE', '会话目录所需本地库不完整')
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
    if (!meta.ok || meta.data?.ok !== true || meta.data.self_user_id !== current.account || !Number.isSafeInteger(meta.data.schema_version))
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '当前账号与本地消息身份未确认')
    const generation = hash([sourceId, meta.data.schema_version])
    if (previous && (previous.account !== current.account || previous.generation !== generation ||
        previous.query !== (args.query ?? '') || typeof previous.after !== 'string'))
      throw new CodedOperationError('CURSOR_UNSUPPORTED', '目录账号、源库或搜索条件已变化，请重新查询')
    const raw = await runPy(['directory', '--workdir', work.dir, '--account', current.account], {
      signal: ctx.signal, stdin: JSON.stringify(args.mode === 'search'
        ? { mode: 'search', query: args.query!.trim(), type: args.type ?? 'any', limit: args.limit ?? 20 }
        : args.mode === 'list'
        ? { mode: 'list', query: args.query ?? '', after: previous?.after ?? '', limit: Math.min(args.limit ?? 20, 30) }
        : args.mode === 'preview' ? { mode: 'preview', conversation_id: (target as { conversation_id: string }).conversation_id, limit: args.limit ?? 3 }
          : { mode: 'resolve', conversation_ids: targets!.map(value => (value as { conversation_id: string }).conversation_id) }),
    })
    checkCancelled(ctx.signal)
    const data = raw.data
    if (!raw.ok || !data || data.ok !== true || data.self_user_id !== current.account) {
      const code = data?.error === 'TARGET_NOT_FOUND' ? 'TARGET_NOT_FOUND' : data?.error === 'BAD_ARGS' ? 'INVALID_ARGUMENT'
        : data?.error === 'SCHEMA_UNSUPPORTED' ? 'VERSION_UNSUPPORTED' : data?.error === 'IDENTITY_UNCONFIRMED' ? 'IDENTITY_UNCONFIRMED' : 'LOCALDB_UNAVAILABLE'
      throw new CodedOperationError(code, '本地会话目录读取失败，请检查企微登录与本地库状态')
    }
    const common = { schema_version: 1, self_user_id: current.account,
      self_name: typeof meta.data.self_name === 'string' ? meta.data.self_name : null,
      source_generation: generation }
    if (args.mode === 'search') {
      outcome = { message: '已查询本地会话和联系人名称', data: { ...common,
        query: args.query!.trim(), match_kind: data.match_kind, match_count: data.match_count,
        same_name: data.same_name, candidates: data.candidates } }
    } else if (args.mode === 'list') {
      const conversations = (data.conversations as { conversation_id: string; name: string | null; kind: string; peer_platform: string; same_name: boolean }[])
        .map(row => ({ session: sessionLocator(current.account, row.conversation_id), name: row.name,
          kind: row.kind, peer_platform: row.peer_platform, same_name: row.same_name }))
      const next_cursor = typeof data.next_after === 'string' ? 'wdir1.' + Buffer.from(JSON.stringify({ account: current.account,
        generation, query: args.query ?? '', after: data.next_after })).toString('base64url') : null
      outcome = { message: '已读取本地会话目录', data: { ...common, conversations, next_cursor } }
    } else if (args.mode === 'preview') {
      const messages = data.messages as Record<string, unknown>[]
      outcome = { message: '已读取会话预览', data: { ...common, session: args.session, messages } }
    } else {
      const resolved_targets = (data.resolved_targets as { conversation_id: string; name: string | null; kind: string; peer_platform: string }[])
        .map(row => ({ session: sessionLocator(current.account, row.conversation_id), name: row.name,
          kind: row.kind, peer_platform: row.peer_platform }))
      if (resolved_targets.length !== args.sessions!.length || resolved_targets.some((row, i) => row.session !== args.sessions![i]))
        throw new CodedOperationError('IDENTITY_UNCONFIRMED', '目标会话核验结果不完整')
      outcome = { message: '已核验添加范围', data: { ...common, resolved_targets } }
    }
    if (Buffer.byteLength(JSON.stringify(outcome.data), 'utf8') > 64 * 1024)
      throw new CodedOperationError('CONTENT_UNAVAILABLE', '会话目录结果超过大小上限，请缩小单页或分批添加')
    const after = await (deps.probeAccountFn ?? probeCurrentAccount)(ctx)
    if (after.account !== current.account || after.windowPid !== current.windowPid || after.dataDir.dir !== current.dataDir.dir)
      throw new CodedOperationError('IDENTITY_UNCONFIRMED', '目录读取期间当前账号发生变化')
    if (sourcePaths.some((path, index) => getFileId(path) !== identities[index]))
      throw new CodedOperationError('CURSOR_UNSUPPORTED', '目录读取期间源库发生变化，请重新查询')
    checkCancelled(ctx.signal)
  } catch (error) { failure = error }
  finally {
    if (!work.cleanup()) {
      if (failure) failure = new CodedOperationError('LOCALDB_UNAVAILABLE', '本地目录清理失败')
      else Object.assign(outcome!.data!, { cleanup_failed: [{ dir: basename(work.dir), reason: 'remove_failed' }] })
    }
  }
  if (failure) throw failure
  return outcome!
}

export function createWecomSessionDirectoryOperation(local: LocaldbReadDeps = {}): WecomOperation<DirectoryArgs> {
  return { name: 'wecom_session_directory', execute: (args, ctx) => runWecomOperation('readonly', ctx,
    () => validate(args), opCtx => readSessionDirectory(args, opCtx, local)) }
}
