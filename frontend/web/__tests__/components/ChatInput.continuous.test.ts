import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import ChatInput from '@/components/ChatInput.vue'

const wrappers: ReturnType<typeof mount>[] = []
function input(sessionId = 'session-A') {
  const wrapper = mount(ChatInput, {
    attachTo: document.body,
    props: { disabled: false, isProcessing: false, files: [], sessionId },
    global: { stubs: { ChatToolbar: true } },
  })
  wrappers.push(wrapper)
  return wrapper
}

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  document.body.innerHTML = ''
})

describe('Chat input continuous task choice', () => {
  it('keeps ordinary messages ordinary and sends an explicit continuous choice once', async () => {
    const wrapper = input()
    await wrapper.get('textarea').setValue('查询今天的订单')
    await wrapper.get('button[title="发送"]').trigger('click')
    expect(wrapper.emitted('send')).toEqual([['查询今天的订单', undefined]])

    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('textarea').setValue('监控孙晨的消息')
    await wrapper.get('button[title="发送"]').trigger('click')
    expect(wrapper.emitted('send')?.[1]).toEqual(['监控孙晨的消息', 'continuous'])
    expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false)
  })

  it('asks before a clear long running request and sends only the selected mode', async () => {
    const wrapper = input()
    const message = '你能从现在开始持续监控我和孙晨的对话并回复他的问题吗'
    await wrapper.get('textarea').setValue(message)
    await wrapper.get('button[title="发送"]').trigger('click')
    expect(wrapper.emitted('send')).toBeUndefined()
    const dialog = document.body.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('是否持续执行')
    const continuous = [...dialog.querySelectorAll('button')].find(button => button.textContent === '持续执行')!
    continuous.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('send')).toEqual([[message, 'continuous']])
  })

  it('supports one time choice and dismissing without sending or losing the draft', async () => {
    const wrapper = input()
    const message = '请持续监控企微消息'
    await wrapper.get('textarea').setValue(message)
    await wrapper.get('button[title="发送"]').trigger('click')
    document.body.querySelector<HTMLButtonElement>('[role="dialog"] [aria-label="关闭对话框"]')!.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('send')).toBeUndefined()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(message)

    await wrapper.get('button[title="发送"]').trigger('click')
    const dialog = document.body.querySelector('[role="dialog"]')!
    ;[...dialog.querySelectorAll('button')].find(button => button.textContent === '按普通任务发送')!.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('send')).toEqual([[message, undefined]])
  })

  it('closes a pending confirmation when the user switches sessions', async () => {
    const wrapper = input()
    await wrapper.get('textarea').setValue('请持续监控企微消息')
    await wrapper.get('button[title="发送"]').trigger('click')
    await wrapper.setProps({ sessionId: 'session-B' })
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('does not carry an explicit continuous choice into another session', async () => {
    const wrapper = input()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.setProps({ sessionId: 'session-B' })
    expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false)
    await wrapper.get('textarea').setValue('查询今天的订单')
    await wrapper.get('button[title="发送"]').trigger('click')
    expect(wrapper.emitted('send')).toEqual([['查询今天的订单', undefined]])
  })
})
