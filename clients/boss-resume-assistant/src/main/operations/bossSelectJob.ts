/**
 * boss_select_job operation（设计 §10.7）：推荐牛人页切换当前招聘职位到指定职位名（写动作）。
 *
 * 前置校验：必须在推荐牛人列表页（有「筛选」按钮）→ 否则 WRONG_PAGE。
 * job_name 精确匹配（CLI 内部不做模糊匹配，匹配 0/多个都 fail-loud）；匹配判断交给调用方 AI。
 * 切换后校验职位框文本已变更，未生效报错（EXECUTION_UNKNOWN：写动作已发出但结果未确认）。
 */
import { JobSwitcher, FILTER_BUTTON_PATTERN } from '../boss/JobSwitcher.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossSelectJobArgs {
  /** 目标职位名（精确，用 list-jobs 查看，如 "PHP开发工程师"） */
  job_name: string
}

export function createBossSelectJobOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossSelectJobArgs> {
  return {
    name: 'boss_select_job',
    execute(args: BossSelectJobArgs, ctx: OpContext): Promise<OperationResult> {
      const jobName = (args?.job_name ?? '').trim()
      return runBossOperation(
        'write',
        ctx,
        sessionFactory,
        () => (jobName ? null : 'job_name（职位名）不能为空'),
        async (session, tracker) => {
          const probe = await session.snapshot()
          if (!probe.strings.some((s) => FILTER_BUTTON_PATTERN.test(s.trim()))) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前页面不是推荐牛人列表页（未找到「筛选」按钮），请先用 boss_goto 切换到「推荐牛人」页',
            )
          }
          const switcher = new JobSwitcher({ snapshot: session.snapshot, click: session.click, signal: ctx.signal })
          ctx.progress({ stage: 'execute', message: `切换到职位「${jobName}」` })
          await switcher.selectJob({ jobName })
          tracker.completed = 1
          return {
            message: `完成：已切换到职位「${jobName}」`,
            data: { job_name: jobName, switched: true },
            effect: 'applied',
          }
        },
      )
    },
  }
}
