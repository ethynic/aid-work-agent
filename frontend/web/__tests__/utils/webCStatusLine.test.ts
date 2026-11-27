import { describe, expect, it } from 'vitest'
import { resolveWebCStatusLine, WEB_C_WORKING_FALLBACK_TEXT } from '@/utils/webCStatusLine'

const base = {
  streamingActive: true,
  isTerminal: false,
  streamingText: '',
  waitingText: '',
  streamFailed: false,
  progressText: '',
}

describe('resolveWebCStatusLine', () => {
  it('执行中且无工具进度时兜底显示正在思考（简单问答空窗）', () => {
    expect(resolveWebCStatusLine(base)).toBe(WEB_C_WORKING_FALLBACK_TEXT)
  })

  it('有工具进度时显示克制化进度文本', () => {
    expect(resolveWebCStatusLine({ ...base, progressText: '技能 doc-layout-clone 执行中' }))
      .toBe('技能 doc-layout-clone 执行中')
  })

  it('工具完成后 progress 清空时回落兜底文案（空窗消失问题）', () => {
    expect(resolveWebCStatusLine({ ...base, progressText: '' }))
      .toBe(WEB_C_WORKING_FALLBACK_TEXT)
  })

  it('正文出现后让位（返回空，由正文气泡 + 正在输入指示承担）', () => {
    expect(resolveWebCStatusLine({ ...base, streamingText: '部分回复' })).toBe('')
  })

  it('反问出现后让位（ask_user 停车等用户回复）', () => {
    expect(resolveWebCStatusLine({ ...base, waitingText: '请确认口径' })).toBe('')
  })

  it('终态后不再显示（消息入列由消息流承担）', () => {
    expect(resolveWebCStatusLine({ ...base, isTerminal: true })).toBe('')
    expect(resolveWebCStatusLine({ ...base, streamingActive: false })).toBe('')
  })

  it('失败态不显示兜底（由「本轮对话未完成」提示承担）', () => {
    expect(resolveWebCStatusLine({ ...base, streamFailed: true })).toBe('')
  })
})
