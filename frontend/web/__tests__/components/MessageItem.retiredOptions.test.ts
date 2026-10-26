import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import MessageItem from '@/components/MessageItem.vue'

vi.mock('@/composables/useAgent', () => ({ useAgent: () => ({ liveVerbose: { value: null } }) }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ getAuthHeader: () => ({}) }) }))

describe('Retired option buttons', () => {
  it('renders a cached message body without reviving old labels or numeric send actions', () => {
    // Cached objects can outlive the mapper/type upgrade. The component must ignore the old field too.
    const message = { role: 'assistant' as const, content: '正常正文', quickOptions: [
      { key: 'one', label: '旧选项甲' }, { key: 'two', label: '旧选项乙' }] }
    const wrapper = mount(MessageItem, { props: { message } })
    expect(wrapper.text()).toContain('正常正文')
    expect(wrapper.text()).not.toContain('旧选项甲')
    expect(wrapper.text()).not.toContain('旧选项乙')
    expect(wrapper.emitted('send')).toBeUndefined()
    wrapper.unmount()
  })
})
