import { runVerifiedPageOperation } from './pageOperation.js'
/**
 * boss_close_detail operation：关闭当前打开的简历详情弹层（自属×或Escape + 轮询画布消失）。
 *
 * 筛选主流程的清理步：boss_resume_detail 读取后不合格（不打招呼）时用本工具关掉详情；
 * 无业务副作用（kind=readonly / effect=none）。详情本来就没打开 → 幂等成功返回。
 * Escape 走 CDP 键盘事件，但执行期间仍提示用户勿动鼠标键盘。
 */
import { DetailGreetExecutor } from '../boss/DetailGreetExecutor.js'
import { FILTER_SNAPSHOT_OPTIONS } from '../boss/FilterSetter.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export function createBossCloseDetailOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<Record<string, never>> {
  return {
    name: 'boss_close_detail',
    execute(_args: Record<string, never>, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation(
        { kind: 'readonly', name: 'boss_close_detail' },
        ctx,
        sessionFactory,
        () => null,
        async (session, _tracker, perf) => {
          const verified = await runVerifiedPageOperation({ name: 'boss_close_detail', goal: '关闭当前简历详情', expected: '简历详情画布消失', operationClass: 'page_state' }, async (attempt) => {
            ctx.progress({ stage: 'execute', message: '关闭当前打开的简历详情弹层（若无详情则幂等成功）' })
            const executor = new DetailGreetExecutor({
              snapshot: () => session.snapshot(FILTER_SNAPSHOT_OPTIONS),
              click: (...args) => attempt.input(() => session.click(...args)),
              pressEscape: (...args) => attempt.input(() => session.pressEscape(...args)),
              signal: ctx.signal,
              sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
              step: <T>(stepName: string, fn: () => Promise<T>) => perf.time(`detail:${stepName}`, fn),
            })
            const outcome = await executor.close()
            attempt.verified({status: 'met', evidence: [outcome.wasOpen ? '关闭输入后轮询已确认简历画布消失' : 'fresh页面未发现简历画布，无需关闭']})
            return {
              message: outcome.wasOpen
                ? '已关闭当前打开的简历详情弹层（列表已恢复可操作）'
                : '当前没有打开的简历详情（无需关闭，幂等成功）',
              data: { was_open: outcome.wasOpen },
              effect: 'none' as const,
            }
          })
          return { ...verified.value, data: { ...verified.value.data, operation_recovery: verified.facts } }
        },
      )
    },
  }
}
