import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { desktopControllerKey } from '@desktop/app/context'
import DesktopLoginPage from '@desktop/pages/DesktopLoginPage.vue'

function mountLogin(overrides: Record<string, unknown> = {}, offline = false) {
  const auth = {
    captcha: vi.fn(async () => ({ success: true, captcha_id: 'captcha-1', svg_base64: 'PHN2Zy8+' })),
    login: vi.fn(async () => ({ token: 'secret-token', tenantId: 'tenant-1', tenantCode: 'ACME', user: { user_id: 'user-1', username: 'Ada' } })),
    restore: vi.fn(),
    logout: vi.fn(),
    ...overrides,
  }
  const controller = { auth, authenticated: vi.fn(), retryConnectivity: vi.fn() }
  const wrapper = mount(DesktopLoginPage, {
    props: { offline },
    attachTo: document.body,
    global: { provide: { [desktopControllerKey as symbol]: controller } },
  })
  return { wrapper, auth, controller }
}

async function fillValidForm(wrapper: VueWrapper) {
  await wrapper.get('#tenant-code').setValue('acme')
  await wrapper.get('#identifier').setValue('ada')
  await wrapper.get('#password').setValue('secret')
  await wrapper.get('#captcha').setValue('ABCD')
}

describe('Desktop login page', () => {
  it('associates validation errors and focuses the first invalid field', async () => {
    const { wrapper } = mountLogin()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('#tenant-code').attributes()).toMatchObject({ 'aria-invalid': 'true', 'aria-describedby': 'tenant-code-error' })
    expect(document.activeElement).toBe(wrapper.get('#tenant-code').element)
    wrapper.unmount()
  })

  it('authenticates only after login and encrypted credential persistence succeed', async () => {
    const { wrapper, auth, controller } = mountLogin()
    await flushPromises()
    await fillValidForm(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(auth.login).toHaveBeenCalledWith(expect.objectContaining({ tenant_code: 'ACME', captcha_id: 'captcha-1' }))
    expect(controller.authenticated).toHaveBeenCalledOnce()
    expect(wrapper.text()).not.toContain('secret-token')
    wrapper.unmount()
  })

  it('keeps API field failures accessible and does not authenticate', async () => {
    const error = Object.assign(new Error('验证码错误'), { fieldErrors: [{ field: 'captcha_code', message: '验证码错误' }] })
    const { wrapper, controller } = mountLogin({ login: vi.fn(async () => { throw error }) })
    await flushPromises()
    await fillValidForm(wrapper)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('#captcha').attributes('aria-describedby')).toBe('captcha-error')
    expect(document.activeElement).toBe(wrapper.get('#captcha').element)
    expect(controller.authenticated).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('disables network login while offline', async () => {
    const { wrapper, auth, controller } = mountLogin({}, true)
    expect(wrapper.get('[role="status"]').text()).toContain('恢复连接后再登录')
    expect(wrapper.get('button[type="submit"]').attributes()).toHaveProperty('disabled')
    expect(auth.captcha).not.toHaveBeenCalled()
    await wrapper.get('[role="status"] button').trigger('click')
    expect(controller.retryConnectivity).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
})
