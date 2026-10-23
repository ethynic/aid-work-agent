import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import SharedReplyPage from '@/components/share/SharedReplyPage.vue'
const { read, route } = vi.hoisted(() => ({ read: vi.fn(), route: { params: { share_key: 'probe-share' } } }))
vi.mock('vue-router', () => ({ useRoute: () => route }))
vi.mock('@/api/messageShare', async (original) => ({ ...(await original()), viewMessageShare: read }))
const ready = { state: 'ready', access_ticket: 'fixture-ticket', message: {
  role: 'assistant', content: 'THIS IS THE CHOSEN REPLY',
  images: [{ file_id:'asset_0', download_url:'/api/public/reply-shares/probe-share/files/asset_0?ticket=fixture-ticket', display_name:'diagram', mime_type:'image/png', placement:'after_text', source:'tool_generated' }],
  attachments: [{ file_id:'asset_1', name:'proposal.docx', mime_type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document', type:'file' }],
} }
beforeEach(() => { read.mockReset() })
afterEach(() => { vi.restoreAllMocks() })
describe('independent public reader state and full reply probes', () => {
  it('public link renders the selected reply, images and attachment without login', async () => {
    read.mockResolvedValueOnce(ready)
    const wrapper = mount(SharedReplyPage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(read).toHaveBeenCalledWith('probe-share', undefined)
    expect(wrapper.text()).toContain('THIS IS THE CHOSEN REPLY')
    expect(wrapper.get('img').attributes('src')).toContain('/api/public/reply-shares/probe-share/files/asset_0')
    expect(wrapper.text()).toContain('proposal.docx')
    expect(wrapper.find('input[type=password]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('locked response reveals no reply/media; wrong password remains locked and correct password releases all content', async () => {
    read.mockResolvedValueOnce({state:'password_required'})
      .mockRejectedValueOnce(new Error('密码不正确，请重新输入'))
      .mockResolvedValueOnce(ready)
    const wrapper = mount(SharedReplyPage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('THIS IS THE CHOSEN REPLY')
    expect(wrapper.find('img').exists()).toBe(false)
    await wrapper.get('input[type=password]').setValue('wrong-fixture')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('密码不正确')
    expect(wrapper.find('img').exists()).toBe(false)
    await wrapper.get('input[type=password]').setValue('correct-fixture')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('THIS IS THE CHOSEN REPLY')
    expect(wrapper.text()).toContain('proposal.docx')
    expect(wrapper.find('img').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(false)
    wrapper.unmount()
  })
  it('missing share reports a retryable error rather than a blank page or login redirect', async () => {
    read.mockRejectedValueOnce(new Error('分享不存在或已不可用'))
    const wrapper = mount(SharedReplyPage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('分享不存在')
    expect(wrapper.text()).toContain('重试')
    wrapper.unmount()
  })
  it('anonymous API requests omit credentials and do not attach a preexisting account token', async () => {
    const actual = await vi.importActual<typeof import('@/api/messageShare')>('@/api/messageShare')
    localStorage.setItem('saas_token', 'fixture-not-a-real-credential')
    const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({state:'password_required'}), {status:200}))
    vi.stubGlobal('fetch', request)
    await actual.viewMessageShare('probe-share')
    expect(request.mock.calls[0][1].credentials).toBe('omit')
    expect(request.mock.calls[0][1].headers.Authorization).toBeUndefined()
    localStorage.removeItem('saas_token')
    vi.unstubAllGlobals()
  })
})
