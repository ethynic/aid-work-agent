/**
 * CLI 岗位配置文件加载与校验（Phase 9，设计文档 §16 决策 6）。
 * - 支持 .json（JSON.parse）与 .yaml/.yml（js-yaml，项目已有依赖）
 * - 硬规则键白名单复用 workflow/hardRules 的 parseHardRuleConfig（不重复造轮子）
 * - 全部校验 fail-loud：缺字段 / 未知键 / 类型错误一律抛错，绝不静默忽略
 */
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { parseHardRuleConfig, type HardRuleConfig } from '../main/workflow/hardRules.js'

// js-yaml 无官方类型包（@types/js-yaml 未安装），用 createRequire + 最小接口声明
const require = createRequire(import.meta.url)
interface YamlModule {
  load: (text: string) => unknown
}
const yaml = require('js-yaml') as YamlModule

/** 岗位配置文件解析后的结构（校验通过保证） */
export interface JobFileConfig {
  name: string
  hardRules: HardRuleConfig
  /** 偏好文本（供 LLM 参考，不做确定性判断） */
  preferences: string | null
  actionLimitSession: number | null
  actionLimitDay: number | null
}

function fail(message: string): never {
  throw new Error(`岗位配置非法: ${message}`)
}

function assertStringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((v) => typeof v !== 'string' || !v.trim())) {
    fail(`${label} 必须是非空字符串数组`)
  }
  return value as string[]
}

function assertPositiveInt(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value <= 0) {
    fail(`${label} 必须是正整数`)
  }
  return value
}

/** 校验硬规则对象的键白名单与各键类型。返回净化后的 HardRuleConfig。 */
function validateHardRules(raw: unknown): HardRuleConfig {
  if (raw === undefined || raw === null) return {}
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    fail('hardRules 必须是对象')
  }
  // 键白名单校验复用 parseHardRuleConfig（未知键抛错）
  const config = parseHardRuleConfig(JSON.stringify(raw))
  const obj = raw as Record<string, unknown>
  const out: HardRuleConfig = {}
  if (config.city !== undefined) out.city = assertStringArray(obj['city'], 'hardRules.city')
  if (config.minYears !== undefined) out.minYears = assertPositiveInt(obj['minYears'], 'hardRules.minYears')
  if (config.requiredSkills !== undefined) out.requiredSkills = assertStringArray(obj['requiredSkills'], 'hardRules.requiredSkills')
  if (config.excludeKeywords !== undefined) out.excludeKeywords = assertStringArray(obj['excludeKeywords'], 'hardRules.excludeKeywords')
  return out
}

/** 从已解析的对象校验岗位配置（与文件格式无关，便于测试） */
export function validateJobConfig(raw: unknown): JobFileConfig {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    fail('配置必须是对象')
  }
  const obj = raw as Record<string, unknown>

  const name = obj['name']
  if (typeof name !== 'string' || !name.trim()) {
    fail('name 必填且必须是非空字符串')
  }

  const hardRules = validateHardRules(obj['hardRules'])

  let preferences: string | null = null
  if (obj['preferences'] !== undefined && obj['preferences'] !== null) {
    if (typeof obj['preferences'] !== 'string') {
      fail('preferences 必须是字符串')
    }
    preferences = obj['preferences']
  }

  let actionLimitSession: number | null = null
  let actionLimitDay: number | null = null
  const limits = obj['actionLimits']
  if (limits !== undefined && limits !== null) {
    if (typeof limits !== 'object' || Array.isArray(limits)) {
      fail('actionLimits 必须是对象（session/day）')
    }
    const lo = limits as Record<string, unknown>
    for (const key of Object.keys(lo)) {
      if (key !== 'session' && key !== 'day') {
        fail(`actionLimits 包含未知键 "${key}"（支持：session, day）`)
      }
    }
    if (lo['session'] !== undefined && lo['session'] !== null) {
      actionLimitSession = assertPositiveInt(lo['session'], 'actionLimits.session')
    }
    if (lo['day'] !== undefined && lo['day'] !== null) {
      actionLimitDay = assertPositiveInt(lo['day'], 'actionLimits.day')
    }
  }

  return { name: name.trim(), hardRules, preferences, actionLimitSession, actionLimitDay }
}

/** 加载岗位配置文件（.json / .yaml / .yml）。文件不存在、格式错误、校验失败均 fail-loud。 */
export function loadJobConfigFile(filePath: string): JobFileConfig {
  if (!fs.existsSync(filePath)) {
    throw new Error(`岗位配置文件不存在: ${filePath}`)
  }
  const text = fs.readFileSync(filePath, 'utf8')
  const ext = path.extname(filePath).toLowerCase()
  let parsed: unknown
  try {
    if (ext === '.json') {
      parsed = JSON.parse(text)
    } else if (ext === '.yaml' || ext === '.yml') {
      parsed = yaml.load(text)
    } else {
      throw new Error(`不支持的配置文件格式 "${ext}"（支持 .json / .yaml / .yml）`)
    }
  } catch (e) {
    if (e instanceof Error && e.message.startsWith('不支持的配置文件格式')) throw e
    throw new Error(`岗位配置文件解析失败: ${e instanceof Error ? e.message : String(e)}`)
  }
  return validateJobConfig(parsed)
}
