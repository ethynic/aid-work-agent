/** 未读/待回复快照：只使用已验证客户端版本的本地库。 */
import { runWecomOperation } from './context.js'
import { CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { probeCurrentAccount, checkCancelled, type CurrentAccount } from '../platform/localdb/identity.js'
import { readLocaldbUnread } from '../platform/localdb/unread.js'
import { type LocaldbReadDeps } from '../platform/localdb/readSession.js'
import { readLocalState, writeLocalState } from '../platform/localdb/matrix.js'

export interface WecomUnreadListArgs {
  name?: string
  /** 仅 local-db 可用；保留 ui 字面值以给旧调用明确错误。 */
  channel?: 'local-db' | 'ui'
  /** 关注会话名列表（冻结点 4）：未传=全部；空数组=INVALID_ARGUMENT；本地库逐名解析，失败进 failed */
  focus?: string[]
}

/** focus 入参校验（冻结点 4：字符串数组；空数组 = INVALID_ARGUMENT；项须非空字符串） */
export function validateFocus(focus: unknown): string | null {
  if (focus === undefined) return null
  if (!Array.isArray(focus)) return 'focus 必须是字符串数组'
  if (focus.length === 0) return 'focus 不能为空数组（未传 = 全部会话；如无关注目标请不要传 focus）'
  for (const item of focus) {
    if (typeof item !== 'string' || item.trim().length === 0) return 'focus 每项必须是非空字符串（会话名）'
  }
  return null
}

export function createWecomUnreadListOperation(
  deps: {
    localdb?: LocaldbReadDeps
    /** 本地库执行器（默认 readLocaldbUnread；测试注入 mock） */
    readDbFn?: typeof readLocaldbUnread
    probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
  } = {},
): WecomOperation<WecomUnreadListArgs> {
  const local = deps.localdb ?? {}
  const getState = local.readStateFn ?? (() => readLocalState(local.stateDir))
  return {
    name: 'wecom_unread_list',
    execute(args: WecomUnreadListArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（当前仅支持可选字段 name: string；channel: local-db/ui；focus: string[]）'
          }
          if (args.name !== undefined && typeof args.name !== 'string') {
            return 'name 必须是字符串'
          }
          if (args.channel !== undefined && args.channel !== 'local-db' && args.channel !== 'ui') {
            return 'channel 必须为 local-db/ui'
          }
          return validateFocus(args.focus)
        },
        async (opCtx) => {
          if (args.channel === 'ui') throw new CodedOperationError('VERSION_UNSUPPORTED', '读取未读会话仅支持本地库通道')
          const state = getState()
          const getAccount = async (): Promise<CurrentAccount> => {
            try {
              const account = await (deps.probeAccountFn ?? local.probeAccountFn ?? probeCurrentAccount)(opCtx)
              checkCancelled(opCtx.signal)
              return account
            } catch (err) {
              // 已观察到登录/身份变化，旧扫描失败上下文不再有效；不改原模式（同 readSession）。
              if (state?.scan_failure && err instanceof CodedOperationError &&
                  ['NOT_LOGGED_IN', 'WECOM_NOT_FOUND', 'IDENTITY_UNCONFIRMED'].includes(err.code)) {
                const latest = getState()
                if (latest) (local.writeStateFn ?? (value => writeLocalState(value, local.stateDir)))({ ...latest, scan_failure: undefined })
              }
              throw err
            }
          }
          const current = await getAccount()
          return (deps.readDbFn ?? readLocaldbUnread)(
            {
              ...(args.name !== undefined ? { name: args.name } : {}),
              ...(args.focus !== undefined ? { focus: args.focus } : {}),
              forced: true,
            },
            opCtx,
            current,
            local,
          )
        },
      )
    },
  }
}
