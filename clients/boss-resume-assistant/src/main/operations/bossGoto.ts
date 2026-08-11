/**
 * boss_goto operation（设计 §10.2）：点击左侧导航菜单跳转页面（推荐牛人 / 沟通）。
 *
 * 已在目标页时跳过（幂等）。导航类操作，无外部写副作用（effect=none）。
 */
import { PageNavigator, type NavTarget } from '../boss/PageNavigator.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossGotoArgs {
  /** recommend=推荐牛人，chat=沟通 */
  target: NavTarget
}

const VALID_TARGETS: ReadonlySet<string> = new Set(['recommend', 'chat'])

export function createBossGotoOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossGotoArgs> {
  return {
    name: 'boss_goto',
    execute(args: BossGotoArgs, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => {
          if (args === null || typeof args !== 'object' || !VALID_TARGETS.has(args?.target)) {
            return 'target 必须是 recommend（推荐牛人）或 chat（沟通）'
          }
          return null
        },
        async (session) => {
          const navigator = new PageNavigator({
            snapshot: session.snapshot,
            click: session.click,
            getUrl: session.getUrl,
            signal: ctx.signal,
          })
          ctx.progress({ stage: 'navigate', message: `跳转「${args.target === 'recommend' ? '推荐牛人' : '沟通'}」` })
          const result = await navigator.navigate(args.target)
          return {
            message: result.clicked ? '已跳转' : '已在目标页面，无需跳转',
            data: { target: args.target, navigated: result.clicked },
          }
        },
      )
    },
  }
}
