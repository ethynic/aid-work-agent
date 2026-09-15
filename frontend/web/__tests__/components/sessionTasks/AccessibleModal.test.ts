import { describe, it, expect } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import BaseModal from '@/components/ui/BaseModal.vue'

describe('会话任务对话框键盘操作', () => {
  it('聚焦对话框、双向循环 Tab、Escape 请求关闭后恢复触发按钮焦点', async () => {
    const trigger = document.createElement('button'); document.body.append(trigger); trigger.focus()
    const wrapper = mount(BaseModal, { attachTo: document.body, props: { modelValue: true, accessible: true, title: '发布范围' }, slots: { default: '<input aria-label="名称" /><button disabled>不可用</button><button>最后</button>' } })
    await flushPromises()
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(document.getElementById(dialog.getAttribute('aria-labelledby')!)?.textContent).toBe('发布范围')
    expect(document.activeElement).toBe(dialog)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    const buttons = Array.from(dialog.querySelectorAll('button'))
    const last = buttons[buttons.length - 1]
    expect(document.activeElement).toBe(last)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(dialog.querySelector('button'))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
    // Controlled parent can reject close (e.g. publish pending); focus remains inside.
    expect(document.activeElement).not.toBe(trigger)
    await wrapper.setProps({ modelValue: false })
    expect(document.activeElement).toBe(trigger)
    wrapper.unmount(); trigger.remove()
  })
  it('未显式启用的既有调用者保留原有语义和 Escape 行为', async () => {
    const wrapper = mount(BaseModal, { props: { modelValue: true, title: '既有弹框' }, global: { stubs: { teleport: true } } })
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })
})
