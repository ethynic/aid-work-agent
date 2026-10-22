/**
 * boss_filter / boss_clear_filter operation（设计 §10.2）。
 *
 * 推荐牛人页筛选面板：设置经验/学历/薪资（替换语义，先清除再选）或一键清空。
 * 全部走 Win32 真实鼠标通道；写动作，徽章计数校验通过才算成功。
 */
import { FilterSetter, FilterPostconditionError, FILTER_SNAPSHOT_OPTIONS, type FilterSpec, type FilterPostcondition } from '../boss/FilterSetter.js'
import { WinClickError } from '../input/WinMouseClicker.js'
import { CancelledError } from './types.js'
import { pageFacts, pageFailure, type PageExecution } from './pageOperation.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import { PerfCollector } from '../perf.js'
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
  membership_hint?: FilterSpec['membership_hint']
  membership_account_ref?: string
  first_degree_only?: boolean
}

function verification(value: FilterPostcondition): PageExecution['verification'] {
  return { status: value.status, evidence: [`filter_button_count=${value.observedCount ?? 'unreadable'}; expected=${value.expectedCount ?? 'unknown'}; panel_open=${value.panelOpen}`] }
}

async function verifiedFilter<T>(setter: FilterSetter, invoke: () => Promise<T>, execution: PageExecution,
  name: string, goal: string, expected: string, progress: OpContext['progress'], perf?: PerfCollector): Promise<T> {
  for (;;) {
    execution.attemptsUsed++
    try {
      const result = await invoke()
      execution.stage = 'postcheck'
      execution.verification = { status: 'met', evidence: ['executor已核验实际按钮计数、选项与面板关闭；不可应用条件保留skipped结果'] }
      return result
    } catch (error) {
      if (error instanceof CancelledError) throw error
      let observed: FilterPostcondition | undefined
      if (execution.inputStatus !== 'not_started' || error instanceof FilterPostconditionError) {
        // Phase 5 存活热点分段：失败后的只读核验此前未包装计时（未包装调用），
        // 现单独计段；perf 缺省（旧调用方）保持原行为。
        const verify = () => setter.verifyLastPostcondition()
        try {
          observed = await (perf ? perf.time('filter:verify:fallback', verify) : verify())
        } catch (verifyError) {
          if (verifyError instanceof CodedOperationError || verifyError instanceof CancelledError) throw verifyError
          // Preserve the original failure; a failed read is not success.
        }
      }
      execution.verification = observed ? verification(observed) : { status: 'inconclusive', evidence: ['fresh filter postcheck unavailable'] }
      if (observed?.status === 'met' && (name === 'boss_clear_filter' || observed.result)) {
        execution.stage = 'postcheck'
        return observed.result as T
      }
      if (error instanceof FilterPostconditionError) {
        execution.stage = 'postcheck'
        // A typed row/slider/checkbox mismatch is already a definite negative read,
        // even if the final toolbar has not been submitted yet.
        execution.verification = { status: 'unmet', evidence: [error.message.slice(0, 500)] }
        if (execution.ordinaryRetriesUsed === 0) {
          execution.ordinaryRetriesUsed++
          progress({ stage: 'execute', message: '筛选后态未满足，重新定位后重试一次' })
          continue
        }
      }
      pageFailure(error, pageFacts(name, goal, expected, execution))
    }
  }
}

function filterExecution(previous?: PageExecution): PageExecution {
  return { attemptsUsed: previous?.attemptsUsed ?? 0, ordinaryRetriesUsed: previous?.ordinaryRetriesUsed ?? 0, inputStatus: 'not_started', stage: 'prepared',
    verification: { status: 'inconclusive', evidence: [] } }
}

function trackedClick(click: import('./bossContext.js').BossSession['click'], execution: PageExecution): typeof click {
  return async (point, viewport) => {
    const previous = execution.inputStatus
    execution.stage = 'input'; execution.inputStatus = 'unknown'
    try { await click(point, viewport); execution.inputStatus = 'attempted' }
    catch (error) {
      if (error instanceof WinClickError && error.exitCode === 2) execution.inputStatus = previous
      throw error
    }
  }
}

function validateFilterArgs(args: BossFilterArgs): string | null {
  if (args === null || typeof args !== 'object') return '参数必须是对象'
  if (Object.keys(args).some((k) => !['experience', 'educations', 'salary', 'filters', 'options_ref', 'age', 'membership_hint', 'membership_account_ref', 'first_degree_only'].includes(k))) return '不支持的筛选参数'
  if (args.membership_account_ref !== undefined && (typeof args.membership_account_ref !== 'string' || !/^[a-f0-9]{64}$/.test(args.membership_account_ref))) return 'membership_account_ref必须是当前筛选探查返回的64位账号引用'
  if (args.membership_hint !== undefined && !['vip', 'non_vip'].includes(args.membership_hint)) return 'membership_hint仅允许vip或non_vip，且不能覆盖真实会员/付费证据'
  if (args.first_degree_only !== undefined && typeof args.first_degree_only !== 'boolean') return 'first_degree_only必须为布尔值'
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
  if (args.first_degree_only === true && !args.filters?.some((r) => r.label.trim() === '院校' && r.options.some((o) => o.trim() !== '不限'))) return '只看第一学历必须依附至少一个院校筛选条件'
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
          const execution = filterExecution(ctx.previousPageExecution)
          const goal = `设置候选人筛选条件：${JSON.stringify(args).slice(0, 850)}`
          const expected = '筛选面板已关闭；实际筛选按钮数字等于已应用条件数，选中条件与请求匹配'
          const setter = new FilterSetter({
            snapshot: () => session.snapshot(FILTER_SNAPSHOT_OPTIONS),
            click: trackedClick(session.click, execution),
            signal: ctx.signal,
            // 性能埋点：固定 sleep 与关键阶段耗时进 collector（只记名称与毫秒）
            sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
            step: <T>(name: string, fn: () => Promise<T>) => perf.time(`filter:${name}`, fn),
            // Phase 5 存活热点分段：纯计算步骤同步计时（结构解析/预校验/可见区几何/年龄读取）
            stepSync: <T>(name: string, fn: () => T) => perf.timeSync(name, fn),
            // VIP 面板滚动适配：CDP 浏览类滚动（不占真实鼠标）
            mouseWheel: session.mouseWheel,
            drag: session.drag,
          })
          const spec: FilterSpec = {
            experience: args.experience?.trim(),
            educations: args.educations?.map((e) => e.trim()),
            salary: args.salary?.trim(),
            filters: args.filters,
            age: args.age,
            membership_hint: args.membership_hint,
            membership_account_ref: args.membership_account_ref,
            first_degree_only: args.first_degree_only,
          }
          let result: Awaited<ReturnType<FilterSetter['apply']>>
          result = await verifiedFilter(setter, () => setter.apply(spec), execution, 'boss_filter', goal, expected, ctx.progress, perf)
          // 保底映射说明：LLM/用户传的档位页面上不存在时已自动映射到最接近的真实档位，必须转述
          const subNote = result.substitutions
            .map((s) => `${s.row}「${s.requested}」无该档位，已按最接近的「${s.matched}」设置`)
            .join('；')
          const skippedNote = result.skippedFilters.length > 0 ? `；已跳过VIP条件：${result.skippedFilters.map((s) => s.label).join('、')}（${result.membership.source === 'paywall' ? '当前页面付费拦截已安全关闭' : '本次账号按非VIP处理'}）` : ''
          return {
            message:
              (result.applied ? `筛选已应用并校验通过：筛选·${result.filterCount}` : '本次没有可应用的普通条件，原筛选保留') + (subNote ? `；${subNote}` : '') + skippedNote,
            data: {
              filter_schema_version: 3,
              operation_recovery: pageFacts('boss_filter', goal, expected, execution),
              page_operation: pageFacts('boss_filter', goal, expected, execution),
              filter_count: result.filterCount,
              applied: result.applied,
              applied_rows: result.appliedRows,
              ...(result.age ? { age: result.age } : {}),
              membership: result.membership,
              account_ref: setter.accountRef,
              skipped_filters: result.skippedFilters,
              first_degree_only: result.firstDegreeOnly,
              ...(result.substitutions.length > 0 ? { substitutions: result.substitutions } : {}),
            },
            effect: result.applied ? 'applied' as const : 'none' as const,
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
          const execution = filterExecution(ctx.previousPageExecution)
          const goal = '清除当前候选人筛选条件'
          const expected = '筛选面板已关闭；实际可见筛选按钮不带数字（0个条件）'
          const setter = new FilterSetter({
            snapshot: () => session.snapshot(FILTER_SNAPSHOT_OPTIONS),
            click: trackedClick(session.click, execution),
            signal: ctx.signal,
            sleep: (ms) => perf.time(`sleep:${ms}`, () => new Promise<void>((r) => setTimeout(r, ms))),
            step: <T>(name: string, fn: () => Promise<T>) => perf.time(`filter:${name}`, fn),
            stepSync: <T>(name: string, fn: () => T) => perf.timeSync(name, fn),
            mouseWheel: session.mouseWheel,
          })
          await verifiedFilter(setter, () => setter.clear(), execution, 'boss_clear_filter', goal, expected, ctx.progress, perf)
          return {
            message: '筛选已清除（徽章无计数）',
            data: { filter_count: 0, applied: true,
              operation_recovery: pageFacts('boss_clear_filter', goal, expected, execution),
              page_operation: pageFacts('boss_clear_filter', goal, expected, execution) },
            effect: 'applied' as const,
          }
        },
      )
    },
  }
}
