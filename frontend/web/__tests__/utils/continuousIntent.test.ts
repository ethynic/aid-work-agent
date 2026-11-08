import { describe, expect, it } from 'vitest'
import { suggestsContinuousMode } from '@/utils/continuousIntent'

describe('Continuous intent prompts are suggestions, not authorization', () => {
  it('suggests a choice for clear long running actions', () => {
    expect(suggestsContinuousMode('你能从现在开始持续监控我和孙晨的对话并回复他的问题吗')).toBe(true)
    expect(suggestsContinuousMode('请每隔10秒检查企微新消息并处理')).toBe(true)
  })

  it('leaves discussion and one time requests on the ordinary path', () => {
    expect(suggestsContinuousMode('怎么设计持续监控企微消息的方案？')).toBe(false)
    expect(suggestsContinuousMode('请检查一次孙晨的企微消息')).toBe(false)
    expect(suggestsContinuousMode('不要持续监控，只回复这一次')).toBe(false)
  })
})
