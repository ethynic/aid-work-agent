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
describe('copy the selected reply with a clear password choice', () => {
  it('defaults to public access and creates a link directly', async () => {
    const wrapper = dialog()
    expect((wrapper.get('input[value="public"]').element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.findAll('button').find(button => button.text() === '生成并复制')!.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('button[title="全屏"]').exists()).toBe(false)
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
    expect((wrapper.get('#reply-share-url').element as HTMLInputElement).value).toContain('/share/reply/example')
    expect(wrapper.text()).toContain('example8')
    expect(wrapper.text()).toContain('自动复制未成功')
    await wrapper.findAll('button').find(button => button.text() === '复制链接和密码')!.trigger('click')
    expect(create).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
  it('generates six readable characters, preserves edits when switching modes and replaces them only on request', async () => {
    const wrapper = dialog()
    await wrapper.get('input[value="password"]').setValue(true)
    const code = wrapper.get('#reply-share-password').element as HTMLInputElement
    expect(code.type).toBe('text')
    expect(code.value).toMatch(/^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$/)
    await wrapper.get('#reply-share-password').setValue('MYCODE')
    await wrapper.get('input[value="public"]').setValue(true)
    await wrapper.get('input[value="password"]').setValue(true)
    expect((wrapper.get('#reply-share-password').element as HTMLInputElement).value).toBe('MYCODE')
    await wrapper.get('button[aria-label="重新生成访问密码"]').trigger('click')
    expect((wrapper.get('#reply-share-password').element as HTMLInputElement).value).toMatch(/^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$/)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith(expect.stringContaining('\n访问密码：'))
    await wrapper.get('button[aria-label="仅复制链接"]').trigger('click')
    expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith(new URL('/share/reply/example', window.location.origin).href)
    expect(create).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})
