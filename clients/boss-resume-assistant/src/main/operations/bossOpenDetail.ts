/**
 * boss_open_detail operation：推荐牛人页按姓名找卡片 → Win32 点姓名节点打开简历详情 →
 * 校验详情画布 + 顶部区姓名==name（防打错人）。详情保持打开（供 boss_resume_detail 读取）。
 *
 * 筛选主流程第一步：boss_open_detail → boss_resume_detail → 合格则 boss_greet_detail /
 * 不合格则 boss_close_detail（docs/plans/desktop-automation/plan-boss-detail-greet.md）。
 *
 * 前置校验：必须在推荐牛人列表页（URL 判定，与 bossGreet 同源防坑 17：沟通页 DOM 内嵌
 * 推荐 iframe 时文案判定会误通过）。当前屏没有目标时滚动加载继续找（GreetExecutor 定向
 * 模式同源：30 屏上限 + 进度事件）；找不到 fail-loud 列出当前屏可见姓名。
 *
 * kind=readonly（与 bossResumeBatch 同款先例：点卡片/滚动借真实鼠标、改变页面显示状态，
 * 但无外部写副作用），effect=none；执行期间用户勿动鼠标（Win32 真实点击 + 滚动）。
 */
import { DetailGreetExecutor } from '../boss/DetailGreetExecutor.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossOpenDetailArgs {
  /** 候选人姓名（必传：与推荐列表卡片姓名 trim 精确相等） */
  name?: string
}

/** 推荐牛人列表页 URL 特征（与 bossGreet 同源：goto 已实证两页 URL 可区分） */
const RECOMMEND_URL_PART = '/web/chat/recommend'

export function createBossOpenDetailOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossOpenDetailArgs> {
  return {
    name: 'boss_open_detail',
    execute(args: BossOpenDetailArgs, ctx: OpContext): Promise<OperationResult> {
      const name = (args?.name ?? '').trim()
      return runBossOperation(
        { kind: 'readonly', name: 'boss_open_detail' },
        ctx,
        sessionFactory,
        // 参数前置校验（connect Chrome 之前 fail-fast）：姓名必传，绝不猜人开详情
        () =>
          name
            ? null
            : '未传 name：请显式传候选人姓名（boss_open_detail --name <姓名> / open-detail <姓名>）后重试',
        async (session, _tracker, perf) => {
          // 前置校验：必须在推荐牛人列表页（URL 判定，坑 17），否则 WRONG_PAGE
          const url = await session.getUrl()
          if (!url.includes(RECOMMEND_URL_PART)) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              `当前页面不是推荐牛人列表页（当前 URL: ${url || '未识别'}），请先用 goto recommend（或 boss_goto）切换到「推荐牛人」页再打开候选人详情`,
            )
          }
          ctx.progress({
            stage: 'execute',
            message: `在推荐牛人列表中查找「${name}」并打开简历详情（点击/滚动借用真实鼠标，请勿移动）`,
          })
          const executor = new DetailGreetExecutor({
            snapshot: session.snapshot,
            click: session.click,
            pressEscape: session.pressEscape,
            scroll: (x, y, deltaY) => session.mouseWheel(x, y, deltaY),
            signal: ctx.signal,
            // 性能埋点：固定 sleep 与关键阶段耗时进 collector（只记名称与毫秒）
            sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
            step: <T>(stepName: string, fn: () => Promise<T>) => perf.time(`detail:${stepName}`, fn),
          })
          const result = await executor.openDetail({
            name,
            // 滚动查找进度（2026-09-01 真机事故教训：定向找人滚动期间必须有进度事件，防用户恐慌关窗）
            onSearchProgress: (screens) => {
              ctx.progress({
                stage: 'execute',
                message: `正在列表中查找「${name}」…已滚动 ${screens} 屏（借用真实鼠标滚动，请勿移动）`,
              })
            },
          })
          return {
            message: `已打开「${result.name}」的简历详情（保持打开，供 boss_resume_detail 读取）`,
            data: { opened: true, name: result.name },
            effect: 'none',
          }
        },
      )
    },
  }
}
