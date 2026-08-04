import assert from 'node:assert/strict'
import test from 'node:test'
import { buildHardRules, parseHardRuleConfig } from '../src/main/workflow/hardRules.js'
import type { ScreeningInput } from '../src/main/screening/ScreeningEngine.js'

function doc(fields: Record<string, string | undefined>, markdown = ''): ScreeningInput {
  return { markdown, fields }
}

test('空配置/空字符串 → 无规则', () => {
  assert.deepEqual(parseHardRuleConfig(null), {})
  assert.deepEqual(parseHardRuleConfig(''), {})
  assert.equal(buildHardRules({}).length, 0)
})

test('未知键 fail-loud（防配置拼写错误被静默忽略）', () => {
  assert.throws(() => parseHardRuleConfig('{"cities":["北京"]}'), /未知键/)
  assert.throws(() => parseHardRuleConfig('"not-object"'), /必须是 JSON 对象/)
  assert.throws(() => parseHardRuleConfig('{bad json'), /解析失败/)
})

test('city 规则：在列通过 / 不在列失败 / 无字段未决', () => {
  const [rule] = buildHardRules({ city: ['北京', '上海'] })
  assert.ok(rule)
  assert.equal(rule!.judge(doc({ city: '北京' })), true)
  assert.equal(rule!.judge(doc({ city: '杭州' })), false)
  assert.equal(rule!.judge(doc({})), null)
})

test('minYears 规则：达标通过 / 不足失败 / 解析不出未决', () => {
  const [rule] = buildHardRules({ minYears: 3 })
  assert.ok(rule)
  assert.equal(rule!.judge(doc({ workYears: '5年' })), true)
  assert.equal(rule!.judge(doc({ workYears: '3' })), true)
  assert.equal(rule!.judge(doc({ workYears: '1年' })), false)
  assert.equal(rule!.judge(doc({ workYears: '应届' })), null)
  assert.equal(rule!.judge(doc({})), null)
})

test('requiredSkills：全部包含才通过；markdown 为空未决', () => {
  const [rule] = buildHardRules({ requiredSkills: ['Vue', 'TypeScript'] })
  assert.ok(rule)
  assert.equal(rule!.judge(doc({}, '熟悉 Vue 与 TypeScript 开发')), true)
  assert.equal(rule!.judge(doc({}, '只会 Vue')), false)
  assert.equal(rule!.judge(doc({}, '  ')), null)
})

test('excludeKeywords：命中即失败', () => {
  const [rule] = buildHardRules({ excludeKeywords: ['外包'] })
  assert.ok(rule)
  assert.equal(rule!.judge(doc({}, '三年外包经验')), false)
  assert.equal(rule!.judge(doc({}, '自研产品经验')), true)
  assert.equal(rule!.judge(doc({}, '')), null)
})

test('多规则组合构建', () => {
  const rules = buildHardRules({ city: ['北京'], minYears: 3, requiredSkills: ['Vue'], excludeKeywords: ['外包'] })
  assert.equal(rules.length, 4)
})
