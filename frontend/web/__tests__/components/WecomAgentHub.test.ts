import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import WecomAgentHub from '@/components/wecomConversation/WecomAgentHub.vue'

// 三个内嵌页面打桩（页面自身已有独立测试；此处只验壳的 Tab 编排）
vi.mock('@/components/wecomConversation/WecomConversationCenter.vue', () => ({
  default: { name: 'WecomConversationCenter', template: '<div data-testid="page-conversations" />' },
}))
vi.mock('@/components/wecomConversation/WecomConversationDaily.vue', () => ({
  default: { name: 'WecomConversationDaily', template: '<div data-testid="page-daily" />' },
}))
vi.mock('@/components/wecomConversation/WecomProxyTasks.vue', () => ({
  default: { name: 'WecomProxyTasks', template: '<div data-testid="page-proxy" />' },
}))

const routeState = vi.hoisted(() => ({
  route: { params: { tenant_id: 't1' } as Record<string, string> },
}))
const pushed = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({
  useRoute: () => routeState.route,
  useRouter: () => ({ push: pushed }),
}))

function mountHub() {
  return mount(WecomAgentHub)
}

beforeEach(() => {
  pushed.mockClear()
  routeState.route = { params: { tenant_id: 't1' } }
})

describe('WecomAgentHub（企业微信Agent 三入口合并壳）', () => {
  it('默认 Tab 为会话记录并渲染对应页面', () => {
    routeState.route = { params: { tenant_id: 't1' } }
    const wrapper = mountHub()
    expect(wrapper.find('[data-testid="wecom-agent-tab-conversations"]').classes())
      .toContain('bg-primary-50')
    expect(wrapper.find('[data-testid="page-conversations"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('企业微信Agent')
  })

  it('路由 tab=proxy 时高亮代理任务并内嵌代理页面', () => {
    routeState.route = { params: { tenant_id: 't1', tab: 'proxy' } }
    const wrapper = mountHub()
    expect(wrapper.find('[data-testid="wecom-agent-tab-proxy"]').classes())
      .toContain('bg-primary-50')
    expect(wrapper.find('[data-testid="page-proxy"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="page-conversations"]').exists()).toBe(false)
  })

  it('非法 tab 值回落会话记录', () => {
    routeState.route = { params: { tenant_id: 't1', tab: 'hacked' } }
    const wrapper = mountHub()
    expect(wrapper.find('[data-testid="page-conversations"]').exists()).toBe(true)
  })

  it('点击 Tab 按名跳转（携带租户与目标 tab）', async () => {
    routeState.route = { params: { tenant_id: 't1' } }
    const wrapper = mountHub()
    await wrapper.find('[data-testid="wecom-agent-tab-daily"]').trigger('click')
    expect(pushed).toHaveBeenCalledWith({
      name: 'tenant-wecom-agent',
      params: { tenant_id: 't1', tab: 'daily' },
    })
  })

  it('点击当前 Tab 不产生跳转', async () => {
    routeState.route = { params: { tenant_id: 't1' } }
    const wrapper = mountHub()
    await wrapper.find('[data-testid="wecom-agent-tab-conversations"]').trigger('click')
    expect(pushed).not.toHaveBeenCalled()
  })
})
