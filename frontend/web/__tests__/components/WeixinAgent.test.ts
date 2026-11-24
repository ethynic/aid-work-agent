import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createWeixinSummary, getWeixinSummary, listDevices } from '@/api/localTools'
import WeixinAgent from '@/components/weixinConversation/WeixinDailySummary.vue'

vi.mock('@/api/localTools', () => ({
  createWeixinSummary: vi.fn(),
  getWeixinSummary: vi.fn(),
  listDevices: vi.fn(),
}))
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ admin: null, isLoggedIn: true, logout: vi.fn() }),
}))

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(listDevices).mockResolvedValue([{
    device_id: 'device-1', name: '测试电脑', platform: 'windows', runtime_version: '1.3.1',
    selected: true, status: 'active', online: true, last_seen_at: null, created_at: null,
  }])
})

async function mountPage(query = '') {
  const emptyPage = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/t/:tenant_id/weixin-agent', component: emptyPage },
      { path: '/t/:tenant_id/local-tools', name: 'tenant-local-tools', component: emptyPage },
    ],
  })
  await router.push('/t/test/weixin-agent' + query)
  const wrapper = mount(WeixinAgent, {
    global: { plugins: [router], stubs: { AppHeader: true }, provide: { toggleSidebar: vi.fn() } },
  })
  await flushPromises()
  return wrapper
}

describe('微信 Agent', () => {
  it('waits for consent, submits the exact group name, and shows partial summary coverage', async () => {
    vi.mocked(createWeixinSummary).mockResolvedValue({
      id: 'run-1', state: 'queued', business_date: '2026-11-23',
    })
    vi.mocked(getWeixinSummary).mockResolvedValue({
      id: 'run-1', state: 'succeeded', business_date: '2026-11-23',
      target_name: '项目交付群', partial: true, summary: '周五前确认交付安排。', message_count: 2,
    })
    const wrapper = await mountPage()
    try {
      expect(wrapper.text()).toContain('测试电脑 · 在线')
      await wrapper.get('#weixin-group-name').setValue('  项目交付群  ')
      await wrapper.get('form').trigger('submit')
      expect(createWeixinSummary).not.toHaveBeenCalled()

      await wrapper.get('input[type="checkbox"]').setValue(true)
      await wrapper.get('form').trigger('submit')
      await flushPromises()
      expect(createWeixinSummary).toHaveBeenCalledWith('  项目交付群  ', 'device-1', expect.any(AbortSignal))
      expect(getWeixinSummary).toHaveBeenCalledWith('run-1', expect.any(AbortSignal))
      expect(wrapper.text()).toContain('项目交付群的总结')
      expect(wrapper.text()).toContain('周五前确认交付安排。')
      expect(wrapper.text()).toContain('只总结了可确认属于今天的部分消息')
      expect(wrapper.text()).toContain('使用 2 条文字消息')
      expect(wrapper.get('input[type="checkbox"]').element).toHaveProperty('checked', false)
    } finally {
      wrapper.unmount()
    }
  })

  it('requires fresh consent when the intended group or displayed device changes', async () => {
    const wrapper = await mountPage()
    try {
      await wrapper.get('#weixin-group-name').setValue('项目交付群')
      await wrapper.get('input[type="checkbox"]').setValue(true)
      await wrapper.get('#weixin-group-name').setValue('项目验收群')
      expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()

      await wrapper.get('input[type="checkbox"]').setValue(true)
      vi.mocked(listDevices).mockResolvedValue([{
        device_id: 'device-2', name: '另一台电脑', platform: 'windows', runtime_version: '1.3.1',
        selected: true, status: 'active', online: true, last_seen_at: null, created_at: null,
      }])
      await wrapper.findAll('button').find(button => button.text() === '刷新设备')!.trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('另一台电脑')
      expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
      expect(createWeixinSummary).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('loads the prior run after refresh and distinguishes empty history from failure', async () => {
    vi.mocked(getWeixinSummary).mockResolvedValue({
      id: 'run-1', state: 'succeeded', business_date: '2026-11-23', summary: null, message_count: 0,
    })
    const wrapper = await mountPage('?run=run-1')
    try {
      expect(wrapper.text()).toContain('没有可以总结的当天文字消息')
      expect(wrapper.find('[role="alert"]').exists()).toBe(false)
      expect(createWeixinSummary).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('lets a user start a new summary if a stale link cannot load any run', async () => {
    vi.mocked(getWeixinSummary).mockRejectedValue(new Error('总结记录不存在'))
    const wrapper = await mountPage('?run=stale')
    try {
      await wrapper.get('#weixin-group-name').setValue('项目交付群')
      await wrapper.get('input[type="checkbox"]').setValue(true)
      expect(wrapper.get('[role="alert"]').text()).toBe('总结记录不存在')
      expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    } finally {
      wrapper.unmount()
    }
  })
})
