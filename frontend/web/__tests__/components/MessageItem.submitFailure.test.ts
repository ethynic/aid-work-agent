/**
 * MessageItem 提交失败态渲染测试（docs/plans/plan-agent-runner-service.md M7 待办①）
 *
 * 业务承诺：
 * - POST /api/chat/runners 失败时，乐观 assistant 消息原地渲染明确错误态与重试入口，
 *   替换永远停留的「🚀 正在发送请求...」乐观消息
 * - definitive：错误文案 + 「重试」按钮；retrying：自动重试次数 + 「立即重试」按钮
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
    role: 'assistant' as const,
    content: '',
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
    expect(wrapper.find('[role="alert"]').text()).toContain('发送失败：积分不足，请充值后重试。')
    const button = wrapper.find('[role="alert"] button')
    expect(button.text()).toBe('重试')
    await button.trigger('click')
    expect(retry).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('retrying 失败渲染自动重试次数与立即重试按钮', async () => {
    const retry = vi.fn()
    const { wrapper } = mountFailed({ kind: 'retrying', message: '任务暂时无法继续，请稍后重试。', attempts: 2, retry })
    expect(wrapper.find('[role="alert"]').text()).toContain('已自动重试 2 次')
    const button = wrapper.find('[role="alert"] button')
    expect(button.text()).toBe('立即重试')
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
})
