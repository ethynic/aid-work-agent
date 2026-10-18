import { runVerifiedPageOperation, retainedReadCleanupFailure } from './pageOperation.js'
/**
 * boss_filter_options operation（设计 §10.2 补充，2026-08-17 用户定调「档位判断交 AI」）：
 * 只读探查推荐牛人页筛选面板可确认的档位，局部不可读行单独报告，供调用方（LLM）
 * 把用户要求匹配到真实可选档位，忽略未匹配条件后再调
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
          let cleanupFacts: Record<string, unknown> | undefined
          let cleanupError: { code: string; message: string } | undefined
          const verified = await runVerifiedPageOperation({ name: 'boss_filter_options', goal: '读取筛选可选档位并收起面板', expected: '可读筛选档位已取得且面板已收起', operationClass: 'readonly' }, async (attempt) => {
            let activeAttempt = attempt
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
              click: (...args) => activeAttempt.input(() => session.click(...args)),
              signal: ctx.signal,
              // 性能埋点：固定 sleep 与关键阶段耗时进 collector（只记名称与毫秒）
              sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
              step: <T>(name: string, fn: () => Promise<T>) => perf.time(`filter:${name}`, fn),
              // VIP 面板滚动适配：CDP 浏览类滚动（不占真实鼠标）
              mouseWheel: (...args) => activeAttempt.input(() => session.mouseWheel(...args)),
            })
            ctx.progress({ stage: 'execute', message: '打开筛选面板读取可选档位（借用真实鼠标，请勿移动）' })
            const rows = await setter.probeOptions({ close: false })
            attempt.verified({ status: 'met', evidence: [`已取得${rows.length}行可读筛选档位`] })
            const cleanupIntent = { name: 'boss_filter_options', operationClass: 'readonly' as const, goal: '保留已读筛选档位，收起筛选面板', expected: '原筛选工具栏可读且筛选面板已收起' }
            try {
              const closed = await runVerifiedPageOperation(cleanupIntent, async (closing) => {
                activeAttempt = closing
                await setter.closeProbePanel()
                closing.verified(await setter.verifyProbeClosed())
              }, attempt.execution)
              cleanupFacts = closed.facts
            } catch (error) {
              const retained = retainedReadCleanupFailure(error, cleanupIntent, activeAttempt.execution)
              cleanupFacts = retained.facts
              cleanupError = retained.cleanupError
            }
            const lines = rows.map((r) => `${r.label}：${r.range ? `整数范围${r.range.min}–${r.range.max}岁，上限可设不限（age.max=null）` : r.options.map((o) => o.text).join('、')}`)
            return {
              message: `完成：读取到 ${rows.length} 行筛选条件可选档位`,
              data: {
                filter_schema_version: 3,
                supported_fields: ['experience', 'educations', 'salary', 'filters', 'age', 'membership_hint', 'membership_account_ref', 'first_degree_only'],
                membership: setter.membership,
                account_ref: setter.accountRef,
                groups: {
                  ordinary: { labels: rows.filter((r) => r.group === 'ordinary').map((r) => r.label), available: true },
                  vip: { labels: rows.filter((r) => r.group === 'vip').map((r) => r.label), available: setter.membership.status === 'vip', visible: rows.some((r) => r.group === 'vip') },
                },
                probe_complete: setter.unreadableRows.length === 0 && rows.every((r) => !!r.control),
                unreadable_rows: setter.unreadableRows,
                rows: rows.map((r) => ({
                  label: r.label,
                  options: r.options.map((o) => o.text),
                  ...(r.selection ? { selection: r.selection } : {}),
                  ...(r.control ? { control: r.control } : {}),
                  ...(r.range ? { range: r.range } : {}),
                  ...(r.group ? { group: r.group } : {}),
                })),
                hint: '将职位默认要求与用户当轮补充条件合并；只提交rows中已确认且可匹配的行与选项，匹配不到或unreadable_rows中的条件忽略，简要告知并继续其余筛选，不声称全部条件已满足；全部未匹配时不调用空筛选或清除。经验/学历/求职状态支持filters多选，年龄用age。membership未知时先询问用户再传membership_hint；推广VIP徽标不是会员证明。非VIP高级条件只跳过，按skipped_filters报告。院校附属第一学历用first_degree_only。',
                readable_lines: lines,
              },
              effect: 'none' as const,
            }
          })
          return { ...verified.value, message: verified.value.message + (cleanupError ? '；已保留读取结果，页面收尾尚未确认' : ''),
            data: { ...verified.value.data, operation_recovery: cleanupFacts ?? verified.facts, ...(cleanupError ? { cleanup_error: cleanupError } : {}) } }
        },
      )
    },
  }
}
