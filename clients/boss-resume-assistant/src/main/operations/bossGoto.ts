import { runVerifiedPageOperation } from './pageOperation.js'
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
        { kind: 'readonly', name: 'boss_goto' },
        ctx,
        sessionFactory,
        () => {
          if (args === null || typeof args !== 'object' || !VALID_TARGETS.has(args?.target)) {
            return 'target 必须是 recommend（推荐牛人）或 chat（沟通）'
          }
          return null
        },
        async (session) => {
          const verified = await runVerifiedPageOperation({ name: 'boss_goto', goal: '跳转目标页面', expected: '当前BOSS标签URL匹配目标页面', operationClass: 'page_state', target: args.target }, async (attempt) => {
            const navigator = new PageNavigator({
              snapshot: session.snapshot,
              click: (...args) => attempt.input(() => session.click(...args)),
              getUrl: session.getUrl,
              pageNavigate: session.pageNavigate ? (...args) => attempt.input(() => session.pageNavigate!(...args)) : undefined,
              onRetry: () => { attempt.execution.ordinaryRetriesUsed = 1; attempt.execution.attemptsUsed = 2 },
              signal: ctx.signal,
            })
            ctx.progress({ stage: 'navigate', message: `跳转「${args.target === 'recommend' ? '推荐牛人' : '沟通'}」` })
            const result = await navigator.navigate(args.target)
            attempt.verified({status: 'met', evidence: [`URL已确认目标页面${args.target}，路径${result.via}`]})
            // via=url-jump 表示菜单点击未生效、走了 URL 直跳兜底（2026-09-18），透出便于云端/用户排障
            const viaNote = result.via === 'url-jump' ? '（菜单点击未生效，URL 直跳兜底）' : ''
            return {
              message: result.clicked ? `已跳转${viaNote}` : '已在目标页面，无需跳转',
              data: { target: args.target, navigated: result.clicked, via: result.via },
            }
          })
          return { ...verified.value, data: { ...verified.value.data, operation_recovery: verified.facts } }
        },
      )
    },
  }
}
