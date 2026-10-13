/**
 * boss_filter / boss_clear_filter operation（设计 §10.2）。
 *
 * 推荐牛人页筛选面板：设置经验/学历/薪资（替换语义，先清除再选）或一键清空。
 * 全部走 Win32 真实鼠标通道；写动作，徽章计数校验通过才算成功。
 */
import { FilterSetter, FILTER_SNAPSHOT_OPTIONS, type FilterSpec } from '../boss/FilterSetter.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { CodedOperationError, type BossOperation, type OperationResult, type OpContext } from './types.js'

export interface BossFilterArgs {
  /** 经验要求行选项，如 '5-10年' */
  experience?: string
  /** 学历要求行选项（多选），如 ['本科','硕士'] */
  educations?: string[]
  /** 薪资待遇行选项（单选），如 '10-20K' */
  salary?: string
  filters?: FilterSpec['filters']
  options_ref?: string
  age?: FilterSpec['age']
}

function validateFilterArgs(args: BossFilterArgs): string | null {
  if (args === null || typeof args !== 'object') return '参数必须是对象'
  if (Object.keys(args).some((k) => !['experience', 'educations', 'salary', 'filters', 'options_ref', 'age'].includes(k))) return '不支持的筛选参数'
  if (args.options_ref !== undefined && (typeof args.options_ref !== 'string' || !args.options_ref.trim())) return 'options_ref 必须是非空引用'
  const { experience, educations, salary } = args
  if (experience !== undefined && (typeof experience !== 'string' || !experience.trim())) {
    return 'experience 必须是非空字符串'
  }
  if (salary !== undefined && (typeof salary !== 'string' || !salary.trim())) {
    return 'salary 必须是非空字符串'
  }
  if (educations !== undefined) {
    if (!Array.isArray(educations) || educations.some((e) => typeof e !== 'string' || !e.trim())) {
      return 'educations 必须是非空字符串数组'
    }
  }
  if (args.filters !== undefined && (!Array.isArray(args.filters) || args.filters.some((r) =>
    !r || typeof r !== 'object' || typeof r.label !== 'string' || !r.label.trim()
    || !Array.isArray(r.options) || r.options.length === 0 || r.options.some((o) => typeof o !== 'string' || !o.trim())
    || Object.keys(r).some((k) => !['label', 'options'].includes(k))))) return 'filters 必须为非空行标签与精确选项数组'
  if (args.age !== undefined) {
    const age = args.age
    if (!age || typeof age !== 'object' || Array.isArray(age) || Object.keys(age).some((k) => !['min', 'max'].includes(k)) || Object.keys(age).length === 0
      || (age.min !== undefined && (!Number.isInteger(age.min) || age.min < 16 || age.min > 45))
      || (age.max !== undefined && age.max !== null && (!Number.isInteger(age.max) || age.max < (age.min ?? 16) || age.max > 45))) return 'age需16至45岁整数范围，最大不限用null，至少提供一端'
  }
  if (!experience?.trim() && !(educations && educations.length > 0) && !salary?.trim() && !args.filters?.length && !args.age) {
    return '至少需要一个筛选条件：experience / educations / salary / filters'
  }
  return null
}

export function createBossFilterOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossFilterArgs> {
  return {
    name: 'boss_filter',
    execute(args: BossFilterArgs, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation(
        { kind: 'write', name: 'boss_filter' },
        ctx,
        sessionFactory,
        () => validateFilterArgs(args),
        async (session, _tracker, perf) => {
          const currentUrl = await session.getUrl()
          let onRecommend = false
          try {
            const page = new URL(currentUrl)
            onRecommend = page.protocol === 'https:' && ['www.zhipin.com', 'zhipin.com'].includes(page.hostname)
              && page.pathname === '/web/chat/recommend'
          } catch { /* 非法URL按错误页面拒绝，不能根据后台iframe文案放行。 */ }
          if (!onRecommend) throw new CodedOperationError('WRONG_PAGE', '当前不在 BOSS 推荐牛人页面，请先用 boss_goto recommend；后台筛选文案不构成页面前置条件')
          ctx.progress({ stage: 'execute', message: '设置筛选面板' })
          let mutated = false
          const setter = new FilterSetter({
            snapshot: () => session.snapshot(FILTER_SNAPSHOT_OPTIONS),
            click: session.click,
            signal: ctx.signal,
            // 性能埋点：固定 sleep 与关键阶段耗时进 collector（只记名称与毫秒）
            sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
            step: <T>(name: string, fn: () => Promise<T>) => perf.time(`filter:${name}`, fn),
            // VIP 面板滚动适配：CDP 浏览类滚动（不占真实鼠标）
            mouseWheel: session.mouseWheel,
            drag: session.drag,
            onMutation: () => { mutated = true },
          })
          const spec: FilterSpec = {
            experience: args.experience?.trim(),
            educations: args.educations?.map((e) => e.trim()),
            salary: args.salary?.trim(),
            filters: args.filters,
            age: args.age,
          }
          let result: Awaited<ReturnType<FilterSetter['apply']>>
          try { result = await setter.apply(spec) } catch (error) {
            if (mutated) throw new CodedOperationError('EXECUTION_UNKNOWN', `筛选状态已开始改变但未完成校验，禁止自动重试：${error instanceof Error ? error.message : String(error)}`)
            throw error
          }
          // 保底映射说明：LLM/用户传的档位页面上不存在时已自动映射到最接近的真实档位，必须转述
          const subNote = result.substitutions
            .map((s) => `${s.row}「${s.requested}」无该档位，已按最接近的「${s.matched}」设置`)
            .join('；')
          return {
            message:
              `筛选已应用并校验通过：筛选·${result.filterCount}` + (subNote ? `；${subNote}` : ''),
            data: {
              filter_schema_version: 2,
              filter_count: result.filterCount,
              applied: true,
              applied_rows: result.appliedRows,
              ...(result.age ? { age: result.age } : {}),
              ...(result.substitutions.length > 0 ? { substitutions: result.substitutions } : {}),
            },
            effect: 'applied' as const,
          }
        },
      )
    },
  }
}

export function createBossClearFilterOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<Record<string, never>> {
  return {
    name: 'boss_clear_filter',
    execute(_args: Record<string, never>, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation(
        { kind: 'write', name: 'boss_clear_filter' },
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
          ctx.progress({ stage: 'execute', message: '打开面板 → 清除 → 确定' })
          let mutated = false
          const setter = new FilterSetter({
            snapshot: () => session.snapshot(FILTER_SNAPSHOT_OPTIONS),
            click: session.click,
            signal: ctx.signal,
            sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
            step: <T>(name: string, fn: () => Promise<T>) => perf.time(`filter:${name}`, fn),
            mouseWheel: session.mouseWheel,
            onMutation: () => { mutated = true },
          })
          try { await setter.clear() } catch (error) {
            if (mutated) throw new CodedOperationError('EXECUTION_UNKNOWN', `筛选清除效果未完成校验，禁止自动重试：${error instanceof Error ? error.message : String(error)}`)
            throw error
          }
          return {
            message: '筛选已清除（徽章无计数）',
            data: { filter_count: 0, applied: true },
            effect: 'applied' as const,
          }
        },
      )
    },
  }
}
