/** 新消息跟踪单轮：只使用已验证客户端版本的本地库，失败保留水位。 */
import { runWecomOperation } from './context.js'
import { CancelledError, CodedOperationError, type OperationResult, type OpContext, type WecomOperation } from './types.js'
import { validateFocus } from './unreadList.js'
import { probeCurrentAccount, checkCancelled, type CurrentAccount } from '../platform/localdb/identity.js'
import { runLocaldbWatch, type LocaldbWatchDeps } from '../platform/localdb/watch.js'
import { logLocaldbEvent } from '../platform/localdb/log.js'

export interface WecomWatchPollArgs {
  /** 仅 local-db 可用；保留 ui 字面值以给旧调用明确错误。 */
  channel?: 'local-db' | 'ui'
  /** 关注会话名列表：未传=全部；空数组=INVALID_ARGUMENT；本地库逐名解析，失败进 failed。 */
  focus?: string[]
}

/** 本地库通道单轮事件（冻结点 4 形态；runLocaldbWatch data 的消费面） */
export interface LocaldbWatchEvent {
  channel: 'local-db'
  new_messages: Array<{
    conversation_id: string
    /** 显式 focus 绑定的会话附绑定时显示名（改名不影响跟踪） */
    display_name?: string
    messages: unknown[]
    has_more?: boolean
  }>
  last_sender: Record<string, 'self' | 'peer' | null>
  failed?: Array<{ focus: string; reason: string }>
}

export function createWecomWatchPollOperation(
  deps: {
    /** 本地库依赖（LocaldbReadDeps 超集，测试注入 mock） */
    localdb?: LocaldbWatchDeps
    /** 本地库执行器（默认 runLocaldbWatch；测试注入 mock） */
    watchDbFn?: typeof runLocaldbWatch
    probeAccountFn?: (ctx: OpContext) => Promise<CurrentAccount>
  } = {},
): WecomOperation<WecomWatchPollArgs> {
  const local = deps.localdb ?? {}
  return {
    name: 'wecom_watch_poll',
    execute(args: WecomWatchPollArgs, ctx: OpContext): Promise<OperationResult> {
      return runWecomOperation(
        'readonly',
        ctx,
        () => {
          if (args === null || typeof args !== 'object' || Array.isArray(args)) {
            return '参数必须是对象（可选字段 channel: local-db/ui；focus: string[]）'
          }
          if (args.channel !== undefined && args.channel !== 'local-db' && args.channel !== 'ui') {
            return 'channel 必须为 local-db/ui'
          }
          return validateFocus(args.focus)
        },
        async (opCtx) => {
          if (args.channel === 'ui') throw new CodedOperationError('VERSION_UNSUPPORTED', '监控读取仅支持本地库通道')
          const getAccount = async (): Promise<CurrentAccount> => {
            const account = await (deps.probeAccountFn ?? local.probeAccountFn ?? probeCurrentAccount)(opCtx)
            checkCancelled(opCtx.signal)
            return account
          }
          const current = await getAccount()
          try {
            return await (deps.watchDbFn ?? runLocaldbWatch)(
              {
                ...(args.focus !== undefined ? { focus: args.focus } : {}),
                forced: true,
              },
              opCtx,
              current,
              local,
            )
          } catch (err) {
            // 取消/身份失败照常抛（IDENTITY_UNCONFIRMED 不吞，§2 分流表）；其余 DB 不可用
            // 一律结构化失败（水位保留、日志留痕），**绝不自动落 UI**——含强制 local-db
            if (err instanceof CancelledError || (err instanceof CodedOperationError && err.code === 'CANCELLED')) throw err
            checkCancelled(opCtx.signal)
            const code = err instanceof CodedOperationError ? err.code : 'LOCALDB_UNAVAILABLE'
            ;(local.logFn ?? logLocaldbEvent)({ type: 'db_fallback', stage: 'watch', reason: code })
            throw new CodedOperationError(code,
              `本地库 watch 轮失败（水位已保留，本轮不落 UI）：${err instanceof Error ? err.message : String(err)}`,
              'none',
              { channel: 'local-db', db_unavailable: true, ...(err instanceof CodedOperationError ? err.data : {}) })
          }
        },
      )
    },
  }
}
