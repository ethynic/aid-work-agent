/**
 * HelpExpand 组件单测
 *
 * 验证展开状态的两种控制模式：
 * - 不传 open：非受控，点击按钮在内部切换
 * - 传 open：受控，点击按钮只发出 update:open，自身不切换（由外部决定展开状态）
 *
 * 写法参考同目录 BaseModal.test.ts：attachTo document.body（jsdom 对未挂载元素清掉
 * 内联 display:none 后 getComputedStyle 仍返回 none，isVisible 判定不可靠）。
 */
import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

import HelpExpand from '@/components/ui/HelpExpand.vue'

const wrappers: Array<{ unmount(): void }> = []

function mountHelp(props: Record<string, unknown> = {}) {
  const wrapper = mount(HelpExpand, {
    props,
    slots: {
      default: '<p id="help-steps">步骤说明</p>',
    },
    attachTo: document.body,
  })
  wrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  while (wrappers.length) wrappers.pop()!.unmount()
})

describe('HelpExpand 展开控制', () => {
  it('不传 open 时点击按钮在内部切换', async () => {
    const wrapper = mountHelp()
    const button = wrapper.find('button')
    const panel = wrapper.find('.mt-2')

    expect(button.attributes('aria-expanded')).toBe('false')
    expect(panel.isVisible()).toBe(false)
    expect(wrapper.emitted('update:open')).toBeUndefined()

    await button.trigger('click')
    expect(button.attributes('aria-expanded')).toBe('true')
    expect(panel.isVisible()).toBe(true)
    expect(wrapper.text()).toContain('步骤说明')

    await button.trigger('click')
    expect(button.attributes('aria-expanded')).toBe('false')
    expect(panel.isVisible()).toBe(false)
    expect(wrapper.emitted('update:open')).toBeUndefined()
  })

  it('传 open 并监听 update:open 时，点击按钮发出事件、自身不切换', async () => {
    const wrapper = mountHelp({ open: false })
    const button = wrapper.find('button')
    const panel = wrapper.find('.mt-2')
    expect(button.attributes('aria-expanded')).toBe('false')
    expect(panel.isVisible()).toBe(false)

    await button.trigger('click')
    expect(wrapper.emitted('update:open')).toEqual([[true]])
    // 自身不切换：仍由外部 open=false 控制
    expect(button.attributes('aria-expanded')).toBe('false')
    expect(panel.isVisible()).toBe(false)

    // 外部把 open 置 true 后展开
    await wrapper.setProps({ open: true })
    expect(button.attributes('aria-expanded')).toBe('true')
    expect(panel.isVisible()).toBe(true)
    expect(wrapper.text()).toContain('步骤说明')

    // 外部 open=true 时点击，发出 false
    await button.trigger('click')
    expect(wrapper.emitted('update:open')).toEqual([[true], [false]])
  })
})
