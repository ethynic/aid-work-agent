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
import { FilterSetter, FILTER_BUTTON_PATTERN, FILTER_SNAPSHOT_OPTIONS } from '../boss/FilterSetter.js'
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
        { kind: 'readonly', name: 'boss_filter_options' },
        ctx,
        sessionFactory,
        () => null,
        async (session, _tracker, perf) => {
          const currentUrl = await session.getUrl()
          let onRecommend = false
          try {
            const page = new URL(currentUrl)
            onRecommend = page.protocol === 'https:' && ['www.zhipin.com', 'zhipin.com'].includes(page.hostname)
              && page.pathname === '/web/chat/recommend'
          } catch { /* 非法URL按错误页面拒绝，不能根据后台iframe文案放行。 */ }
          if (!onRecommend) throw new CodedOperationError('WRONG_PAGE', '当前不在 BOSS 推荐牛人页面，请先用 boss_goto recommend；后台筛选文案不构成页面前置条件')
          // 前置校验：必须在推荐牛人列表页（有「筛选」按钮，与 bossGreet 同源判定）
          const probe = await session.snapshot()
          if (!probe.strings.some((s) => FILTER_BUTTON_PATTERN.test(s.trim()))) {
            throw new CodedOperationError(
              'WRONG_PAGE',
              '当前页面不是推荐牛人列表页（未找到「筛选」按钮），请先用 boss_goto 切换到「推荐牛人」页',
            )
          }
          const setter = new FilterSetter({
            snapshot: () => session.snapshot(FILTER_SNAPSHOT_OPTIONS),
            click: session.click,
            signal: ctx.signal,
            // 性能埋点：固定 sleep 与关键阶段耗时进 collector（只记名称与毫秒）
            sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
            step: <T>(name: string, fn: () => Promise<T>) => perf.time(`filter:${name}`, fn),
            // VIP 面板滚动适配：CDP 浏览类滚动（不占真实鼠标）
            mouseWheel: session.mouseWheel,
          })
          ctx.progress({ stage: 'execute', message: '打开筛选面板读取可选档位（借用真实鼠标，请勿移动）' })
          const rows = await setter.probeOptions()
          const lines = rows.map((r) => `${r.label}：${r.range ? `整数范围${r.range.min}–${r.range.max}岁，上限可设不限（age.max=null）` : r.options.map((o) => o.text).join('、')}`)
          return {
            message: `完成：读取到 ${rows.length} 行筛选条件可选档位`,
            data: {
              filter_schema_version: 2,
              supported_fields: ['experience', 'educations', 'salary', 'filters', ...(rows.some((r) => r.control === 'range') ? ['age'] : [])],
              probe_complete: rows.every((r) => !!r.control),
              rows: rows.map((r) => ({
                label: r.label,
                options: r.options.map((o) => o.text),
                ...(r.selection ? { selection: r.selection } : {}),
                ...(r.control ? { control: r.control } : {}),
                ...(r.range ? { range: r.range } : {}),
              })),
              hint: '将职位默认要求与用户当轮补充条件合并；扩展维度用 filters=[{label:页面行标签,options:[精确选项]}]。范围控件不能作为离散选项。未解析的维度不能据此声称页面不存在。',
              readable_lines: lines,
            },
            effect: 'none',
          }
        },
      )
    },
  }
}
