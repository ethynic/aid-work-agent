import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
// 飞书行只用 notificationsAPI（对齐企微卡 mock 手法），mock 掉出站即可
const status = vi.hoisted(() => vi.fn())
const preferences = vi.hoisted(() => vi.fn())
const suggest = vi.hoisted(() => vi.fn())
const bindConfirm = vi.hoisted(() => vi.fn())
const setPreference = vi.hoisted(() => vi.fn())
const unbind = vi.hoisted(() => vi.fn())
vi.mock('@/api/notifications', () => ({
  notificationsAPI: {
    feishuBindStatus: status,
    preferences,
    feishuBindSuggest: suggest,
    feishuBindConfirm: bindConfirm,
    feishuSetPreference: setPreference,
    feishuBindUnbind: unbind,
  },
}))
import FeishuTaskNotifyCard from '@/notifications/FeishuTaskNotifyCard.vue'

const availableUnbound = { app_available: true, bound: false, name: null, target_state: null }
const availableBound = { app_available: true, bound: true, name: '', target_state: 'active' }

function makeCard() {
  return mount(FeishuTaskNotifyCard)
}

beforeEach(() => {
  status.mockReset(); preferences.mockReset(); suggest.mockReset()
  bindConfirm.mockReset(); setPreference.mockReset(); unbind.mockReset()
  preferences.mockResolvedValue({ preferences: [] })
})

describe('feishu task notify card', () => {
  it('shows tenant-unconfigured hint and hides actions when unavailable', async () => {
    status.mockResolvedValue({ ...availableBound, app_available: false })
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('将飞书应用的通知用途设为两用或仅通知')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'BaseSwitch' }).exists()).toBe(false)
    expect(wrapper.emitted('availability')?.[wrapper.emitted('availability')!.length - 1]).toEqual([false])
  })

  it('binds automatically via phone suggestion and refreshes status', async () => {
    status.mockResolvedValueOnce(availableUnbound).mockResolvedValueOnce(availableBound)
    suggest.mockResolvedValue({ matched: true, open_id: 'ou_' + 'a'.repeat(32), name: '' })
    bindConfirm.mockResolvedValue({ success: true, open_id: 'ou_' + 'a'.repeat(32), name: '', target_registered: true })
    const wrapper = makeCard()
    await flushPromises()
    expect(bindConfirm).toHaveBeenCalledWith('ou_' + 'a'.repeat(32))
    expect(wrapper.text()).toContain('已通过手机号自动绑定飞书账号')
    // 偏好未开启（preferences mock 为空）时徽标为「已绑定」
    expect(wrapper.text()).toContain('已绑定')
    expect(wrapper.emitted('availability')?.[wrapper.emitted('availability')!.length - 1]).toEqual([true])
  })

  it('shows phone-miss hint below the row when suggestion misses', async () => {
    status.mockResolvedValue(availableUnbound)
    suggest.mockResolvedValue({ matched: false })
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('当前账号手机号未在飞书通讯录匹配到成员')
    expect(bindConfirm).not.toHaveBeenCalled()
    // 未绑定无解绑按钮（绑定仅识别自动完成）
    expect(wrapper.text()).not.toContain('解绑')
  })

  it('surfaces mapped suggest errors (no phone) without miss hint', async () => {
    status.mockResolvedValue(availableUnbound)
    suggest.mockRejectedValue(Object.assign(new Error('NOTIFICATION_BIND_PHONE_REQUIRED'), { code: 'NOTIFICATION_BIND_PHONE_REQUIRED' }))
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('未登记手机号')
    expect(wrapper.text()).not.toContain('未在飞书通讯录匹配到成员')
  })

  it('maps bind error codes to safe copy', async () => {
    status.mockResolvedValue(availableUnbound)
    suggest.mockResolvedValue({ matched: true, open_id: 'ou_' + 'a'.repeat(32), name: '' })
    bindConfirm.mockRejectedValue(Object.assign(new Error('NOTIFICATION_FEISHU_BIND_TAKEN'), { code: 'NOTIFICATION_FEISHU_BIND_TAKEN' }))
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('该飞书账号已被其他账号绑定')
    expect(wrapper.text()).not.toContain('NOTIFICATION_FEISHU_BIND_TAKEN')
  })

  it('warns instead of optimistic success when target registration fails', async () => {
    status.mockResolvedValue(availableUnbound)
    suggest.mockResolvedValue({ matched: true, open_id: 'ou_' + 'a'.repeat(32), name: '' })
    bindConfirm.mockResolvedValue({ success: true, open_id: 'ou_' + 'a'.repeat(32), name: '', target_registered: false })
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('通知目标登记失败')
    expect(wrapper.text()).not.toContain('已通过手机号自动绑定飞书账号')
  })

  it('bound row shows unbind button and working switch', async () => {
    status.mockResolvedValue(availableBound)
    const wrapper = makeCard()
    await flushPromises()
    expect(wrapper.text()).toContain('解绑')
    const switchButton = wrapper.find('button[aria-label="飞书通知开关"]')
    expect(switchButton.exists()).toBe(true)
    await switchButton.trigger('click')
    await flushPromises()
    expect(setPreference).toHaveBeenCalledWith(true)
  })

  it('unbind keeps row unbound without auto re-suggestion', async () => {
    // 解绑后不自动重识别（识别命中会立刻自动重绑，用户无法保持解绑态）
    status.mockResolvedValueOnce(availableBound).mockResolvedValueOnce(availableUnbound)
    suggest.mockResolvedValue({ matched: true, open_id: 'ou_' + 'a'.repeat(32), name: '' })
    unbind.mockResolvedValue({ success: true })
    const wrapper = makeCard()
    await flushPromises()
    await wrapper.find('button').trigger('click')
    await flushPromises()
    expect(unbind).toHaveBeenCalled()
    expect(wrapper.text()).toContain('已解绑飞书账号')
    expect(wrapper.text()).toContain('未绑定')
    expect(suggest).not.toHaveBeenCalled()
    expect(bindConfirm).not.toHaveBeenCalled()
  })
})
