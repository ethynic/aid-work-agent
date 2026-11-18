/**
 * boss_filter 可选筛选值的输入清洗（设计「BOSS 筛选参数容错」；2026-11-13 决策：容错收敛在
 * CLI 侧，服务端不做归一化）。
 *
 * 原则：有效条件按原规则执行，无效的可选条件按未填写处理；模型生成的参数由代码清洗。
 * 本函数是唯一规则来源，三处入口共用：
 * - toolDefs.ts：boss_filter 维持预处理字段的 z.preprocess（filters/age/引用类，MCP 严格校验前生效）；
 * - cli/commands/filter.ts：filter 命令入口（预览清洗结果、零输入跳过鼠标警示）；
 * - operations/bossFilter.ts：operation 入口（告知/跳过证据写入回执、零输入结构化回执）。
 *
 * 纯函数、total：任何输入都不抛错；只清洗 boss_filter 的 9 个可选筛选字段，
 * 其余工具与字段不套用本规则。清洗后无法形成任何筛选条件时由 operation 返回
 * 零输入结构化回执（INVALID_ARGUMENT / effect=none），不发起页面动作。
 *
 * R5（2026-11-13）：放宽四处（experience/salary/educations/first_degree_only 接受 null、
 * educations 项接受任意字符串）后，这些原始形态经 MCP schema 到达 operation 由本函数清洗。
 * 无效值（experience:null/空白、educations 空白项、first_degree_only:null 等）出具版本化
 * 跳过证据（evidence），随回执 data.filter_evidence 发布，云端终态核对据此豁免「CLI 明确
 * 跳过」的请求项，其余条件核验不变；educations:null 与「省略」同义，静默处理不出具证据。
 * 仍在 z.preprocess 删除的字段（age/filters/引用类）无法出具请求项证据——它们的非法形态
 * 云端本就拒绝，不在核对范围。
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

/** 回执 filter_evidence 的单项跳过记录：field/path 定位被跳过的请求项 */
export interface BossFilterSkipRecord {
  /** 请求中的顶层字段名（云端核对按字段名+路径对齐请求项） */
  field: 'experience' | 'salary' | 'educations' | 'first_degree_only'
  /** 请求项定位：标量字段与字段同名；数组项带原下标（educations[1]） */
  path: string
  /** invalid_value = 值存在但无法形成有效条件，按未填写处理 */
  reason: 'invalid_value'
}

/** 版本化筛选跳过证据：随 boss_filter 回执 data.filter_evidence 发布（R5） */
export interface BossFilterEvidence {
  version: 1
  /** 本请求中被清洗跳过的请求项；空数组 = 无跳过（回执省略该字段） */
  skipped: BossFilterSkipRecord[]
}

export interface SanitizedBossFilterInput {
  /** 清洗后的输入（新对象，不改写调用方入参） */
  input: BossFilterCleanInput
  /** 需向用户简要告知的清洗项（非法年龄/非布尔 first_degree_only 等被整体跳过的条件） */
  notices: string[]
  /** 清洗后是否仍有至少一个有效筛选条件 */
  hasAnyCondition: boolean
  /** 本请求中被清洗跳过的请求项证据（operation 写入回执 data.filter_evidence，空则省略） */
  evidence: BossFilterEvidence
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

/** educations：null 按未填写静默省略；其余非数组视为无条件（出具证据）；数组仅保留去首尾空白后的非空字符串，被跳过项按原下标出具证据 */
function cleanEducations(raw: unknown, skipped: BossFilterSkipRecord[]): string[] | undefined {
  if (raw === null) return undefined
  if (!Array.isArray(raw)) {
    skipped.push({ field: 'educations', path: 'educations', reason: 'invalid_value' })
    return undefined
  }
  const cleaned: string[] = []
  raw.forEach((item, index) => {
    if (typeof item === 'string' && item.trim() !== '') cleaned.push(item.trim())
    else skipped.push({ field: 'educations', path: `educations[${index}]`, reason: 'invalid_value' })
  })
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
 * 放宽四处（experience/salary/educations/first_degree_only）被跳过的请求项出具 evidence；
 * 其余字段维持原行为（告知或静默），不出具请求项证据。
 */
export function sanitizeBossFilterInput(raw: unknown): SanitizedBossFilterInput {
  const input: BossFilterCleanInput = {}
  const notices: string[] = []
  const skipped: BossFilterSkipRecord[] = []
  if (isObject(raw)) {
    if (raw.experience !== undefined) {
      const cleaned = cleanDimensionText(raw.experience)
      if (cleaned !== undefined) input.experience = cleaned
      else skipped.push({ field: 'experience', path: 'experience', reason: 'invalid_value' })
    }
    if (raw.salary !== undefined) {
      const cleaned = cleanDimensionText(raw.salary)
      if (cleaned !== undefined) input.salary = cleaned
      else skipped.push({ field: 'salary', path: 'salary', reason: 'invalid_value' })
    }
    if (raw.educations !== undefined) {
      const cleaned = cleanEducations(raw.educations, skipped)
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
      else {
        skipped.push({ field: 'first_degree_only', path: 'first_degree_only', reason: 'invalid_value' })
        // null 与「省略」同义（云端 Optional 语义），静默处理；其余垃圾类型简要告知
        if (raw.first_degree_only !== null) notices.push('first_degree_only 需为布尔值，已忽略该参数')
      }
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
  return { input, notices, hasAnyCondition, evidence: { version: 1, skipped } }
}
