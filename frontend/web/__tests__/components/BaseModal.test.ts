/**
 * BaseModal 组件单测
 *
 * 验证点击遮罩层关闭弹框时的拖拽选择保护：
 * - 正常点击遮罩层（mousedown + click 都在弹框外）-> 关闭弹框
 * - 从弹框内拖拽选择文本、在弹框外松开鼠标（mousedown 在弹框内 + click 落在遮罩层）-> 不关闭弹框
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import BaseModal from '@/components/ui/BaseModal.vue'

const overlaySelector = '.fixed.inset-0.z-50'

function mountModal() {
  return mount(BaseModal, {
    props: {
      modelValue: true,
      mode: 'view', // view 模式：点击遮罩直接关闭
    },
    slots: {
      default: '<input id="name-input" />',
    },
    global: {
      stubs: { teleport: true },
    },
    attachTo: document.body,
  })
}

describe('BaseModal 遮罩层点击关闭', () => {
  it('正常点击遮罩层时关闭弹框', async () => {
    const wrapper = mountModal()
    const overlay = wrapper.find(overlaySelector)

    await overlay.trigger('mousedown')
    await overlay.trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })

  it('从弹框内拖拽选择文本后在弹框外松开鼠标时不关闭弹框', async () => {
    const wrapper = mountModal()
    const overlay = wrapper.find(overlaySelector)

    // 在弹框内容区（输入框）内按下鼠标，模拟拖拽选择文本
    await wrapper.find('#name-input').trigger('mousedown')
    // 在遮罩层（弹框外）松开鼠标，浏览器把 click 派发到 mousedown 与 mouseup 的共同祖先（遮罩层）
    await overlay.trigger('click')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })
})
