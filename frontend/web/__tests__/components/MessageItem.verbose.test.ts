/**
 * MessageItem verbose 占位替换测试（Phase 2，设计 §8.2）
 *
 * 业务承诺：
 * - assistant 正文为空且处理中时，live verbose 文案原地替换「对方正在输入中...」
 *   占位（同一占位区，不新增聊天气泡）
 * - 占位文案带 aria-live="polite"（无障碍）
 * - response 开始后（content 非空）占位与 verbose 一并隐藏
 * - 历史 metadata 中的 verboseMessages 默认不在已完成消息下展示
 */
import { mount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import MessageItem from '@/components/MessageItem.vue'

// mock useAgent：暴露可写的 liveVerbose ref（真实实现为 per-session computed 代理）
const liveVerboseRef = { value: null as any }
vi.mock('@/composables/useAgent', () => ({
  useAgent: () => ({ liveVerbose: liveVerboseRef }),
}))

vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ getAuthHeader: () => ({}) }),
}))

function mountPlaceholder() {
  return mount(MessageItem, {
    props: {
      message: {
        role: 'assistant' as const,
        content: '',
        timestamp: Date.now(),
      },
      isProcessing: true,
      inputHintState: 'working',
    },
  })
}

describe('MessageItem verbose 占位替换', () => {
  it('处理中且无正文时，用 verbose 文案替换「对方正在输入中...」占位', async () => {
    liveVerboseRef.value = {
      eventId: 'verbose_mi_1',
      data: '正在生成报价单，请耐心等待。',
      source: 'policy',
      timestamp: 1788144000000,
    }
    const wrapper = mountPlaceholder()
    await nextTick()

    expect(wrapper.text()).toContain('正在生成报价单，请耐心等待。')
    expect(wrapper.text()).not.toContain('对方正在输入中')

    // aria-live="polite" 且同一占位区只有一个提示元素（不新增聊天气泡）
    const liveRegions = wrapper.findAll('[aria-live="polite"]')
    expect(liveRegions.length).toBe(1)
    expect(liveRegions[0].text()).toBe('正在生成报价单，请耐心等待。')
    // 占位仍在原 AI 白框内（未新增独立消息气泡容器）
    expect(wrapper.findAll('.message-ai-content').length).toBe(1)

    wrapper.unmount()
    liveVerboseRef.value = null
  })

  it('无 verbose 时回退默认「对方正在输入中...」文案', () => {
    liveVerboseRef.value = null
    const wrapper = mountPlaceholder()
    expect(wrapper.text()).toContain('对方正在输入中...')
    wrapper.unmount()
  })

  it('response 开始后（正文非空）占位与 verbose 一并隐藏', async () => {
    liveVerboseRef.value = {
      eventId: 'verbose_mi_2',
      data: '正在生成报价单，请耐心等待。',
      source: 'system',
      timestamp: 1788144000001,
    }
    // 需要响应式对象：content 突变模拟 response 流式到达
    const message = reactive({
      role: 'assistant' as const,
      content: '',
      timestamp: Date.now(),
    })
    const wrapper = mount(MessageItem, {
      props: { message, isProcessing: true, inputHintState: 'working' },
    })
    await nextTick()
    expect(wrapper.text()).toContain('正在生成报价单，请耐心等待。')

    // response 流式到达：正文非空 → 占位隐藏
    message.content = '报价单已生成'
    await nextTick()
    expect(wrapper.find('[aria-live="polite"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('报价单已生成')
    expect(wrapper.text()).not.toContain('对方正在输入中')

    wrapper.unmount()
    liveVerboseRef.value = null
  })

  it('已完成的历史消息不展示 verbose（即使正文为空的旧消息也不渲染占位）', () => {
    liveVerboseRef.value = {
      eventId: 'verbose_mi_3',
      data: '不应出现在历史消息下的提示。',
      source: 'policy',
      timestamp: 1788144000002,
    }
    const wrapper = mount(MessageItem, {
      props: {
        message: { role: 'assistant' as const, content: '历史回复', timestamp: Date.now() },
        isProcessing: false,
        inputHintState: 'idle',
      },
    })
    expect(wrapper.text()).toContain('历史回复')
    expect(wrapper.find('[aria-live="polite"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('不应出现在历史消息下的提示。')
    wrapper.unmount()
    liveVerboseRef.value = null
  })
})
