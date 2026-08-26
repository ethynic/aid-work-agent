/**
 * boss_interview_demo operation（设计 §10.2）：约面试表单填充演示（只填不发送）。
 *
 * 沟通页当前会话 → 点「约面试」→ 备注逐字输入 → 面试时间选明天 → 点「取消」关闭。
 * ⚠️ 演示用途：绝不点击「发送」，无外部写副作用（effect=none，sent 恒 false）。
 */
import { InterviewDemoExecutor } from '../boss/InterviewDemoExecutor.js'
import {
  defaultSessionFactory,
  ensureChatPage,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossInterviewDemoArgs {
  /** 备注内容（缺省用默认文案）；表单上限 140 字 */
  remark?: string
}

export function createBossInterviewDemoOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossInterviewDemoArgs> {
  return {
    name: 'boss_interview_demo',
    execute(args: BossInterviewDemoArgs, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => {
          if (args?.remark !== undefined) {
            if (typeof args.remark !== 'string' || !args.remark.trim()) return 'remark 必须是非空字符串'
            if (args.remark.length > 140) return `remark 不能超过 140 字（表单上限）：当前 ${args.remark.length} 字`
          }
          return null
        },
        async (session) => {
          await ensureChatPage(session, ctx)
          const executor = new InterviewDemoExecutor({
            snapshot: session.snapshot,
            click: session.click,
            clickAndType: session.clickAndType,
            signal: ctx.signal,
          })
          ctx.progress({ stage: 'execute', message: '填备注 + 选明天日期（只填不发送）' })
          const result = await executor.run({ remark: args?.remark })
          return {
            message: `完成：备注「${result.remark}」+ 日期 ${result.date} 已填入并取消关闭（未发送）`,
            data: { remark: result.remark, date: result.date, sent: false },
          }
        },
      )
    },
  }
}
