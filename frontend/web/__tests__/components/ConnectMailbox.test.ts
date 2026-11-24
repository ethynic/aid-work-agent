import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { ref } from 'vue'
import ConnectMailbox from '@/components/connections/ConnectMailbox.vue'
import ConnectMailboxDialog from '@/components/connections/ConnectMailboxDialog.vue'
import type { EmailSettingsConfig } from '@/api/connectMailbox'
import type { MailboxBrandKey } from '@/components/connections/mailboxBrands'

const mocks = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn(), del: vi.fn(), success: vi.fn(), error: vi.fn(), confirm: vi.fn(() => true) }))

// jsdom 未实现 Element.scrollIntoView，弹窗失败提示展开步骤时会调用
Element.prototype.scrollIntoView = vi.fn()

vi.mock('vue-router', () => ({ useRoute: () => ({ params: { tenant_id: 'tenant' } }), useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ admin: ref(null), isLoggedIn: ref(true), isInitialized: ref(true), init: vi.fn(), logout: vi.fn() }),
}))
vi.mock('vue-toastification', () => ({ useToast: () => ({ success: mocks.success, error: mocks.error }) }))
vi.mock('@/api/connectMailbox', () => ({ getEmailSettings: mocks.get, saveEmailSettings: mocks.save, deleteEmailSettings: mocks.del }))

function config(overrides: Partial<EmailSettingsConfig> = {}): EmailSettingsConfig {
  return {
    email_address: '987654321@qq.com',
    smtp_server: 'smtp.qq.com',
    smtp_port: 465,
    smtp_user: '987654321@qq.com',
    smtp_password: '****',
    smtp_encryption: 'ssl',
    imap_server: 'imap.qq.com',
    imap_port: 993,
    imap_encryption: 'ssl',
    ...overrides,
  }
}

let page: VueWrapper | undefined
let dialog: VueWrapper | undefined

async function mountPage(): Promise<VueWrapper> {
  page = mount(ConnectMailbox, { global: { stubs: { AppHeader: true, Teleport: true } } })
  await flushPromises()
  return page
}

function mountDialog(brand: MailboxBrandKey, current: EmailSettingsConfig | null = null): VueWrapper {
  dialog = mount(ConnectMailboxDialog, { props: { modelValue: true, brand, current }, global: { stubs: { Teleport: true } } })
  return dialog
}

function button(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find(b => b.text().trim() === label)!
}

function buttonContaining(wrapper: VueWrapper, label: string) {
  return wrapper.findAll('button').find(b => b.text().includes(label))!
}

function mainButton(wrapper: VueWrapper) {
  return wrapper.findAll('button').find(b => ['连接', '连接中…', '重试'].includes(b.text().trim()))!
}

function tile(wrapper: VueWrapper, brandName: string) {
  return wrapper.get(`button[aria-label="连接${brandName}"]`)
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.confirm.mockReturnValue(true)
  vi.stubGlobal('confirm', mocks.confirm)
  mocks.get.mockResolvedValue({ success: true, data: null, bound: false })
})

afterEach(() => {
  page?.unmount(); page = undefined
  dialog?.unmount(); dialog = undefined
  vi.unstubAllGlobals()
})

describe('连接邮箱页面', () => {
  it('未连接：显示两组品牌卡片，不出现预留品牌', async () => {
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('我的邮箱')
    for (const name of ['QQ邮箱', '163邮箱', '腾讯企业邮箱', '网易企业邮箱', '自定义邮箱']) {
      expect(wrapper.find(`button[aria-label="连接${name}"]`).exists()).toBe(true)
    }
    expect(wrapper.text()).not.toContain('Outlook')
    expect(wrapper.text()).not.toContain('iCloud')
  })

  it('加载失败：提示并可重试', async () => {
    mocks.get.mockRejectedValue(new Error('network down'))
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('邮箱信息加载失败')
    expect(wrapper.text()).toContain('请检查网络后重试。')
    expect(mocks.get).toHaveBeenCalledTimes(1)
    await button(wrapper, '重试').trigger('click')
    await flushPromises()
    expect(mocks.get).toHaveBeenCalledTimes(2)
  })

  it('查询接口返回 success:false：按加载失败处理，不显示成未连接', async () => {
    mocks.get.mockResolvedValue({ success: false, error: 'x', bound: false, data: null })
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('邮箱信息加载失败')
    expect(wrapper.find(`button[aria-label="连接QQ邮箱"]`).exists()).toBe(false)
  })

  it('QQ邮箱弹窗空表单提交：出现校验提示，不调用保存接口', async () => {
    const wrapper = await mountPage()
    await tile(wrapper, 'QQ邮箱').trigger('click')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('请输入邮箱账号')
    expect(wrapper.text()).toContain('请输入授权码')
    expect(mocks.save).not.toHaveBeenCalled()
  })

  it('QQ邮箱填写账号和授权码后连接：按 9 项保存、提示成功并重新加载', async () => {
    const wrapper = await mountPage()
    await tile(wrapper, 'QQ邮箱').trigger('click')
    await wrapper.get('#mailbox-account').setValue('987654321')
    expect(wrapper.text()).toContain('将连接 987654321@qq.com')
    await wrapper.get('#mailbox-secret').setValue('auth-code')
    mocks.save.mockResolvedValueOnce({ success: true, message: 'ok' })
    mocks.get.mockResolvedValueOnce({ success: true, data: config(), bound: true })
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(mocks.save).toHaveBeenCalledExactlyOnceWith({
      email_address: '987654321@qq.com',
      smtp_server: 'smtp.qq.com',
      smtp_port: 465,
      smtp_user: '987654321@qq.com',
      smtp_password: 'auth-code',
      smtp_encryption: 'ssl',
      imap_server: 'imap.qq.com',
      imap_port: 993,
      imap_encryption: 'ssl',
    })
    expect(mocks.success).toHaveBeenCalledWith('邮箱连接成功，测试邮件已发送至 987654321@qq.com')
    expect(mocks.get).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('已连接')
    expect(wrapper.text()).toContain('987654321@qq.com')
  })

  it('QQ邮箱弹窗填其他域名账号：提示只能连接 QQ邮箱，不调用保存接口', async () => {
    const wrapper = await mountPage()
    await tile(wrapper, 'QQ邮箱').trigger('click')
    await wrapper.get('#mailbox-account').setValue('a@163.com')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('这里只能连接 QQ邮箱，其他邮箱请选择对应的品牌或「自定义邮箱」')
    expect(mocks.save).not.toHaveBeenCalled()
  })

  it('已连接的腾讯企业邮箱点重新连接：地址带出、密码为空', async () => {
    mocks.get.mockResolvedValue({
      success: true,
      bound: true,
      data: config({ email_address: 'zhang.wei@minghua.com', smtp_server: 'smtp.exmail.qq.com', smtp_user: 'zhang.wei@minghua.com', imap_server: 'imap.exmail.qq.com' }),
    })
    const wrapper = await mountPage()
    await button(wrapper, '重新连接').trigger('click')
    expect((wrapper.get('#mailbox-address').element as HTMLInputElement).value).toBe('zhang.wei@minghua.com')
    expect((wrapper.get('#mailbox-secret').element as HTMLInputElement).value).toBe('')
  })

  it('已连接时点另一个品牌：弹窗顶部提示会替换当前邮箱', async () => {
    mocks.get.mockResolvedValue({ success: true, bound: true, data: config() })
    const wrapper = await mountPage()
    await tile(wrapper, '163邮箱').trigger('click')
    expect(wrapper.text()).toContain('连接成功后将替换当前已连接的 987654321@qq.com。')
  })

  it('断开连接：取消确认不请求；确认后删除并回到未连接', async () => {
    mocks.get.mockResolvedValue({ success: true, bound: true, data: config() })
    const wrapper = await mountPage()
    mocks.confirm.mockReturnValue(false)
    await button(wrapper, '断开连接').trigger('click')
    await flushPromises()
    expect(mocks.confirm).toHaveBeenCalledWith(expect.stringContaining('987654321@qq.com'))
    expect(mocks.del).not.toHaveBeenCalled()
    mocks.confirm.mockReturnValue(true)
    mocks.del.mockResolvedValue({ success: true, message: 'ok' })
    mocks.get.mockResolvedValue({ success: true, data: null, bound: false })
    await button(wrapper, '断开连接').trigger('click')
    await flushPromises()
    expect(mocks.del).toHaveBeenCalledTimes(1)
    expect(mocks.success).toHaveBeenCalledWith('已断开邮箱连接')
    expect(wrapper.text()).not.toContain('已连接')
    expect(wrapper.find(`button[aria-label="连接QQ邮箱"]`).exists()).toBe(true)
  })
})

describe('连接邮箱弹窗', () => {
  it('网易企业邮箱：第一套服务器失败后自动尝试第二套，连接成功', async () => {
    mocks.save.mockResolvedValueOnce({ success: false, error: 'x', error_code: 'auth' })
      .mockResolvedValueOnce({ success: true, message: 'ok' })
    const wrapper = mountDialog('qiye')
    await wrapper.get('#mailbox-address').setValue('zhang.wei@minghua.com')
    await wrapper.get('#mailbox-secret').setValue('code123')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(mocks.save).toHaveBeenCalledTimes(2)
    expect((mocks.save.mock.calls[0] as unknown[])[0]).toMatchObject({ smtp_server: 'smtp.qiye.163.com' })
    expect((mocks.save.mock.calls[1] as unknown[])[0]).toMatchObject({ smtp_server: 'smtphz.qiye.163.com' })
    expect(wrapper.emitted('connected')).toEqual([['zhang.wei@minghua.com']])
  })

  it('腾讯企业邮箱两套服务器全部失败：显示失败提示，点链接展开步骤，主按钮变重试', async () => {
    mocks.save.mockResolvedValueOnce({ success: false, error: 'e', error_code: 'auth' })
      .mockResolvedValueOnce({ success: false, error: 'e', error_code: 'timeout' })
    const wrapper = mountDialog('exmail')
    await wrapper.get('#mailbox-address').setValue('zhang.wei@minghua.com')
    await wrapper.get('#mailbox-secret').setValue('code123')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('连接失败：已尝试所有服务器，请检查填写内容')
    expect(wrapper.text()).toContain('查看填写和开通步骤')
    expect(mocks.save).toHaveBeenCalledTimes(2)
    const helpToggle = buttonContaining(wrapper, '怎么填写和开通？')
    expect(helpToggle.attributes('aria-expanded')).toBe('false')
    await buttonContaining(wrapper, '查看填写和开通步骤').trigger('click')
    expect(buttonContaining(wrapper, '怎么填写和开通？').attributes('aria-expanded')).toBe('true')
    expect(mainButton(wrapper).text()).toContain('重试')
  })

  it('全部失败后修改密码重试：请求未返回时不再显示旧的失败提示', async () => {
    mocks.save.mockResolvedValueOnce({ success: false, error: 'e', error_code: 'auth' })
      .mockResolvedValueOnce({ success: false, error: 'e', error_code: 'timeout' })
    const wrapper = mountDialog('exmail')
    await wrapper.get('#mailbox-address').setValue('zhang.wei@minghua.com')
    await wrapper.get('#mailbox-secret').setValue('code123')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('连接失败：已尝试所有服务器，请检查填写内容')
    mocks.save.mockReturnValueOnce(new Promise(() => {}))
    await wrapper.get('#mailbox-secret').setValue('new-code')
    await mainButton(wrapper).trigger('click')
    expect(wrapper.text()).not.toContain('连接失败：')
    await flushPromises()
  })

  it('自定义邮箱：默认只显示 4 个输入框，按默认端口保存；端口非法时报错不再请求', async () => {
    const wrapper = mountDialog('custom')
    for (const id of ['mailbox-address', 'mailbox-secret', 'mailbox-smtp-server', 'mailbox-imap-server']) {
      expect(wrapper.get(`#${id}`).isVisible()).toBe(true)
    }
    expect(wrapper.find('#mailbox-smtp-port').isVisible()).toBe(false)
    await wrapper.get('#mailbox-address').setValue('li.wei@contoso.com')
    await wrapper.get('#mailbox-secret').setValue('pw-secret')
    await wrapper.get('#mailbox-smtp-server').setValue('smtp.contoso.com')
    await wrapper.get('#mailbox-imap-server').setValue('imap.contoso.com')
    mocks.save.mockResolvedValueOnce({ success: true, message: 'ok' })
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(mocks.save).toHaveBeenCalledExactlyOnceWith({
      email_address: 'li.wei@contoso.com',
      smtp_server: 'smtp.contoso.com',
      smtp_port: 465,
      smtp_user: 'li.wei@contoso.com',
      smtp_password: 'pw-secret',
      smtp_encryption: 'ssl',
      imap_server: 'imap.contoso.com',
      imap_port: 993,
      imap_encryption: 'ssl',
    })
    await buttonContaining(wrapper, '高级设置').trigger('click')
    await wrapper.get('#mailbox-smtp-port').setValue('70000')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('请输入 1–65535 的端口号')
    expect(mocks.save).toHaveBeenCalledTimes(1)
  })

  it('尝试中：取消与主按钮禁用，关闭请求不向父组件传递', async () => {
    mocks.save.mockReturnValue(new Promise(() => {}))
    const wrapper = mountDialog('qq')
    await wrapper.get('#mailbox-account').setValue('987654321')
    await wrapper.get('#mailbox-secret').setValue('code')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(button(wrapper, '取消').attributes('disabled')).toBeDefined()
    expect(mainButton(wrapper).text()).toContain('连接中…')
    await wrapper.get('button[aria-label="关闭对话框"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('全部失败且为网络类错误：提示标题为超时或网络异常', async () => {
    mocks.save.mockResolvedValue({ success: false, error: 'e', error_code: 'connect' })
    const wrapper = mountDialog('qq')
    await wrapper.get('#mailbox-account').setValue('987654321')
    await wrapper.get('#mailbox-secret').setValue('code')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('连接失败：连接邮箱服务器超时或网络异常')
    expect(mocks.save).toHaveBeenCalledTimes(1)
  })

  it('QQ邮箱授权失败：失败提示附「查看获取步骤」，点击后展开获取步骤且失败提示保留', async () => {
    mocks.save.mockResolvedValueOnce({ success: false, error: 'x', error_code: 'auth' })
    const wrapper = mountDialog('qq')
    await wrapper.get('#mailbox-account').setValue('987654321')
    await wrapper.get('#mailbox-secret').setValue('wrong-code')
    await mainButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('连接失败：授权码不正确，或未开启 IMAP/SMTP 服务')
    expect(buttonContaining(wrapper, '如何获取授权码？').attributes('aria-expanded')).toBe('false')
    await button(wrapper, '查看获取步骤').trigger('click')
    expect(buttonContaining(wrapper, '如何获取授权码？').attributes('aria-expanded')).toBe('true')
    expect(wrapper.text()).toContain('连接失败：授权码不正确，或未开启 IMAP/SMTP 服务')
  })

  it('关闭弹窗后清空表单：重新打开时账号与密码输入框为空', async () => {
    const wrapper = mountDialog('qq')
    await wrapper.get('#mailbox-account').setValue('987654321')
    await wrapper.get('#mailbox-secret').setValue('code')
    await wrapper.setProps({ modelValue: false })
    await wrapper.setProps({ modelValue: true })
    expect((wrapper.get('#mailbox-account').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('#mailbox-secret').element as HTMLInputElement).value).toBe('')
  })
})
