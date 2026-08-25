import { describe, it, expect } from 'vitest'
import { greetingForSubagent, DEFAULT_GREETING } from '../../utils/sessionGreetings'

describe('greetingForSubagent', () => {
  it('已配置的智能体返回专属摘要与 2 个快捷按钮', () => {
    const g = greetingForSubagent('recruiting-operator')
    expect(g).not.toBeNull()
    expect(g!.summary.length).toBeGreaterThan(0)
    expect(g!.prompts.length).toBe(2)
  })

  it('每个已配置智能体都有非空摘要且恰有 2 个快捷按钮', () => {
    const types = [
      'recruiting-operator', 'video-agent', 'trade-specialist', 'travel-consultant',
      'after-sales', 'competitor-research', 'complaint-handling', 'contract-archive-review',
      'customer-followup', 'order-processing', 'pre-sales', 'social-media-operations',
    ]
    for (const type of types) {
      const g = greetingForSubagent(type)
      expect(g, `subagent ${type} 应有空态配置`).not.toBeNull()
      expect(g!.summary.trim().length).toBeGreaterThan(0)
      expect(g!.prompts.length).toBe(2)
      for (const p of g!.prompts) {
        expect(p.label.trim().length).toBeGreaterThan(0)
        expect(p.message.trim().length).toBeGreaterThan(0)
      }
    }
  })

  it('未配置的 subagent 返回 null（由调用方兜底）', () => {
    expect(greetingForSubagent('unknown-agent')).toBeNull()
  })

  it('null/undefined 返回 null', () => {
    expect(greetingForSubagent(null)).toBeNull()
    expect(greetingForSubagent(undefined)).toBeNull()
  })

  it('DEFAULT_GREETING 提供通用摘要与通用快捷按钮（主智能体兜底）', () => {
    expect(DEFAULT_GREETING.summary.length).toBeGreaterThan(0)
    expect(DEFAULT_GREETING.prompts.length).toBeGreaterThanOrEqual(1)
  })
})
