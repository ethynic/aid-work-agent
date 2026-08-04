/**
 * 岗位硬规则构建器（Phase 8，设计文档 §9.2 两阶段判断的阶段一）。
 * 把 jobs.hard_rules JSON 配置翻译成 ScreeningEngine 的 HardRule[]。
 *
 * 支持的配置键（fail-loud：未知键直接抛错，防止配置拼写错误被静默忽略）：
 * - city: string[]           期望城市白名单（fields.city 不在列 → FAIL；无该字段 → UNDECIDED）
 * - minYears: number         最低工作年限（fields.workYears 解析不出 → UNDECIDED）
 * - requiredSkills: string[] 必备技能（简历 markdown 不包含 → FAIL；markdown 为空 → UNDECIDED）
 * - excludeKeywords: string[] 排除关键词（markdown 命中 → FAIL；markdown 为空 → UNDECIDED）
 */
import type { HardRule } from '../screening/ScreeningEngine.js'

export interface HardRuleConfig {
  city?: string[]
  minYears?: number
  requiredSkills?: string[]
  excludeKeywords?: string[]
}

const KNOWN_KEYS = new Set(['city', 'minYears', 'requiredSkills', 'excludeKeywords'])

/** 解析并校验 hard_rules JSON；非法输入 fail-loud */
export function parseHardRuleConfig(raw: string | null | undefined): HardRuleConfig {
  if (!raw || !raw.trim()) return {}
  let obj: unknown
  try {
    obj = JSON.parse(raw)
  } catch (e) {
    throw new Error(`hard_rules JSON 解析失败: ${e instanceof Error ? e.message : String(e)}`)
  }
  if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) {
    throw new Error('hard_rules 必须是 JSON 对象')
  }
  for (const key of Object.keys(obj as Record<string, unknown>)) {
    if (!KNOWN_KEYS.has(key)) {
      throw new Error(`hard_rules 包含未知键 "${key}"（支持：${[...KNOWN_KEYS].join(', ')}）`)
    }
  }
  return obj as HardRuleConfig
}

/** 配置 → HardRule[]。无规则返回空数组（阶段一全过，交给 LLM/UNCERTAIN 逻辑） */
export function buildHardRules(config: HardRuleConfig): HardRule[] {
  const rules: HardRule[] = []

  if (Array.isArray(config.city) && config.city.length > 0) {
    const allowed = config.city
    rules.push({
      field: 'city',
      description: `期望城市须为：${allowed.join('/')}`,
      judge: (doc) => {
        const city = doc.fields['city']
        if (!city) return null
        return allowed.some((c) => city.includes(c))
      },
    })
  }

  if (typeof config.minYears === 'number' && Number.isFinite(config.minYears)) {
    const min = config.minYears
    rules.push({
      field: 'workYears',
      description: `工作年限 ≥ ${min} 年`,
      judge: (doc) => {
        const raw = doc.fields['workYears']
        if (!raw) return null
        const m = raw.match(/(\d+(?:\.\d+)?)/)
        if (!m) return null
        return Number(m[1]) >= min
      },
    })
  }

  if (Array.isArray(config.requiredSkills) && config.requiredSkills.length > 0) {
    const skills = config.requiredSkills
    rules.push({
      field: 'requiredSkills',
      description: `必须具备技能：${skills.join('、')}`,
      judge: (doc) => {
        if (!doc.markdown.trim()) return null
        return skills.every((s) => doc.markdown.includes(s))
      },
    })
  }

  if (Array.isArray(config.excludeKeywords) && config.excludeKeywords.length > 0) {
    const words = config.excludeKeywords
    rules.push({
      field: 'excludeKeywords',
      description: `不得包含：${words.join('、')}`,
      judge: (doc) => {
        if (!doc.markdown.trim()) return null
        return !words.some((w) => doc.markdown.includes(w))
      },
    })
  }

  return rules
}
