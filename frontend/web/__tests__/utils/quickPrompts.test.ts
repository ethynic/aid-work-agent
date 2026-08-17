import { describe, it, expect } from 'vitest'
import { quickPromptsForSubagent } from '../../utils/quickPrompts'

describe('quickPromptsForSubagent', () => {
  it('recruiting-operator 返回「筛选简历」快捷按钮（一键演示闭环入口）', () => {
    const prompts = quickPromptsForSubagent('recruiting-operator')
    expect(prompts.length).toBeGreaterThanOrEqual(1)
    expect(prompts[0]!.label).toBe('筛选简历')
    expect(prompts[0]!.message).toBe('帮我筛选简历')
  })

  it('未配置的 subagent 返回空数组（不渲染）', () => {
    expect(quickPromptsForSubagent('travel-consultant')).toEqual([])
  })

  it('null/undefined 返回空数组', () => {
    expect(quickPromptsForSubagent(null)).toEqual([])
    expect(quickPromptsForSubagent(undefined)).toEqual([])
  })
})
