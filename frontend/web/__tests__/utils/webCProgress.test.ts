import { describe, expect, it } from 'vitest'
import { restrainedProgress } from '@/utils/webCProgress'

/** webC 过程状态克制化：Runner snapshot.progress 文本 -> 访客单行状态位文案 */
describe('restrainedProgress', () => {
  it('保留常规过程短语并剥离前缀 emoji', () => {
    expect(restrainedProgress('🚀 正在调用AI报价子智能体处理任务...')).toBe('正在调用AI报价子智能体处理任务...')
    expect(restrainedProgress('📋 执行计划已创建')).toBe('执行计划已创建')
    expect(restrainedProgress('✅ 任务完成，正在生成回复...')).toBe('任务完成，正在生成回复...')
  })

  it('过滤失败类文案（失败保持中性不外显）', () => {
    expect(restrainedProgress('❌ 技能「web-search-pro」执行失败: 超时')).toBe('')
    expect(restrainedProgress('❌ 报价子智能体任务失败')).toBe('')
  })

  it('过滤澄清类文案（澄清由 waitingText 呈现，不重复展示）', () => {
    expect(restrainedProgress('❓ 需要澄清: 请问预算范围...')).toBe('')
  })

  it('非文本与空串返回空串', () => {
    expect(restrainedProgress(undefined)).toBe('')
    expect(restrainedProgress(null)).toBe('')
    expect(restrainedProgress(123)).toBe('')
    expect(restrainedProgress('')).toBe('')
    expect(restrainedProgress('   ')).toBe('')
  })

  it('超长文案截断到 40 字加省略号（防止 preview 撑爆单行）', () => {
    const long = '正在读取包含很多很多内容的长结果用于撑爆单行状态位测试测试测试测试测试测试测试测试测试测试'
    expect(long.length).toBeGreaterThan(40)
    const out = restrainedProgress(long)
    expect(out.length).toBe(41)
    expect(out.endsWith('…')).toBe(true)
  })

  it('纯 emoji 前缀剥离后不留残余空白', () => {
    expect(restrainedProgress('📦  已加载技能: 标书生成')).toBe('已加载技能: 标书生成')
  })
})
