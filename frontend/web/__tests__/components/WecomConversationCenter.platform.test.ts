import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import WecomConversationCenter from '@/components/wecomConversation/WecomConversationCenter.vue'

const api = vi.hoisted(() => ({ listConversations: vi.fn(), listMessages: vi.fn(), messagePageKey: vi.fn() }))
vi.mock('@/api/wecomConversation', () => api)
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({
  admin: { value: null }, isLoggedIn: { value: true }, logout: vi.fn(),
}) }))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }), useRouter: () => ({ push: vi.fn() }) }))

let wrapper: VueWrapper | null = null
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.clearAllMocks() })

describe('企微群聊消息的平台标记', () => {
  it('只标出已确认的微信发言人，企微及未知发言人没有平台标签', async () => {
    api.listConversations.mockResolvedValue({ conversations: [
      { id: 1, name: '项目群', kind: 'group', timezone: 'Asia/Shanghai', source_account_id: 'account-a' },
    ] })
    api.listMessages.mockResolvedValue({ messages: [
      { id: 3, source_message_id: 3, sender_name: '未核实', sender_platform: 'unknown', sender_side: 'peer', text: '第三条' },
      { id: 2, source_message_id: 2, sender_name: '企微同事', sender_platform: 'wecom', sender_side: 'peer', text: '第二条' },
      { id: 1, source_message_id: 1, sender_name: '微信客户', sender_platform: 'wechat', sender_side: 'peer', text: '第一条' },
    ], has_more: false })
    api.messagePageKey.mockReturnValue({ sent: 'infinity', id: 1 })

    wrapper = mount(WecomConversationCenter, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    const bubbles = wrapper.findAll('[data-source-msg-id]')
    expect(bubbles).toHaveLength(3)
    expect(bubbles[0]!.element.parentElement?.textContent).toContain('微信客户（微信用户）')
    expect(bubbles[1]!.element.parentElement?.textContent).toContain('企微同事')
    expect(bubbles[1]!.element.parentElement?.textContent).not.toContain('（企微）')
    expect(bubbles[2]!.element.parentElement?.textContent).toContain('未核实')
    expect(bubbles[2]!.element.parentElement?.textContent).not.toContain('（微信用户）')
  })
})
