/**
 * boss_filter_options operation（设计 §10.2 补充，2026-08-17 用户定调「档位判断交 AI」）：
 * 只读探查推荐牛人页筛选面板的全部可选档位（经验/学历/薪资各行选项），供调用方（LLM）
 * 把用户口语化要求（如 15k-20k / 5年以上 / 本科及以上）映射成最接近的精确档位后再调
 * boss_filter——CLI 内部不做模糊匹配（与 list-jobs → select-job 同一模式）。
 *
 * 链路：ensurePanelOpen → describePanel（两道消歧）→ 点「筛选」收起面板还原页面。
 * 前置：必须在推荐牛人列表页（有「筛选」按钮）→ 否则 WRONG_PAGE。
 * 只读（effect=none）；开/收面板走 Win32 点击，借用真实鼠标约 2 秒。
 */
import { FilterSetter, FILTER_BUTTON_PATTERN } from '../boss/FilterSetter.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export function createBossFilterOptionsOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<Record<string, never>> {
  return {
    name: 'boss_filter_options',
    execute(_args: Record<string, never>, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation(
        'readonly',
        ctx,
        sessionFactory,
        () => null,
        async (session) => {
          // 前置校验：必须在推荐牛人列表页（有「筛选」按钮，与 bossGreet 同源判定）
          const probe = await session.snapshot()
          if (!probe.strings.some((s) => FILTER_BUTTON_PATTERN.test(s.trim()))) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前页面不是推荐牛人列表页（未找到「筛选」按钮），请先用 boss_goto 切换到「推荐牛人」页',
            )
          }
          const setter = new FilterSetter({
            snapshot: session.snapshot,
            click: session.click,
            signal: ctx.signal,
          })
          ctx.progress({ stage: 'execute', message: '打开筛选面板读取可选档位（借用真实鼠标，请勿移动）' })
          const rows = await setter.probeOptions()
          const lines = rows.map((r) => `${r.label}：${r.options.map((o) => o.text).join('、')}`)
          return {
            message: `完成：读取到 ${rows.length} 行筛选条件可选档位`,
            data: {
              rows: rows.map((r) => ({
                label: r.label,
                options: r.options.map((o) => o.text),
              })),
              hint: '把用户的筛选要求映射到以上档位后，用精确档位值调用 boss_filter（如用户要 15-20K 而只有 15-25K，选 15-25K 并向用户说明）',
              readable_lines: lines,
            },
            effect: 'none',
          }
        },
      )
    },
  }
}
