import { isBossPage } from '../boss/PageNavigator.js'
import { runVerifiedPageOperation } from './pageOperation.js'
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
        { kind: 'write', name: 'boss_select_job' },
        ctx,
        sessionFactory,
        () => (jobName ? null : 'job_name（职位名）不能为空'),
        async (session, tracker, perf) => {
          const verified = await runVerifiedPageOperation({ name: 'boss_select_job', goal: '切换当前招聘职位', expected: '当前职位框精确等于目标且下拉已收起', operationClass: 'page_state', target: jobName }, async (attempt) => {
            if (!isBossPage(await session.getUrl(), 'recommend')) throw new CodedOperationError('WRONG_PAGE', '当前不在推荐牛人页面，请先用 boss_goto recommend')
            // Phase 5 快照热点分段（TS/CDP 侧）：选职位与列职位同源（JobSwitcher），快照按用途计段
            const probe = await perf.time('snapshot:precheck', () => session.snapshot())
            if (!probe.strings.some((s) => FILTER_BUTTON_PATTERN.test(s.trim()))) {
              throw new CodedOperationError(
                'WRONG_PAGE',
                '当前页面不是推荐牛人列表页（未找到「筛选」按钮），请先用 boss_goto 切换到「推荐牛人」页',
              )
            }
            const switcher = new JobSwitcher({
              snapshot: (opts?: import('../cdp/CdpGateway.js').DomSnapshotOptions) => perf.time('snapshot:selectJob', () => session.snapshot(opts)),
              click: (...args) => attempt.input(() => session.click(...args)),
              signal: ctx.signal,
            })
            ctx.progress({ stage: 'execute', message: `切换到职位「${jobName}」` })
            await switcher.selectJob({ jobName })
            attempt.verified({status: 'met', evidence: [`当前职位框已验证为${jobName}且下拉收起`]})
            tracker.completed = 1
            return {
              message: `完成：已切换到职位「${jobName}」`,
              data: { job_name: jobName, switched: true },
              effect: 'applied' as const,
            }
          }, ctx.previousPageExecution)
          return { ...verified.value, data: { ...verified.value.data, operation_recovery: verified.facts } }
        },
      )
    },
  }
}
