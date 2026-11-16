import { mount, flushPromises } from '@vue/test-utils'
import { ref } from 'vue'
import { beforeEach, expect, it, vi } from 'vitest'
import XiaohongshuConnector from '@/components/connections/XiaohongshuConnector.vue'
import { getXiaohongshuAction, getXiaohongshuDevice, startXiaohongshuAction } from '@/api/localTools'

vi.mock('@/api/localTools', () => ({
  getXiaohongshuAction: vi.fn(),
  getXiaohongshuDevice: vi.fn(),
  startXiaohongshuAction: vi.fn(),
}))
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({
    admin: ref({ user_id: 'user-a' }), isInitialized: ref(true), isLoggedIn: ref(true), logout: vi.fn(),
  }),
}))
vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => ({ params: { tenant_id: 'tenant-a' } }),
}))

beforeEach(() => vi.resetAllMocks())

it('扫码提示不声称登录成功，打开操作仍使用界面显示的设备', async () => {
  vi.mocked(getXiaohongshuDevice).mockResolvedValue({
    status: 'ready', device: { device_id: 'device-a', name: '办公电脑' },
  })
  vi.mocked(startXiaohongshuAction).mockResolvedValueOnce('login-id').mockResolvedValueOnce('open-id')
  vi.mocked(getXiaohongshuAction).mockResolvedValueOnce({ status: 'qr_ready' })
    .mockResolvedValueOnce({ status: 'failed', code: 'XHS_LOGIN_REQUIRED' })
  const wrapper = mount(XiaohongshuConnector, { global: { stubs: { AppHeader: true } } })
  try {
    await flushPromises()
    expect(wrapper.text()).toContain('办公电脑')
    const click = (label: string) => wrapper.findAll('button').find(button => button.text() === label)!.trigger('click')
    await click('扫码登录')
    await flushPromises()
    expect(wrapper.text()).toContain('二维码已显示在所选设备')
    expect(wrapper.text()).not.toContain('已用登录会话打开')
    await click('打开小红书')
    await flushPromises()
    expect(wrapper.text()).toContain('请先扫码登录')
    expect(vi.mocked(startXiaohongshuAction).mock.calls.map(args => args.slice(0, 2)))
      .toEqual([['login', 'device-a'], ['open', 'device-a']])
  } finally {
    wrapper.unmount()
  }
})
