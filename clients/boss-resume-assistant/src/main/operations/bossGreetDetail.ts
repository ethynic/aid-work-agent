/**
 * boss_greet_detail operation：在当前打开的简历详情页点「打招呼」（写动作）。
 *
 * 默认只评分入库后关闭详情等待人工审核；用户明确选择直接打招呼后显式启用本工具。
 *
 * 链路（DetailGreetExecutor.greet，真机探查结论见
 * docs/plans/desktop-automation/plan-boss-detail-greet.md 2026-09-28 两轮实证）：
 * 校验详情画布 + 顶部区姓名==name（防打错人）→ DOM 结构定位操作列（LCA(收藏,举报,不合适)
 * 容器内恰好 1 个「打招呼」；容器外的列表按钮一律视为诱饵）→ Win32 点击 → 轮询按钮翻转
 * 「继续沟通」→ Escape 关闭详情并确认。已是「继续沟通」→ 幂等返回（不点击，正常关闭）；
 * dry_run 只定位不点击（详情保持打开）。点击后未翻转 → fail-loud（unknown，不要重试）。
 */
import { DetailGreetExecutor } from '../boss/DetailGreetExecutor.js'
import { FILTER_SNAPSHOT_OPTIONS } from '../boss/FilterSetter.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossGreetDetailArgs {
  /** 候选人姓名（必传：校验当前打开的详情属于该候选人，防止打错人） */
  name?: string
  /** 只定位不点击（测试链路，默认 false 真实点击） */
  dry_run?: boolean
  /** 默认关闭；只在用户明确选择本轮直接打招呼后启用。 */
  greet_in_detail?: boolean
}

export function createBossGreetDetailOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossGreetDetailArgs> {
  return {
    name: 'boss_greet_detail',
    execute(args: BossGreetDetailArgs, ctx: OpContext): Promise<OperationResult> {
      const name = (args?.name ?? '').trim()
      const dryRun = args?.dry_run === true
      return runBossOperation(
        { kind: 'write', name: 'boss_greet_detail' },
        ctx,
        sessionFactory,
        // 参数前置校验（connect Chrome 之前 fail-fast）：姓名必传——防打错人是本工具的核心约束
        () => {
          if (!name) return '未传 name：请显式传候选人姓名后重试（详情归属校验依赖该姓名）'
          if (args?.greet_in_detail !== true) return '详情页打招呼默认关闭；仅用户明确选择合格后直接打招呼时传greet_in_detail=true（CLI: --greet-in-detail）'
          return null
        },
        async (session, tracker, perf) => {
          ctx.progress({
            stage: 'execute',
            message: dryRun
              ? `校验详情归属并定位「${name}」的「打招呼」按钮（dry-run，不点击）`
              : `校验详情归属并向「${name}」发送打招呼（真实写动作，借用真实鼠标，请勿移动）`,
          })
          const executor = new DetailGreetExecutor({
            snapshot: () => session.snapshot(FILTER_SNAPSHOT_OPTIONS),
            click: session.click,
            pressEscape: session.pressEscape,
            signal: ctx.signal,
            // 性能埋点：固定 sleep 与关键阶段耗时进 collector（只记名称与毫秒）
            sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
            step: <T>(stepName: string, fn: () => Promise<T>) => perf.time(`detail:${stepName}`, fn),
          })
          const outcome = await executor.greet({ name, dryRun })
          if (outcome.stopReason) return {
            message: '当前详情已证明属于筛选结果之外的推荐内容，已停止，未打招呼',
            data: { name, greeted: false, stop_reason: outcome.stopReason }, effect: 'none',
          }
          if (outcome.greeted) tracker.completed = 1
          const data: Record<string, unknown> = { name, greeted: outcome.greeted }
          if (outcome.already) data.already = true
          if (dryRun) data.dry_run = true
          let message: string
          if (outcome.greeted) {
            message = `完成：已在简历详情页向「${name}」发送打招呼（按钮已翻转为「继续沟通」），详情已关闭`
          } else if (outcome.already) {
            message = `「${name}」此前已打过招呼（详情按钮已显示「继续沟通」），本次未重复点击，详情已关闭`
          } else {
            message = `dry-run 定位成功：「${name}」的详情页「打招呼」按钮已定位且未点击（详情保持打开）；去掉 dry_run 后执行将真实点击`
          }
          // effect 缺省由 runBossOperation 按 kind+completed 计算：真打成功=applied，幂等/dry-run=none
          return { message, data }
        },
      )
    },
  }
}
