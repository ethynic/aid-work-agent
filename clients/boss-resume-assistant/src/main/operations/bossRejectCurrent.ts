/**
 * boss_reject_current operation（设计 §10.2）：沟通页把当前会话候选人标记为「不合适」（写动作）。
 *
 * 单发写动作：一次只标当前会话（每次固定 1，无 limit 参数）。弹确认层自动点「确定」；
 * 弹原因选择层则 fail-loud 请人工选择（绝不乱选原因）。不在沟通页时自动先跳转。
 */
import { ChatRejectExecutor } from '../boss/ChatRejectExecutor.js'
import {
  defaultSessionFactory,
  ensureChatPage,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'
import { BusinessOperationState, withBusinessRecovery } from '../boss/BusinessOperationState.js'

export function createBossRejectCurrentOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<Record<string, never>> {
  return {
    name: 'boss_reject_current',
    execute(_args: Record<string, never>, ctx: OpContext): Promise<OperationResult> {
      const state = new BusinessOperationState()
      const checkpoint: import('../boss/ChatRejectExecutor.js').RejectCheckpoint = {}
      const run = (ctx: OpContext): Promise<OperationResult> => runBossOperation({ kind: 'write', name: 'boss_reject_current' }, ctx, sessionFactory, () => null, async (session) => {
        return withBusinessRecovery(state, { name: 'boss_reject_current', goal: '把原当前会话候选人标记不合适',
          expected: '原目标会话从已加载的会话列表中消失或隐藏，其他会话保持可读', operationClass: 'external_effect' }, async () => {
        await ensureChatPage(session, ctx)
        const executor = new ChatRejectExecutor({
          state, checkpoint,
          snapshot: session.snapshot,
          click: session.click,
          signal: ctx.signal,
        })
        ctx.progress({ stage: 'execute', message: '标记当前会话为「不合适」' })
        await executor.rejectCurrent({ expectedContactRef: ctx.expectedContactRef })
        return {
          message: '完成：已把当前会话的候选人标记为不合适',
          data: { rejected: true },
          effect: 'applied' as const,
        }
        })
      })
      if (ctx.recoveryContinuation) ctx.recoveryContinuation.resume = run
      return run(ctx)
    },
  }
}
