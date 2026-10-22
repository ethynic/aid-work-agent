import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MessageShareDialog from '@/components/chat/MessageShareDialog.vue'
const { create } = vi.hoisted(() => ({ create: vi.fn() }))
vi.mock('@/api/messageShare', () => ({ createMessageShare: create }))

beforeEach(() => {
  create.mockReset().mockResolvedValue({ share_key: 'example', share_path: '/share/reply/example' })
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
})
function dialog() {
  return mount(MessageShareDialog, { props: { sessionId: 'session-example', messageId: 'selected-reply' }, global: { stubs: { Teleport: true } } })
}
describe('copy the selected reply with an explicit password choice', () => {
  it('requires a choice and creates a public link without adding management steps', async () => {
    const wrapper = dialog()
    expect(wrapper.findAll('button').find(button => button.text() === '生成并复制')!.attributes('disabled')).toBeDefined()
    await wrapper.get('input[value="public"]').setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('session-example', 'selected-reply', undefined)
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(new URL('/share/reply/example', window.location.origin).href)
    expect(wrapper.text()).toContain('已复制')
    wrapper.unmount()
  })
  it('copies the link and chosen password together, retaining the link when clipboard access fails', async () => {
    vi.mocked(navigator.clipboard.writeText).mockRejectedValue(new Error('permission denied'))
    const wrapper = dialog()
    await wrapper.get('input[value="password"]').setValue(true)
    await wrapper.get('#reply-share-password').setValue('example8')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(create).toHaveBeenCalledWith('session-example', 'selected-reply', 'example8')
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(expect.stringContaining('\n访问密码：example8'))
    expect(wrapper.get('textarea').element.value).toContain('/share/reply/example')
    expect(wrapper.text()).toContain('自动复制未成功')
    await wrapper.findAll('button').find(button => button.text() === '复制分享内容')!.trigger('click')
    expect(create).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
