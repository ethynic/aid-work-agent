/**
 * MessageItem 提交失败态渲染测试（docs/plans/plan-agent-runner-service.md M7 待办①）
 *
 * 业务承诺：
 * - POST /api/chat/runners 失败时，用户消息旁渲染明确错误态与重试入口，
 *   替换永远停留的「🚀 正在发送请求...」乐观消息
 * - 灰色简短错误提示，气泡旁的图标可用键盘重试或删除；未知受理不能删除
 * - 失败渲染块不受 runnerActive/debug 门控（普通用户可见）
 * - submitFailure 存在时不落入「对方正在输入中」输入占位
 */
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import MessageItem from '@/components/MessageItem.vue'
import { useDebugMode } from '@/composables/useDebugMode'
import type { ProgressMessage, SubmitFailureState } from '@/types'

// mock useAgent：liveVerbose 置空（真实实现为 per-session computed 代理）
vi.mock('@/composables/useAgent', () => ({
  useAgent: () => ({ liveVerbose: { value: null } }),
}))

vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ getAuthHeader: () => ({}) }),
}))

function mountFailed(failure: SubmitFailureState, extra: Record<string, unknown> = {}) {
  const progress: ProgressMessage[] = [{
    type: failure.kind === 'definitive' ? 'error' : 'progress',
    content: failure.kind === 'definitive' ? `❌ 发送失败：${failure.message}` : '⏳ 发送不稳定，正在自动重试（第 1 次）...',
    timestamp: Date.now(),
  }]
  const message = reactive({
    role: 'user' as const,
    content: '上海的',
    timestamp: Date.now(),
    progressMessages: progress,
    submitFailure: failure,
    ...extra,
  })
  const wrapper = mount(MessageItem, {
    props: {
      message,
      isProcessing: failure.kind === 'retrying',
      inputHintState: failure.kind === 'retrying' ? 'thinking' : 'idle',
    },
  })
  return { wrapper, message }
}

describe('MessageItem 提交失败态渲染', () => {
  it('definitive 失败渲染错误文案与重试按钮，点击触发幂等重试闭包', async () => {
    const retry = vi.fn()
    const { wrapper } = mountFailed({ kind: 'definitive', message: '积分不足，请充值后重试。', attempts: 0, retry })
    expect(wrapper.find('[role="alert"]').text()).toContain('消息未发送成功')
    expect(wrapper.find('[role="alert"]').text()).toBe('消息未发送成功')
    expect(wrapper.find('[role="alert"]').classes()).toContain('text-muted')
    const button = wrapper.find('button[aria-label="重试消息"]')
    expect(button.text()).toBe('')
    expect(button.attributes('title')).toBe('重试消息')
    await button.trigger('click')
    expect(retry).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('未知受理保留准确的确认提示和重试图标，不提供删除', async () => {
    const retry = vi.fn()
    const { wrapper } = mountFailed({ kind: 'retrying', message: '任务暂时无法继续，请稍后重试。', attempts: 2, retry })
    expect(wrapper.find('[role="alert"]').text()).toBe('发送状态确认中，正在自动重试')
    expect(wrapper.find('button[aria-label="删除失败消息"]').exists()).toBe(false)
    const button = wrapper.find('button[aria-label="重试消息"]')
    await button.trigger('click')
    expect(retry).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('失败态不显示「对方正在输入中」占位（处理中且正文为空也不显示）', () => {
    const { wrapper } = mountFailed({ kind: 'retrying', message: '任务暂时无法继续，请稍后重试。', attempts: 1, retry: vi.fn() })
    expect(wrapper.find('[aria-live="polite"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('对方正在输入中')
    wrapper.unmount()
  })

  it('失败渲染块不受 runnerActive 与 debug 门控', async () => {
    const debug = useDebugMode()
    const failure = (): SubmitFailureState => ({ kind: 'definitive', message: '积分不足，请充值后重试。', attempts: 0, retry: vi.fn() })
    debug.isDebugEnabled.value = false
    const gated = mountFailed(failure(), { runnerActive: true })
    expect(gated.wrapper.find('[role="alert"]').exists()).toBe(true)
    gated.wrapper.unmount()
    debug.isDebugEnabled.value = true
    try {
      const debugged = mountFailed(failure(), { runnerActive: true })
      expect(debugged.wrapper.find('[role="alert"]').exists()).toBe(true)
      debugged.wrapper.unmount()
    } finally {
      debug.isDebugEnabled.value = false
    }
  })

  it('无失败标记的普通消息不渲染失败块（回归）', () => {
    const wrapper = mount(MessageItem, {
      props: { message: { role: 'assistant' as const, content: '正常回复', timestamp: Date.now() }, isProcessing: false },
    })
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('提交失败的内部助手占位不渲染白框或时间戳', () => {
    const { wrapper } = mountFailed({ kind: 'definitive', message: '服务暂时不可用。', attempts: 0, retry: vi.fn() },
      { role: 'assistant', content: '' })
    expect(wrapper.find('.message-ai-wrapper').exists()).toBe(false)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('执行失败即使关闭 debug 也在用户消息旁显示重试，重试中禁用按钮', async () => {
    const retry = vi.fn()
    const message = reactive({ role: 'user' as const, content: '上海的', timestamp: Date.now(),
      runnerFailure: { message: '服务暂时不可用，请稍后重试。', retry, retrying: false, remove: vi.fn() } })
    const wrapper = mount(MessageItem, { props: { message } })
    expect(wrapper.find('[role="alert"]').text()).toContain('消息未发送成功')
    expect(wrapper.find('.message-ai-wrapper').exists()).toBe(false)
    await wrapper.find('button[aria-label="重试消息"]').trigger('click')
    expect(retry).toHaveBeenCalledTimes(1)
    message.runnerFailure.retrying = true
    await wrapper.vm.$nextTick()
    expect(wrapper.find('button[aria-label="正在处理失败消息"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('button[aria-label="删除失败消息"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('删除使用气泡旁的灰色图标，提供名称并调用原消息动作', async () => {
    const remove = vi.fn()
    const { wrapper } = mountFailed({ kind: 'definitive', message: '失败详情', attempts: 0, retry: vi.fn(), remove })
    const button = wrapper.find('button[aria-label="删除失败消息"]')
    expect(button.text()).toBe('')
    expect(button.classes().join(' ')).not.toContain('danger')
    expect(wrapper.text()).not.toContain('失败详情')
    await button.trigger('click')
    expect(remove).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
