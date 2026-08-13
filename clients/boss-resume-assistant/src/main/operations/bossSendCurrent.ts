/**
 * boss_send_current operation（设计 §10.6）：沟通页向当前已选会话发送消息（写动作）。
 *
 * 前提：已在沟通页选中一个会话（右侧面板有发送按钮）。否则 WRONG_PAGE（提示先选会话）。
 * 流程：输入并发送消息（ChatSendExecutor.sendMessage）。默认真发送；dry_run 只输入不发送。
 * 前置校验：message 非空（在 connect Chrome 前完成）。
 */
import { ChatSendExecutor, locateSendButton } from '../boss/ChatSendExecutor.js'
import {
  defaultSessionFactory,
  ensureChatPage,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, OperationResult, OpContext } from './types.js'

export interface BossSendCurrentArgs {
  /** 发送的消息内容 */
  message: string
  /** 只输入不点发送（测试链路），默认 false 真发送 */
  dry_run?: boolean
}

export function createBossSendCurrentOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossSendCurrentArgs> {
  return {
    name: 'boss_send_current',
    execute(args: BossSendCurrentArgs, ctx: OpContext): Promise<OperationResult> {
      const message = args?.message ?? ''
      const dryRun = args?.dry_run === true
      return runBossOperation(
        'write',
        ctx,
        sessionFactory,
        () => {
          if (!message.trim()) return 'message（消息内容）不能为空'
          return null
        },
        async (session, tracker) => {
          await ensureChatPage(session, ctx)
          // 前置：必须已选中会话（有发送按钮），否则提示先选会话
          const probe = await session.snapshot()
          const { count } = locateSendButton(probe)
          if (count === 0) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前沟通页未选中会话（未找到发送按钮）：请先在左侧会话列表选择一个会话后再发送',
            )
          }
          const sender = new ChatSendExecutor({
            snapshot: session.snapshot,
            click: session.click,
            typeChar: session.typeChar,
            signal: ctx.signal,
          })
          ctx.progress({
            stage: 'execute',
            message: dryRun ? '输入消息（dry-run 不发送）' : '输入并发送消息',
          })
          const r = await sender.sendMessage({ message, dryRun })
          tracker.completed = r.sent ? 1 : 0
          return {
            message: dryRun ? '完成：已向当前会话输入消息（dry-run 未发送）' : '完成：已向当前会话发送消息',
            data: { sent: r.sent, dry_run: dryRun },
            effect: r.sent ? ('applied' as const) : ('none' as const),
          }
        },
      )
    },
  }
}
