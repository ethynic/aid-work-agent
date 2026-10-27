import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MessageItem from '@/components/MessageItem.vue'
import { useDebugMode } from '@/composables/useDebugMode'
import type { ChatMessage } from '@/types'

vi.mock('@/composables/useAgent', () => ({ useAgent: () => ({ liveVerbose: { value: null } }) }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({}) }) }))

const TRACEBACK = 'Traceback (most recent call last): AttributeError: object has no attribute cursor'

function failedMessage(runnerActive: boolean): ChatMessage {
  return {
    role: 'assistant',
    content: '',
    messageId: 'm1',
    timestamp: Date.now(),
    runnerActive,
    progressMessages: [
      { type: 'tool_start', content: '', timestamp: 1, toolName: 'skill_execute', displayName: '技能 [ai-lead-gen]' },
      { type: 'tool_result', content: `技能 [ai-lead-gen]执行失败: ${TRACEBACK}`, timestamp: 2,
        toolName: 'skill_execute', displayName: '技能 [ai-lead-gen]', success: false, result: { error: TRACEBACK } },
    ],
  }
}

function expectNeutralFailure(wrapper: ReturnType<typeof mount>) {
  const text = wrapper.text()
  expect(text).toContain('技能 [ai-lead-gen]未完成')
  expect(text).not.toContain('❌')
  expect(text).not.toContain('执行失败')
  expect(text).not.toContain('Traceback')
  expect(wrapper.html()).not.toContain('text-danger')
  wrapper.unmount()
}

afterEach(() => {
  useDebugMode().isDebugEnabled.value = false
})

describe('失败步骤的过程反馈', () => {
  it('执行中只显示「未完成」，不显示叉号与错误详情', () => {
    const wrapper = mount(MessageItem, {
      props: { message: failedMessage(true), isProcessing: true, inputHintState: 'working' },
    })
    expectNeutralFailure(wrapper)
  })

  it('debug 执行详情同样不显示叉号与错误详情', () => {
    useDebugMode().isDebugEnabled.value = true
    const wrapper = mount(MessageItem, { props: { message: failedMessage(false) } })
    expectNeutralFailure(wrapper)
  })
})
