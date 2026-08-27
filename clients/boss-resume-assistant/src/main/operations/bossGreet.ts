/**
 * boss_greet operation（设计 §10.2）：推荐牛人页逐个点击「打招呼」（写动作）。
 *
 * 前置校验：必须在推荐牛人列表页——URL 含 /web/chat/recommend 才继续（2026-08-26 修坑 17：
 * 曾用「存在『筛选』按钮文案」判定，沟通页 DOM 内嵌推荐 iframe 时文案同样命中、校验误通过，
 * 结果 0 人成功还报「完成」；URL 判定不受 iframe 影响）→ 否则 WRONG_PAGE。
 * 只点当前视口内可见按钮，点完自动滚动加载；每点一个校验按钮数减少，否则 fail-loud。
 * operation 层 limit 上限 100；MCP schema 用 maximum=3 收紧（设计 §14）。
 *
 * 定向模式（2026-08-18 修复，真机发现列表顺序与名单顺序不保证一致可能打错人）：
 * 传 names 姓名清单时先配对卡片姓名再点击，只打姓名精确匹配的人，配对失败的卡片一律跳过
 * （宁可不打，不能打错）；结果按实际打过的人返回（greeted_names/missing_names）。
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
  /** 上限，operation 默认 10，最大 100（非定向）。定向模式（传 names）自动钳到 ≥ names 数量，默认 limit=1 不会截断名单 */
  limit?: number
  /** 定向打招呼：候选人姓名清单（1-3 个非空字符串，去重；精确匹配卡片姓名，配对失败跳过） */
  names?: string[]
}

/** 推荐牛人列表页 URL 特征（goto 命令已实证可区分：推荐页含 /web/chat/recommend，沟通页是 /web/chat/index） */
const RECOMMEND_URL_PART = '/web/chat/recommend'
/** 定向名单上限（与 MCP schema 的 max=3 一致） */
const NAMES_MAX = 3

export function createBossGreetOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossGreetArgs> {
  return {
    name: 'boss_greet',
    execute(args: BossGreetArgs, ctx: OpContext): Promise<OperationResult> {
      const limit = args?.limit ?? 10
      const names = args?.names
      return runBossOperation(
        'write',
        ctx,
        sessionFactory,
        () => {
          if (!Number.isInteger(limit) || limit <= 0 || limit > 100) {
            return `limit 必须是 1-100 的整数：${String(args?.limit)}`
          }
          if (names !== undefined) {
            if (!Array.isArray(names) || names.length === 0) {
              return 'names 必须是至少含 1 个姓名的数组'
            }
            if (names.length > NAMES_MAX) {
              return `names 最多 ${NAMES_MAX} 个姓名（当前 ${names.length} 个）`
            }
            if (names.some((n) => typeof n !== 'string' || !n.trim())) {
              return 'names 中每个姓名都必须是非空字符串'
            }
          }
          return null
        },
        async (session, tracker) => {
          // 前置校验：必须在推荐牛人列表页（URL 判定，坑 17）。
          // 不用「筛选」按钮文案判定：沟通页 DOM 内嵌推荐 iframe 时文案同样命中、误通过，
          // 结果 0 人成功还报「完成」；URL 不受 iframe 影响（goto 已实证两页 URL 可区分）
          const url = await session.getUrl()
          if (!url.includes(RECOMMEND_URL_PART)) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              `当前页面不是推荐牛人列表页（当前 URL: ${url || '未识别'}），请先用 goto recommend（或 boss_goto）切换到「推荐牛人」页再打招呼`,
            )
          }
          const probe = await session.snapshot()
          // 滚动点取实际视口中心（不能硬编码：窗口矮时落点出视口，滚轮不生效会误判到底）
          const vp = viewportOf(probe)
          const scrollPoint = { x: Math.floor(vp.width / 2), y: Math.floor(vp.height / 2) }
          // 定向名单去重（trim 后精确匹配）
          const targetNames = names ? [...new Set(names.map((n) => n.trim()))] : undefined
          // 定向模式 limit 钳到 ≥ 名单人数：names 是意图（打全名单），limit 只是安全上限。
          // MCP schema 对省略的 limit 注入默认 1（zod .default(1)），SUBAGENT 链路只传 names
          // 不传 limit——不钳会把 2-3 人名单截断成只打 1 人、其余误报进 missing_names。
          // names ≤ 3 已校验，钳后不突破单次 3 人授权上限。
          const effectiveLimit = targetNames ? Math.max(limit, targetNames.length) : limit
          const total = targetNames ? targetNames.length : limit
          const executor = new GreetExecutor({
            snapshot: session.snapshot,
            click: session.click,
            scroll: (deltaY) => session.mouseWheel(scrollPoint.x, scrollPoint.y, deltaY),
            signal: ctx.signal,
            onProgress: (done) => {
              tracker.completed = done
              ctx.progress({ stage: 'execute', current: done, total, message: `已打招呼 ${done}/${total} 人` })
            },
          })
          const result = await executor.greetVisible({ limit: effectiveLimit, names: targetNames })
          if (targetNames) {
            const greeted = result.greetedNames ?? []
            const missing = result.missingNames ?? []
            const parts = [
              `完成：定向打招呼成功 ${greeted.length} 人${greeted.length > 0 ? `（${greeted.join('、')}）` : ''}`,
            ]
            if (missing.length > 0) {
              parts.push(`未找到 ${missing.length} 人（${missing.join('、')}）${result.reachedEnd ? '，已滚到底' : ''}`)
            }
            return {
              message: parts.join('；'),
              data: {
                greeted: result.greeted,
                reached_end: result.reachedEnd,
                greeted_names: greeted,
                missing_names: missing,
              },
            }
          }
          return {
            message: `完成：成功打招呼 ${result.greeted} 人（${result.reachedEnd ? '已滚到底' : '达到上限'}）`,
            data: { greeted: result.greeted, reached_end: result.reachedEnd },
          }
        },
      )
    },
  }
}
