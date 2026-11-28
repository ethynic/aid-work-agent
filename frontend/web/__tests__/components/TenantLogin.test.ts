import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TenantLogin from '@/components/saas/TenantLogin.vue'

const {
  mockPush,
  mockRoute,
  mockGetCaptcha,
  mockAdminPasswordLogin,
  mockGetTenantPublicInfo,
} = vi.hoisted(() => ({
  mockPush: vi.fn(),
  mockRoute: {
    path: '/t/tenant_demo/login',
    params: { tenant_id: 'tenant_demo' },
    query: {},
  },
  mockGetCaptcha: vi.fn(),
  mockAdminPasswordLogin: vi.fn(),
  mockGetTenantPublicInfo: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: mockPush }),
}))

vi.mock('vue-toastification', () => ({
  useToast: () => ({ warning: vi.fn() }),
}))

vi.mock('@/api/auth', () => ({
  getCaptcha: mockGetCaptcha,
}))

vi.mock('@/api/saasTenant', () => ({
  adminPasswordLogin: mockAdminPasswordLogin,
  getTenantPublicInfo: mockGetTenantPublicInfo,
}))

vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ setLogin: vi.fn() }),
}))

vi.mock('@/composables/useAgent', () => ({
  useAgent: () => ({
    clearSessionCache: vi.fn(),
    clearSession: vi.fn(),
    clearAttachments: vi.fn(),
  }),
}))

vi.mock('@/composables/useSession', () => ({
  useSession: () => ({ clearSessionCache: vi.fn() }),
}))

vi.mock('@/composables/useSubagentList', () => ({
  useSubagentList: () => ({ clearCache: vi.fn() }),
}))

describe('TenantLogin password visibility toggle', () => {
  beforeEach(() => {
    mockPush.mockReset()
    mockGetCaptcha.mockResolvedValue({ success: true, captcha_id: 'cap-1', svg_base64: '' })
    mockGetTenantPublicInfo.mockResolvedValue({
      success: true,
      tenant: { company_name: '爱定义AI数字员工顾问', logo_url: null, status: 'active' },
    })
    mockAdminPasswordLogin.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('masks password by default and toggles visibility on click without submitting', async () => {
    const wrapper = mount(TenantLogin, {
      global: {
        stubs: {
          RouterLink: { template: '<a><slot /></a>' },
          'router-link': { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    const passwordInput = wrapper.get('input[autocomplete="current-password"]')
    expect(passwordInput.attributes('type')).toBe('password')

    const showToggle = wrapper.get('button[aria-label="显示密码"]')
    expect(showToggle.attributes('type')).toBe('button')
    expect(showToggle.attributes('aria-pressed')).toBe('false')

    await showToggle.trigger('click')
    expect(passwordInput.attributes('type')).toBe('text')
    const hideToggle = wrapper.get('button[aria-label="隐藏密码"]')
    expect(hideToggle.attributes('aria-pressed')).toBe('true')
    expect(mockAdminPasswordLogin).not.toHaveBeenCalled()

    await hideToggle.trigger('click')
    expect(passwordInput.attributes('type')).toBe('password')
    expect(wrapper.get('button[aria-label="显示密码"]').attributes('aria-pressed')).toBe('false')
    expect(mockAdminPasswordLogin).not.toHaveBeenCalled()

    wrapper.unmount()
  })
})
