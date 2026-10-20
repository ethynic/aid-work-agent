import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageItem from '@/components/MessageItem.vue'
import { runnerMessages } from '@/utils/runnerMessages'
import { runnerView } from '../mocks/runnerView'

vi.mock('@/composables/useAgent', () => ({ useAgent: () => ({ liveVerbose: { value: null } }) }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({}) }) }))

describe('Interrupted Runner actual message rendering', () => {
  it.each(['interrupted', 'waiting'] as const)('renders %s without assistant body and removes typing placeholder', (status) => {
    const row = runnerView({ status, snapshot: { output: '',
      waiting: status === 'waiting' ? { kind: 'verification' } as any : undefined,
      progressMessages: [{ type: 'progress', content: '成功读取 3 份简历' }] } })
    const message = runnerMessages(row).slice(-1)[0]!
    const wrapper = mount(MessageItem, { props: { message, isProcessing: true, inputHintState: 'working' } })
    expect(message.content).toBe('')
    expect(wrapper.get('[role="status"]').text()).toContain(status === 'interrupted' ? '任务已中断' : '结果需要核对')
    expect(wrapper.text()).not.toContain('对方正在输入中')
    expect(wrapper.text()).not.toContain('任务完成')
    wrapper.unmount()
  })
})
