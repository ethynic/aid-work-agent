/**
 * boss_filter 可选筛选值的输入清洗（设计「BOSS 筛选参数容错」；2026-11-13 决策：容错收敛在
 * CLI 侧，服务端不做归一化）。
 *
 * 原则：有效条件按原规则执行，无效的可选条件按未填写处理；模型生成的参数由代码清洗。
 * 本函数是唯一规则来源，三处入口共用：
 * - toolDefs.ts：boss_filter 各可选筛选字段的 z.preprocess（MCP 严格校验前生效）；
 * - cli/commands/filter.ts：filter 命令入口（预览清洗结果、零输入跳过鼠标警示）；
 * - operations/bossFilter.ts：operation 入口（告知写入回执、零输入结构化回执）。
 *
 * 纯函数、total：任何输入都不抛错；只清洗 boss_filter 的 9 个可选筛选字段，
 * 其余工具与字段不套用本规则。清洗后无法形成任何筛选条件时由 operation 返回
 * 零输入结构化回执（INVALID_ARGUMENT / effect=none），不发起页面动作。
 */

/** 清洗后的输入只含 boss_filter 已知字段；引用/提示类字段非空值原样保留，故类型为 unknown */
export interface BossFilterCleanInput {
  experience?: string
  educations?: string[]
  salary?: string
  filters?: Array<{ label: string; options: string[] }>
  options_ref?: unknown
  age?: { min?: number; max?: number | null }
  membership_hint?: unknown
  membership_account_ref?: unknown
  first_degree_only?: boolean
}

export interface SanitizedBossFilterInput {
  /** 清洗后的输入（新对象，不改写调用方入参） */
  input: BossFilterCleanInput
  /** 需向用户简要告知的清洗项（非法年龄/非布尔 first_degree_only 等被整体跳过的条件） */
  notices: string[]
  /** 清洗后是否仍有至少一个有效筛选条件 */
  hasAnyCondition: boolean
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** experience/salary：非字符串、空串、纯空白视为该维度不筛选；非空文本去首尾空白 */
function cleanDimensionText(raw: unknown): string | undefined {
  if (typeof raw !== 'string') return undefined
  const trimmed = raw.trim()
  return trimmed === '' ? undefined : trimmed
}

/** educations：非数组视为无条件；数组仅保留去首尾空白后的非空字符串 */
function cleanEducations(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined
  const cleaned = raw
    .filter((item): item is string => typeof item === 'string' && item.trim() !== '')
    .map((item) => item.trim())
  return cleaned.length > 0 ? cleaned : undefined
}

/**
 * filters：非数组/空数组视为无动态条件；跳过无有效标签或无有效选项的行；
 * 选项保留去首尾空白后的非空字符串；行重建为恰好 {label, options}。
 */
function cleanFilters(raw: unknown): Array<{ label: string; options: string[] }> | undefined {
  if (!Array.isArray(raw)) return undefined
  const rows: Array<{ label: string; options: string[] }> = []
  for (const row of raw) {
    if (!isObject(row)) continue
    const label = typeof row.label === 'string' ? row.label.trim() : ''
    const options = Array.isArray(row.options)
      ? row.options.filter((item): item is string => typeof item === 'string' && item.trim() !== '').map((item) => item.trim())
      : []
    if (!label || options.length === 0) continue
    rows.push({ label, options })
  }
  return rows.length > 0 ? rows : undefined
}

/**
 * age：空对象/非对象/无有效边界不筛选年龄（静默）；空值边界表示未填；
 * {min:25,max:null} 继续表示 25 岁以上。实际传入的非法年龄（超范围/倒序/非空
 * 错误类型/非整数）跳过整个年龄条件并告知，避免把错误范围解释成另一个范围。
 */
function cleanAge(raw: unknown, notices: string[]): { min?: number; max?: number | null } | undefined {
  if (!isObject(raw)) return undefined
  let min: number | undefined
  let max: number | null | undefined
  let invalidBound = false
  if (raw.min !== undefined && raw.min !== null) {
    if (typeof raw.min === 'number' && Number.isInteger(raw.min) && raw.min >= 16 && raw.min <= 45) min = raw.min
    else invalidBound = true
  }
  if (raw.max === null) max = null
  else if (raw.max !== undefined) {
    if (typeof raw.max === 'number' && Number.isInteger(raw.max) && raw.max >= 16 && raw.max <= 45) max = raw.max
    else invalidBound = true
  }
  if (invalidBound) {
    notices.push('年龄条件无效（需16至45岁整数），已跳过年龄筛选')
    return undefined
  }
  if (min === undefined && typeof max !== 'number') return undefined
  if (min !== undefined && typeof max === 'number' && max < min) {
    notices.push('年龄范围上下限倒置，已跳过年龄筛选')
    return undefined
  }
  const age: { min?: number; max?: number | null } = {}
  if (min !== undefined) age.min = min
  if (max !== undefined) age.max = max
  return age
}

/** options_ref/membership_hint/membership_account_ref：空值按未提供处理；非空值原样保留（格式错误仍按原规则拒绝） */
function cleanRefLike(raw: unknown): unknown {
  if (raw === null || raw === undefined) return undefined
  if (typeof raw === 'string' && raw.trim() === '') return undefined
  return raw
}

/**
 * 唯一规则来源的 boss_filter 输入清洗。total：任何输入都不抛错。
 */
export function sanitizeBossFilterInput(raw: unknown): SanitizedBossFilterInput {
  const input: BossFilterCleanInput = {}
  const notices: string[] = []
  if (isObject(raw)) {
    if (raw.experience !== undefined) {
      const cleaned = cleanDimensionText(raw.experience)
      if (cleaned !== undefined) input.experience = cleaned
    }
    if (raw.salary !== undefined) {
      const cleaned = cleanDimensionText(raw.salary)
      if (cleaned !== undefined) input.salary = cleaned
    }
    if (raw.educations !== undefined) {
      const cleaned = cleanEducations(raw.educations)
      if (cleaned !== undefined) input.educations = cleaned
    }
    if (raw.filters !== undefined) {
      const cleaned = cleanFilters(raw.filters)
      if (cleaned !== undefined) input.filters = cleaned
    }
    if (raw.age !== undefined) {
      const cleaned = cleanAge(raw.age, notices)
      if (cleaned !== undefined) input.age = cleaned
    }
    if (raw.first_degree_only !== undefined) {
      if (typeof raw.first_degree_only === 'boolean') input.first_degree_only = raw.first_degree_only
      else notices.push('first_degree_only 需为布尔值，已忽略该参数')
    }
    if (raw.options_ref !== undefined) {
      const cleaned = cleanRefLike(raw.options_ref)
      if (cleaned !== undefined) input.options_ref = cleaned
    }
    if (raw.membership_hint !== undefined) {
      const cleaned = cleanRefLike(raw.membership_hint)
      if (cleaned !== undefined) input.membership_hint = cleaned
    }
    if (raw.membership_account_ref !== undefined) {
      const cleaned = cleanRefLike(raw.membership_account_ref)
      if (cleaned !== undefined) input.membership_account_ref = cleaned
    }
  }
  const hasAnyCondition = input.experience !== undefined || input.salary !== undefined
    || (input.educations?.length ?? 0) > 0 || (input.filters?.length ?? 0) > 0
    || input.age !== undefined
  return { input, notices, hasAnyCondition }
}
