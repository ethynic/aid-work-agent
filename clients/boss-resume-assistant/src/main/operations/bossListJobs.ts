/**
 * boss_list_jobs operation（设计 §10.7）：推荐牛人页打开职位下拉，解析当前招聘者的全部职位（只读）。
 *
 * 前置校验：必须在推荐牛人列表页（有「筛选」按钮）→ 否则 WRONG_PAGE。
 * 借用真实鼠标点开下拉（写动作通道），但不改变任何职位状态（只读 effect=none）。
 * 用于在 select-job 前确认精确职位名（用户口述的职位名可能不精确）。
 */
import { JobSwitcher, FILTER_BUTTON_PATTERN } from '../boss/JobSwitcher.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export function createBossListJobsOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<Record<string, never>> {
  return {
    name: 'boss_list_jobs',
    execute(_args: Record<string, never>, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => null,
        async (session) => {
          // 前置校验：必须在推荐牛人列表页（有「筛选」按钮）
          const probe = await session.snapshot()
          if (!probe.strings.some((s) => FILTER_BUTTON_PATTERN.test(s.trim()))) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前页面不是推荐牛人列表页（未找到「筛选」按钮），请先用 boss_goto 切换到「推荐牛人」页',
            )
          }
          const switcher = new JobSwitcher({ snapshot: session.snapshot, click: session.click, signal: ctx.signal })
          ctx.progress({ stage: 'execute', message: '打开职位下拉并解析职位列表' })
          const jobs = await switcher.listJobs()
          const lines = jobs
            .map((j, i) => `  ${i + 1}. ${j.name}（${j.city} ${j.salary}${j.pending ? '，待开放' : ''}）`)
            .join('\n')
          const openCount = jobs.filter((j) => !j.pending).length
          return {
            message: `完成：当前招聘者共 ${jobs.length} 个职位（已开放 ${openCount}）\n${lines}`,
            data: {
              jobs: jobs.map((j) => ({
                name: j.name,
                city: j.city,
                salary: j.salary,
                pending: j.pending,
                point: { x: j.point.x, y: j.point.y },
              })),
            },
            effect: 'none',
          }
        },
      )
    },
  }
}
