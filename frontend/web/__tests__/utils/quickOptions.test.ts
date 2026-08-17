import { describe, it, expect } from 'vitest'
import { extractQuickOptions } from '../../utils/quickOptions'

/** boss_jobs_list 成功结果形状（proxy_tool BossJobsListTool.execute 返回值节选） */
const bossJobsListResult = {
  success: true,
  code: null,
  message: '当前在招职位 2 个：PHP开发工程师（Laravel）、Java后端工程师',
  data: {
    jobs: [],
    options: [
      { key: 'job-1', label: 'PHP开发工程师（Laravel）', description: '要求 3-5年/本科 · 简历 3 · 匹配 2' },
      { key: 'job-2', label: 'Java后端工程师', description: '要求未配置 · 简历 0 · 匹配 0' },
    ],
  },
}

describe('extractQuickOptions', () => {
  it('合法 options（≥2 项）原样提取（boss_jobs_list 结果）', () => {
    const options = extractQuickOptions(bossJobsListResult)
    expect(options).toHaveLength(2)
    expect(options[0]!.key).toBe('job-1')
    expect(options[0]!.label).toBe('PHP开发工程师（Laravel）')
    expect(options[1]!.description).toBe('要求未配置 · 简历 0 · 匹配 0')
  })

  it('无 options（空 active 的 boss_jobs_list 不带 options 键）返回空数组', () => {
    expect(extractQuickOptions({ success: true, data: { jobs: [] } })).toEqual([])
    expect(extractQuickOptions({ success: true })).toEqual([])
  })

  it('仅 1 项返回空数组（SUBAGENT 约定单选项直用不列单）', () => {
    const result = {
      success: true,
      data: { options: [{ key: 'job-1', label: '唯一职位' }] },
    }
    expect(extractQuickOptions(result)).toEqual([])
  })

  it('失败结果（success=false）返回空数组', () => {
    const failed = {
      ...bossJobsListResult,
      success: false,
      code: 'FAILED',
      message: '职位库查询失败',
    }
    expect(extractQuickOptions(failed)).toEqual([])
  })

  it('形状不符（key/label 非字符串或缺失）逐项过滤', () => {
    const result = {
      success: true,
      data: {
        options: [
          { key: 'job-1', label: '正常项A' },
          { key: 'job-2', label: '正常项B' },
          { label: '缺 key' },
          { key: '', label: '空 key' },
          { key: 'job-5', label: 123 },
          null,
          'not-an-object',
        ],
      },
    }
    // 非法项被过滤，剩余 2 项合法 → 挂载
    expect(extractQuickOptions(result)).toEqual([
      { key: 'job-1', label: '正常项A' },
      { key: 'job-2', label: '正常项B' },
    ])
  })

  it('过滤后仅剩 1 项合法 → 整体不挂载（<2 项约定）', () => {
    const result = {
      success: true,
      data: {
        options: [{ key: 'job-1', label: '唯一合法项' }, { label: '缺 key' }],
      },
    }
    expect(extractQuickOptions(result)).toEqual([])
  })

  it('非对象入参返回空数组', () => {
    expect(extractQuickOptions(null)).toEqual([])
    expect(extractQuickOptions(undefined)).toEqual([])
    expect(extractQuickOptions('string')).toEqual([])
  })
})
