import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import WecomScheduledSendForm from '@/components/wecomConversation/WecomScheduledSendForm.vue'
import WecomScheduledSendPrice from '@/components/saas/WecomScheduledSendPrice.vue'
import type { ScheduledSendPlan } from '@/api/wecomConversation'

const directory = vi.hoisted(() => vi.fn())
const savePlan = vi.hoisted(() => vi.fn())
const price = vi.hoisted(() => vi.fn())
vi.mock('@/api/wecomConversation', () => ({ listDirectory: directory, saveScheduledSend: savePlan }))
vi.mock('@/api/adminReports', () => ({ wecomScheduledSendPrice: price }))

const group = 'ws1.group'
const contact = 'ws1.contact'
const plan: ScheduledSendPlan = {
  task_id: 'sched_1', name: '晨间消息', status: 'paused', next_due_at: null,
  device_id: 'device-a', account: 'account-a', device_name: '固定设备', sessions: [group],
  session_names: { [group]: '早班群' }, text: '原正文', schedule_type: 'once', time_config: { run_at: '2099-11-21T09:00:17' },
}
const scope = {
  device_id: 'device-a', self_user_id: 'account-a', source_generation: 'generation-a', next_cursor: null,
  conversations: [{ session: group, name: '早班群', kind: 'group' }, { session: contact, name: '值班同事', kind: 'single' }],
}
const Modal = {
  props: ['modelValue', 'title'],
  template: '<section v-if="modelValue" :aria-label="title"><slot /><slot name="footer" /></section>',
}
function mountForm(existing: ScheduledSendPlan | null = null) {
  return mount(WecomScheduledSendForm, { props: { plan: existing }, global: { stubs: { BaseModal: Modal } } })
}

beforeEach(() => {
  vi.resetAllMocks()
  directory.mockResolvedValue(scope)
  savePlan.mockResolvedValue({ plan })
})

it('新建多选群聊和单聊，提交北京时间周规则和未经裁剪的正文', async () => {
  const wrapper = mountForm()
  await wrapper.get('#scheduled-name').setValue('早班提醒')
  await wrapper.get('#scheduled-text').setValue('  先签到\n再开会 ')
  await wrapper.get('#scheduled-rule').setValue('weekly')
  await wrapper.get('#scheduled-time').setValue('09:07')
  await wrapper.get('input[value="mon"]').setValue(true)
  await wrapper.get('input[value="thu"]').setValue(true)
  await wrapper.findAll('button').find(item => item.text() === '从本地目录选择会话')!.trigger('click')
  await flushPromises()
  for (const item of wrapper.findAll('[aria-label="选择目标会话"] input[type="checkbox"]')) await item.setValue(true)
  await wrapper.findAll('button').find(item => item.text() === '完成选择')!.trigger('click')
  await wrapper.get('#scheduled-send-form').trigger('submit')
  await flushPromises()
  expect(savePlan).toHaveBeenCalledExactlyOnceWith({ name: '早班提醒', text: '  先签到\n再开会 ',
    sessions: [group, contact], schedule_type: 'weekly', time_config: { hour: 9, minute: 7, weekdays: ['mon', 'thu'] },
    expected_device_id: 'device-a', expected_source_generation: 'generation-a',
  }, undefined)
  expect(wrapper.emitted('saved')).toHaveLength(1)
  wrapper.unmount()
})

it('固定设备未打开目录也可改文字，并保留原一次性时间的秒数', async () => {
  const wrapper = mountForm(plan)
  await wrapper.get('#scheduled-text').setValue('  修改文字 ')
  await wrapper.get('#scheduled-send-form').trigger('submit')
  await flushPromises()
  expect(directory).not.toHaveBeenCalled()
  expect(savePlan).toHaveBeenCalledExactlyOnceWith({ name: plan.name, text: '  修改文字 ', sessions: [group],
    schedule_type: 'once', time_config: { run_at: '2099-11-21 09:00:17' },
  }, plan.task_id)
  wrapper.unmount()
})

it('Portal 保留零值为十进制字符串，恢复默认显式发送空值', async () => {
  const entry = { unit_price: '0.50', default_price: '0.50', overridden: false }
  price.mockResolvedValueOnce(entry)
    .mockResolvedValueOnce({ ...entry, unit_price: '0.00', overridden: true })
    .mockResolvedValueOnce(entry)
  const wrapper = mount(WecomScheduledSendPrice)
  await flushPromises()
  await wrapper.get('#wecom-scheduled-price').setValue('0')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  expect(price).toHaveBeenNthCalledWith(2, '0')
  expect(wrapper.text()).toContain('当前 0.00')
  await wrapper.findAll('button').find(item => item.text() === '恢复默认')!.trigger('click')
  await flushPromises()
  expect(price).toHaveBeenNthCalledWith(3, null)
  expect(wrapper.text()).toContain('当前 0.50')
  wrapper.unmount()
})
