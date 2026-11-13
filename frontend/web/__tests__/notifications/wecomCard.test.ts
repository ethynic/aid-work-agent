import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
const status = vi.hoisted(() => vi.fn())
const preferences = vi.hoisted(() => vi.fn())
const suggest = vi.hoisted(() => vi.fn())
const bindUrl = vi.hoisted(() => vi.fn())
const bindConfirm = vi.hoisted(() => vi.fn())
const setPreference = vi.hoisted(() => vi.fn())
const unbind = vi.hoisted(() => vi.fn())
// 企微卡片只用 notificationsAPI（Phase 4 封装），mock 掉出站即可
vi.mock('@/api/notifications', () => ({
  notificationsAPI: {
    wecomBindStatus: status,
    preferences,
    wecomBindSuggest: suggest,
    wecomBindUrl: bindUrl,
    wecomBindConfirm: bindConfirm,
    wecomSetPreference: setPreference,
    wecomBindUnbind: unbind,
  },
}))
import WecomTaskNotifyCard from '@/notifications/WecomTaskNotifyCard.vue'

const availableUnbound = { app_available: true, bound: false, name: null, target_state: null }
const availableBound = { app_available: true, bound: true, name: '张三', target_state: 'active' }

function makeCard() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
  return mount(WecomTaskNotifyCard, { global: { plugins: [router], stubs: { teleport: true } } })
}

beforeEach(() => {
  status.mockReset(); preferences.mockReset(); suggest.mockReset(); bindUrl.mockReset()
  bindConfirm.mockReset(); setPreference.mockReset(); unbind.mockReset()
  preferences.mockResolvedValue({ preferences: [] })
  localStorage.clear(); sessionStorage.clear()
})

afterEach(() => vi.useRealTimers())

describe('wecom task notify card', () => {
  it('hides actions when the wecom app is unavailable', async () => {
    status.mockResolvedValue({ ...availableBound, app_available: false })
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('服务端暂未开放企微任务通知')
    expect(wrapper.text()).not.toContain('开启企微通知')
    expect(status).toHaveBeenCalled()
  })

  it('prefills phone suggestion and confirms bind on user action', async () => {
    status.mockResolvedValue(availableUnbound)
    suggest.mockResolvedValue({ matched: true, userid: 'zhangsan', name: '张三' })
    bindConfirm.mockResolvedValue({ success: true, userid: 'zhangsan', name: '张三', target_registered: true })
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('识别到企微账号：张三')
    await wrapper.findAll('button').find(b => b.text().includes('确认绑定'))!.trigger('click')
    await flushPromises()
    expect(bindConfirm).toHaveBeenCalledWith('zhangsan')
    expect(wrapper.text()).toContain('已绑定企微账号「张三」')
  })

  it('maps bind error codes to safe copy', async () => {
    status.mockResolvedValue(availableUnbound)
    suggest.mockResolvedValue({ matched: true, userid: 'zhangsan', name: '张三' })
    bindConfirm.mockRejectedValue(Object.assign(new Error('NOTIFICATION_BIND_TAKEN'), { code: 'NOTIFICATION_BIND_TAKEN' }))
    const wrapper = makeCard()
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text().includes('确认绑定'))!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('该企微账号已被其他账号绑定')
    expect(wrapper.text()).not.toContain('NOTIFICATION_BIND_TAKEN')
  })

  it('warns instead of optimistic success when target registration fails', async () => {
    status.mockResolvedValue(availableUnbound)
    suggest.mockResolvedValue({ matched: true, userid: 'zhangsan', name: '张三' })
    bindConfirm.mockResolvedValue({ success: true, userid: 'zhangsan', name: '张三', target_registered: false })
    const wrapper = makeCard()
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text().includes('确认绑定'))!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('绑定成功，但通知目标登记失败')
    expect(wrapper.text()).not.toContain('已绑定企微账号「张三」')
  })

  it('opens the wecom authorize page in a new window on PC instead of rendering a QR code', async () => {
    status.mockResolvedValue(availableUnbound)
    bindUrl.mockResolvedValue({ success: true, state: 's', oauth_url: 'https://open.weixin.qq.com/connect/oauth2/authorize',
      qr_url: 'https://open.work.weixin.qq.com/wwopen/sso/qrConnect' })
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const wrapper = makeCard()
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '绑定企微')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('在企微中扫码确认后本页将自动完成绑定')
    expect(wrapper.text()).not.toContain('企微绑定二维码')
    await wrapper.findAll('button').find(b => b.text() === '打开企微授权页')!.trigger('click')
    expect(open).toHaveBeenCalledWith('https://open.work.weixin.qq.com/wwopen/sso/qrConnect', '_blank', 'noopener')
    open.mockRestore()
  })

  it('stores bind context per state so concurrent tabs do not overwrite each other', async () => {
    // 复审 2：两个标签页各拿不同 state，上下文按 state 分键互不覆盖
    status.mockResolvedValue(availableUnbound)
    const bindInfo = (state: string) => ({ success: true, state, oauth_url: 'https://oauth', qr_url: 'https://qr' })
    bindUrl.mockResolvedValueOnce(bindInfo('s1'))
    bindUrl.mockResolvedValueOnce(bindInfo('s2'))
    const first = makeCard()
    await flushPromises()
    await first.findAll('button').find(b => b.text() === '绑定企微')!.trigger('click')
    await flushPromises()
    const second = makeCard()
    await flushPromises()
    await second.findAll('button').find(b => b.text() === '绑定企微')!.trigger('click')
    await flushPromises()
    expect(JSON.parse(localStorage.getItem('wecom-bind-context:s1')!).token_key).toBe('saas_token')
    expect(JSON.parse(localStorage.getItem('wecom-bind-context:s2')!).token_key).toBe('saas_token')
    expect(localStorage.getItem('wecom-bind-context')).toBeNull()
  })

  it('stores the initiating tenant from the tenant-scoped route', async () => {
    // 复审 3：租户设置页发起时存租户段，回调页据此带 X-Tenant-Id
    status.mockResolvedValue(availableUnbound)
    bindUrl.mockResolvedValue({ success: true, state: 's', oauth_url: 'https://oauth', qr_url: 'https://qr' })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/t/:tenant_id/notifications', component: { template: '<div />' }, children: [{ path: '', component: { template: '<div />' } }] },
      { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
    ] })
    await router.push('/t/tenant-9/notifications'); await router.isReady()
    const wrapper = mount(WecomTaskNotifyCard, { global: { plugins: [router], stubs: { teleport: true } } })
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '绑定企微')!.trigger('click')
    await flushPromises()
    const context = JSON.parse(localStorage.getItem('wecom-bind-context:s')!)
    expect(context.tenant).toBe('tenant-9')
    expect(context.return_path).toBe('/t/tenant-9/notifications')
  })

  it('enables and disables the wecom preference when bound', async () => {
    status.mockResolvedValue(availableBound)
    setPreference.mockResolvedValue({ category: 'task', channel: 'wecom_app', provider: 'wecom_app', enabled: true, enabled_since: null })
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('已绑定，通知未开启')
    await wrapper.findAll('button').find(b => b.text() === '开启企微通知')!.trigger('click')
    await flushPromises()
    expect(setPreference).toHaveBeenCalledWith(true)
    expect(wrapper.text()).toContain('企微通知已开启')
    setPreference.mockResolvedValue({ category: 'task', channel: 'wecom_app', provider: 'wecom_app', enabled: false, enabled_since: null })
    await wrapper.findAll('button').find(b => b.text() === '关闭企微通知')!.trigger('click')
    await flushPromises()
    expect(setPreference).toHaveBeenLastCalledWith(false)
  })

  it('prompts rebind when the bound target is no longer active', async () => {
    status.mockResolvedValue({ ...availableBound, target_state: 'invalid' })
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('请解绑后重新绑定')
    expect(wrapper.findAll('button').find(b => b.text() === '开启企微通知')!.attributes('disabled')).toBeDefined()
  })

  it('unbinds and refreshes status', async () => {
    status.mockResolvedValueOnce(availableBound)
    status.mockResolvedValueOnce(availableUnbound)
    unbind.mockResolvedValue({ success: true })
    const wrapper = makeCard()
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '解绑')!.trigger('click')
    await flushPromises()
    expect(unbind).toHaveBeenCalled()
    expect(wrapper.text()).toContain('已解绑企微账号')
    expect(wrapper.text()).toContain('绑定企微')
  })
})
