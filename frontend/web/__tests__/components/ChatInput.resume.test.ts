import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChatInput from '@/components/ChatInput.vue'

function input(props: Record<string, unknown> = {}) {
  return mount(ChatInput, {
    props: { disabled: false, isProcessing: false, files: [], ...props },
    global: { stubs: { ChatToolbar: true } },
  })
}

describe('Chat input resume button for parked tasks', () => {
  it('offers the play button for a resumable parked task and emits resume without confirm', async () => {
    const wrapper = input({ canResume: true })
    expect(wrapper.find('button[title="继续上次任务"]').exists()).toBe(true)
    expect(wrapper.find('button[title="停止生成"]').exists()).toBe(false)
    await wrapper.get('button[title="继续上次任务"]').trigger('click')
    expect(wrapper.emitted('resume')).toEqual([[]])
  })

  it('keeps the stop button while processing even when a resume is possible', () => {
    const wrapper = input({ canResume: true, isProcessing: true, canStop: true })
    expect(wrapper.find('button[title="停止生成"]').exists()).toBe(true)
    expect(wrapper.find('button[title="继续上次任务"]').exists()).toBe(false)
  })

  it('keeps send and resume buttons side by side so typed content continues the task', async () => {
    const wrapper = input({ canResume: true })
    await wrapper.get('textarea').setValue('继续执行')
    await wrapper.get('button[title="发送"]').trigger('click')
    expect(wrapper.emitted('send')).toEqual([['继续执行', undefined]])
    expect(wrapper.find('button[title="继续上次任务"]').exists()).toBe(true)
  })

  it('shows no play button for ordinary idle sessions', () => {
    const wrapper = input()
    expect(wrapper.find('button[title="继续上次任务"]').exists()).toBe(false)
    expect(wrapper.find('button[title="停止生成"]').exists()).toBe(false)
  })
})
