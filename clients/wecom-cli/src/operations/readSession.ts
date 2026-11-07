/** 读取会话：只使用已验证客户端版本的本地库；不切换会话或清角标。 */
import { runWecomOperation } from './context.js'
import { readBusinessSessions, readSessionDay, validateSessionRead, type AttachmentRead } from '../platform/localdb/sessions.js'
import { CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { verifyTargetRef, type VerifyTargetRefFn, type TargetRefIdentity } from '../platform/targetRef.js'
import { validateTargetSelector } from './navigate.js'
import { probeCurrentAccount, checkCancelled, type CurrentAccount } from '../platform/localdb/identity.js'
import { readLocaldbSession, type LocaldbReadDeps } from '../platform/localdb/readSession.js'
import { readLocalState, writeLocalState } from '../platform/localdb/matrix.js'
import { decodeCursor } from '../platform/localdb/messages.js'

export interface WecomReadSessionArgs {
  session?: string
  attachment?: AttachmentRead | null
  /** Internal Runner resolution phase; not projected into the Agent's business schema. */
  resolve_only?: boolean
  channel?: 'local-db' | 'ui'
  limit?: number
  cursor?: string
  target_ref?: string
  /** 按会话名定位，与 target_ref 二选一；本地库精确消歧。 */
  target_name?: string
  /** 旧 UI 参数，现已停用。 */
  max_pages?: number
  /** 最近 N 天；本地库按 send_time 过滤。 */
  since_days?: number
  /** 日期模式：按业务日读取该会话（YYYY-MM-DD）；与 attachment/resolve_only 分开使用。 */
  date?: string
  /** 日期模式时区（IANA），缺省 Asia/Shanghai。 */
  timezone?: string
  /** 日期模式背景：true 带该日零点前最近 100 条，与当日消息共同分页；首次缺省 false。 */
  include_context?: boolean
}

export function createWecomReadSessionOperation(
  deps: {
    localdb?: LocaldbReadDeps
    readDbFn?: typeof readLocaldbSession
    verifyRefFn?: VerifyTargetRefFn
  } = {},
): WecomOperation<WecomReadSessionArgs> {
  const verifyRefFn = deps.verifyRefFn ?? verifyTargetRef
  return {
    name: 'wecom_read_session',
    execute(args: WecomReadSessionArgs, ctx: OpContext): Promise<OperationResult> {
      if (args && args.session !== undefined) {
        // 日期模式（设计 §4.1，评审 P1-2）：显式 date 或 wd1 日期游标都进入日期
        // 分支——标准续页只传 cursor，不能落回普通增量分支。
        const isDayMode = args.date !== undefined || (typeof args.cursor === 'string' && args.cursor.startsWith('wd1.'))
        if (isDayMode) {
          const requestDate = args.date
          const requestSession = args.session
          const dayInput = { session: requestSession, date: requestDate, timezone: args.timezone,
            include_context: args.include_context, limit: args.limit, cursor: args.cursor }
          return runWecomOperation('readonly', ctx, () => {
            if (args.target_name !== undefined || args.target_ref !== undefined) return 'session与旧目标参数不可同时传入'
            if (args.resolve_only === true) return '日期模式不用于纯定位'
            if (args.attachment != null) return '日期模式与附件读取分开使用'
            if (args.channel !== undefined || args.max_pages !== undefined || args.since_days !== undefined) return '日期模式接受session、date、timezone、include_context、cursor或limit'
            return validateSessionRead({ sessions: [requestSession], single: true, date: requestDate,
              timezone: args.timezone, include_context: args.include_context, limit: args.limit, cursor: args.cursor })
          }, opCtx => readSessionDay(dayInput, opCtx, deps.localdb ?? {}))
        }
        const input = { sessions: [args.session], cursor: args.cursor, limit: args.limit, single: true, metadataOnly: args.resolve_only === true, attachment: args.attachment ?? undefined }
        return runWecomOperation('readonly', ctx, () => {
          if (args.target_name !== undefined || args.target_ref !== undefined) return 'session与旧目标参数不可同时传入'
          if (args.resolve_only !== undefined && typeof args.resolve_only !== 'boolean') return 'resolve_only须为布尔值'
          if (args.resolve_only === true && args.cursor !== undefined) return '纯定位不接受cursor，也不推进消息进度'
          if (args.timezone !== undefined || args.include_context !== undefined) return 'timezone/include_context仅在date日期模式可用'
          if (args.channel !== undefined || args.max_pages !== undefined || args.since_days !== undefined) return 'session模式使用DB读取，接受session、cursor、limit或按需attachment'
          return validateSessionRead(input)
        }, opCtx => readBusinessSessions(input, opCtx, deps.localdb ?? {}))
      }
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（target_ref/target_name 二选一；max_pages/since_days 可选）'
          }
          if (args.resolve_only !== undefined) return 'resolve_only仅用于session纯定位'
          if (args.attachment != null) return '附件读取须指定session'
          if (args.date !== undefined || args.timezone !== undefined || args.include_context !== undefined) return 'date/timezone/include_context 仅用于session日期模式，与旧目标参数不可同时使用'
          if (args.channel !== undefined && args.channel !== 'local-db' && args.channel !== 'ui') return 'channel 必须为 local-db/ui'
          if (args.limit !== undefined && (!Number.isSafeInteger(args.limit) || args.limit < 1)) return 'limit 必须为正安全整数'
          if (args.cursor !== undefined && (typeof args.cursor !== 'string' || !args.cursor)) return 'cursor 必须为非空字符串'
          const selectorErr = validateTargetSelector(args)
          if (selectorErr !== null) return selectorErr
          if (args.max_pages !== undefined) return 'max_pages 仅用于已停用的 UI 读取，请使用 limit 控制本地库条数'
          if (
            args.since_days !== undefined &&
            (!Number.isInteger(args.since_days) || args.since_days < 1)
          ) {
            return 'since_days 必须是 ≥1 的整数'
          }
          return null
        },
        async (opCtx) => {
          const local = deps.localdb ?? {}
          const getState = local.readStateFn ?? (() => readLocalState(local.stateDir))
          const state = getState()
          const target: TargetRefIdentity | null = args.target_ref ? verifyRefFn(args.target_ref) : null
          if (args.cursor !== undefined && !decodeCursor(args.cursor).ok) {
            throw new CodedOperationError('CURSOR_UNSUPPORTED', '游标格式或版本不支持，请重新建立读取基线')
          }
          let current: CurrentAccount | null = null
          const getAccount = async (): Promise<CurrentAccount> => {
            try {
              const account = await (local.probeAccountFn ?? probeCurrentAccount)(opCtx)
              checkCancelled(opCtx.signal)
              return account
            } catch (err) {
              // 已观察到登录/身份变化，旧扫描失败上下文不再有效；不改原模式。
              if (state?.scan_failure && err instanceof CodedOperationError &&
                  ['NOT_LOGGED_IN', 'WECOM_NOT_FOUND', 'IDENTITY_UNCONFIRMED'].includes(err.code)) {
                const latest = getState()
                if (latest) (local.writeStateFn ?? (value => writeLocalState(value, local.stateDir)))({ ...latest, scan_failure: undefined })
              }
              throw err
            }
          }
          if (target?.account !== undefined) {
            current = await getAccount()
            if (target.account !== current.account) throw new CodedOperationError('TARGET_REF_STALE', '目标句柄属于另一个账号，请重新搜索')
          }
          if (args.channel === 'ui') throw new CodedOperationError('VERSION_UNSUPPORTED', '读取会话仅支持本地库通道')
          current ??= await getAccount()
          if (target && target.account === undefined) {
            throw new CodedOperationError('TARGET_REF_STALE', '旧目标句柄未绑定账号，不能用于本地库读取；请重新搜索或按名称读取')
          }
          return (deps.readDbFn ?? readLocaldbSession)({
            name: target?.name ?? args.target_name!, ...(target ? { target } : {}),
            limit: args.limit, cursor: args.cursor, sinceDays: args.since_days, forced: true,
          }, opCtx, current, local)
        },
      )
    },
  }
}
