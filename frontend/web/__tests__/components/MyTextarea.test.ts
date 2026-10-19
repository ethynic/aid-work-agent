/**
 * MyTextarea 组件单测
 *
 * 验证 auto-resize 与 min-height 的联动（定制弹框扩展提示词文本框曾因两者失效而撑满弹框）：
 * - auto-resize=false：以 min-height 固定高度，挂载/回填内容都不撑高
 * - auto-resize=true（默认）：不写 min-height，按内容撑高
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import MyTextarea from '@/components/ui/MyTextarea.vue'

function mountTextarea(props: Record<string, unknown> = {}) {
  return mount(MyTextarea, {
    props: { modelValue: '', enablePreview: false, ...props },
    global: { stubs: { teleport: true } },
  })
}

describe('MyTextarea auto-resize', () => {
  it('auto-resize=false 时应用 min-height，且挂载即带内容也不撑高', async () => {
    const wrapper = mountTextarea({
      modelValue: '第一行\n第二行\n第三行',
      autoResize: false,
      minHeight: '140px',
    })
    await nextTick()

    const el = wrapper.find('textarea').element as HTMLTextAreaElement
    expect(el.style.minHeight).toBe('140px')
    // 撑高逻辑不介入：不写入内联 height（jsdom 下 scrollHeight 为 0，若撑高会写成 0px）
    expect(el.style.height).toBe('')
  })

  it('auto-resize=false 时回填内容也不撑高', async () => {
    const wrapper = mountTextarea({ autoResize: false, minHeight: '140px' })
    await nextTick()

    await wrapper.setProps({ modelValue: '回填的较长内容\n'.repeat(50) })
    await nextTick()

    const el = wrapper.find('textarea').element as HTMLTextAreaElement
    expect(el.style.minHeight).toBe('140px')
    expect(el.style.height).toBe('')
  })

  it('默认 auto-resize=true 时不写 min-height，按内容撑高', async () => {
    const wrapper = mountTextarea({ modelValue: '内容', minHeight: '140px' })
    await nextTick()

    const el = wrapper.find('textarea').element as HTMLTextAreaElement
    expect(el.style.minHeight).toBe('')
    expect(el.style.height).toBe('0px')
  })
})
