/**
 * boss_filter / boss_clear_filter operation（设计 §10.2）。
 *
 * 推荐牛人页筛选面板：设置经验/学历/薪资（替换语义，先清除再选）或一键清空。
 * 全部走 Win32 真实鼠标通道；写动作，徽章计数校验通过才算成功。
 */
import { FilterSetter, type FilterSpec } from '../boss/FilterSetter.js'
import {
  defaultSessionFactory,
  runBossOperation,
  type BossSessionFactory,
} from './bossContext.js'
import type { BossOperation, OperationResult, OpContext } from './types.js'

export interface BossFilterArgs {
  /** 经验要求行选项，如 '5-10年' */
  experience?: string
  /** 学历要求行选项（多选），如 ['本科','硕士'] */
  educations?: string[]
  /** 薪资待遇行选项（单选），如 '10-20K' */
  salary?: string
}

function validateFilterArgs(args: BossFilterArgs): string | null {
  if (args === null || typeof args !== 'object') return '参数必须是对象'
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
  if (!experience?.trim() && !(educations && educations.length > 0) && !salary?.trim()) {
    return '至少需要一个筛选条件：experience / educations / salary'
  }
  return null
}

export function createBossFilterOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<BossFilterArgs> {
  return {
    name: 'boss_filter',
    execute(args: BossFilterArgs, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation('write', ctx, sessionFactory, () => validateFilterArgs(args), async (session) => {
        ctx.progress({ stage: 'execute', message: '设置筛选面板' })
        const setter = new FilterSetter({
          snapshot: session.snapshot,
          click: session.click,
          signal: ctx.signal,
        })
        const spec: FilterSpec = {
          experience: args.experience?.trim(),
          educations: args.educations?.map((e) => e.trim()),
          salary: args.salary?.trim(),
        }
        const result = await setter.apply(spec)
        // 保底映射说明：LLM/用户传的档位页面上不存在时已自动映射到最接近的真实档位，必须转述
        const subNote = result.substitutions
          .map((s) => `${s.row}「${s.requested}」无该档位，已按最接近的「${s.matched}」设置`)
          .join('；')
        return {
          message:
            `筛选已应用并校验通过：筛选·${result.filterCount}` + (subNote ? `；${subNote}` : ''),
          data: {
            filter_count: result.filterCount,
            applied: true,
            ...(result.substitutions.length > 0 ? { substitutions: result.substitutions } : {}),
          },
          effect: 'applied' as const,
        }
      })
    },
  }
}

export function createBossClearFilterOperation(
  sessionFactory: BossSessionFactory = defaultSessionFactory,
): BossOperation<Record<string, never>> {
  return {
    name: 'boss_clear_filter',
    execute(_args: Record<string, never>, ctx: OpContext): Promise<OperationResult> {
      return runBossOperation('write', ctx, sessionFactory, () => null, async (session) => {
        ctx.progress({ stage: 'execute', message: '打开面板 → 清除 → 确定' })
        const setter = new FilterSetter({
          snapshot: session.snapshot,
          click: session.click,
          signal: ctx.signal,
        })
        await setter.clear()
        return {
          message: '筛选已清除（徽章无计数）',
          data: { filter_count: 0, applied: true },
          effect: 'applied' as const,
        }
      })
    },
  }
}
