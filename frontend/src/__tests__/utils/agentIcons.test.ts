/**
 * agentIcons 工具函数单元测试
 *
 * 验证：
 * - 数组长度为 200（防止误删导致匹配范围变化）
 * - 稳定性：同一 agentId 多次调用返回相同结果
 * - 分布性：100 个虚拟 id 碰撞率 < 30%
 * - 空字符串 fallback 到第一个图标
 * - 数组元素均为非空字符串（防止空 path 导致渲染异常）
 */
import { describe, it, expect } from 'vitest'
import { AGENT_ICON_PATHS, getAgentIconPath } from '@/utils/agentIcons'

describe('agentIcons', () => {
  it('图标数组长度应为 200', () => {
    expect(AGENT_ICON_PATHS.length).toBe(200)
  })

  it('数组元素均为非空字符串', () => {
    for (let i = 0; i < AGENT_ICON_PATHS.length; i++) {
      const path = AGENT_ICON_PATHS[i]
      expect(typeof path).toBe('string')
      expect(path.length).toBeGreaterThan(0)
      // 每个 path 至少包含一个 M 或 m 命令（SVG path 起点）
      expect(/[Mm]/.test(path)).toBe(true)
    }
  })

  it('空字符串 agentId 回退到第一个图标', () => {
    expect(getAgentIconPath('')).toBe(AGENT_ICON_PATHS[0])
  })

  it('同一 agentId 多次调用应返回相同结果（稳定性）', () => {
    const ids = ['trade-specialist', 'travel-consultant', 'customer-followup', 'complaint', 'after-sales', 'main']
    for (const id of ids) {
      const first = getAgentIconPath(id)
      // 调用 10 次，结果应一致
      for (let i = 0; i < 10; i++) {
        expect(getAgentIconPath(id)).toBe(first)
      }
    }
  })

  it('100 个虚拟 agentId 的碰撞率应 < 30%（分布性）', () => {
    // 生成 100 个不同的虚拟 agentId
    const ids: string[] = []
    for (let i = 0; i < 100; i++) {
      ids.push(`agent-${i}-${Math.random().toString(36).slice(2, 8)}`)
    }
    // 收集每个 path 被命中的次数
    const pathCounts = new Map<string, number>()
    for (const id of ids) {
      const path = getAgentIconPath(id)
      pathCounts.set(path, (pathCounts.get(path) || 0) + 1)
    }
    // 碰撞率 = 1 - 不同 path 数 / 总 id 数
    // 100 个 id 落在 200 个 path 上，理论碰撞率较低
    const collisionRate = 1 - pathCounts.size / ids.length
    expect(collisionRate).toBeLessThan(0.3)
  })

  it('不同 agentId 应尽量返回不同 path（基础分布性）', () => {
    // 取 10 个差异较大的 agentId，至少应有 5 个不同 path
    const ids = [
      'a', 'b', 'c', 'd', 'e',
      'trade-specialist', 'travel-consultant', 'complaint-handler',
      'after-sales-service', 'customer-followup'
    ]
    const uniquePaths = new Set(ids.map(id => getAgentIconPath(id)))
    expect(uniquePaths.size).toBeGreaterThanOrEqual(5)
  })

  it('返回的 path 必定在 AGENT_ICON_PATHS 数组内', () => {
    const ids = ['', 'main', 'trade-specialist', 'unknown-agent', 'another-one']
    for (const id of ids) {
      const path = getAgentIconPath(id)
      expect(AGENT_ICON_PATHS).toContain(path)
    }
  })
})
