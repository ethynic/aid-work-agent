/**
 * boss_greet operation（设计 §10.2）：推荐牛人页逐个点击「打招呼」（写动作）。
 *
 * 前置校验：必须在推荐牛人列表页（否则 0 按钮会被误报成「已滚到底」）→ WRONG_PAGE。
 * 只点当前视口内可见按钮，点完自动滚动加载；每点一个校验按钮数减少，否则 fail-loud。
 * operation 层 limit 上限 100；MCP schema 用 maximum=3 收紧（设计 §14）。
 */
import { GreetExecutor } from '../boss/GreetExecutor.js'
import { viewportOf } from '../boss/FilterSetter.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossGreetArgs {
  /** 上限，operation 默认 10，最大 100 */
  limit?: number
}

/** 推荐牛人列表页特征：「筛选」/「筛选·N」按钮存在 */
const FILTER_BUTTON_PATTERN = /^筛选(·\d+)?$/

export function createBossGreetOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossGreetArgs> {
  return {
    name: 'boss_greet',
    execute(args: BossGreetArgs, ctx: OpContext): Promise<OperationResult> {
      const limit = args?.limit ?? 10
      return runBossOperation(
        'write',
        ctx,
        sessionFactory,
        () => {
          if (!Number.isInteger(limit) || limit <= 0 || limit > 100) {
            return `limit 必须是 1-100 的整数：${String(args?.limit)}`
          }
          return null
        },
        async (session, tracker) => {
          // 前置校验：必须在推荐牛人列表页
          const probe = await session.snapshot()
          if (!probe.strings.some((s) => FILTER_BUTTON_PATTERN.test(s.trim()))) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前页面不是推荐牛人列表页（未找到「筛选」按钮），请先用 boss_goto 切换到「推荐牛人」页',
            )
          }
          // 滚动点取实际视口中心（不能硬编码：窗口矮时落点出视口，滚轮不生效会误判到底）
          const vp = viewportOf(probe)
          const scrollPoint = { x: Math.floor(vp.width / 2), y: Math.floor(vp.height / 2) }
          const executor = new GreetExecutor({
            snapshot: session.snapshot,
            click: session.click,
            scroll: (deltaY) => session.mouseWheel(scrollPoint.x, scrollPoint.y, deltaY),
            signal: ctx.signal,
            onProgress: (done) => {
              tracker.completed = done
              ctx.progress({ stage: 'execute', current: done, total: limit, message: `已打招呼 ${done}/${limit} 人` })
            },
          })
          const result = await executor.greetVisible({ limit })
          return {
            message: `完成：成功打招呼 ${result.greeted} 人（${result.reachedEnd ? '已滚到底' : '达到上限'}）`,
            data: { greeted: result.greeted, reached_end: result.reachedEnd },
          }
        },
      )
    },
  }
}
