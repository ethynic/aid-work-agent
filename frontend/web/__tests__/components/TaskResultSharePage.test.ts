import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaskResultSharePage from '@/components/share/TaskResultSharePage.vue'
import { TaskResultShareError, taskResultShareFileUrl } from '@/api/taskResultShare'

const { read, token, replace, push, route } = vi.hoisted(() => ({
  read: vi.fn(), token: vi.fn(), replace: vi.fn(), push: vi.fn(),
  route: { params: { share_key: 'opaque_key' } },
}))
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ replace, push }) }))
vi.mock('@/api/taskResultShare', async original => ({ ...(await original()), viewTaskResultShare: read }))
vi.mock('@/platform/credentialStore', () => ({ credentialGet: token }))

const ready = {
  state: 'ready', task_name: '月度总结', task_status: 'active', runner_status: 'completed',
  finished_at: '2026-11-06T12:00:00', tenant_id: 'tenant_1', task_id: 'sched_1', runner_id: 'runner_1',
  viewer_is_owner: false, access_ticket: 'ticket_1',
  message: { role: 'assistant', content: '本次最终总结',
    downloadableFiles: [{ file_id: 'asset_0', file_name: '总结.pdf', file_size: 100,
      mime_type: 'application/pdf', download_url: '/api/public/task-result-shares/opaque_key/files/asset_0?ticket=ticket_1' }] },
}

beforeEach(() => { read.mockReset(); token.mockReset(); replace.mockReset(); push.mockReset() })

describe('public scheduled result page', () => {
  it('renders only this run without login and keeps its file behind the share ticket', async () => {
    read.mockResolvedValueOnce(ready)
    const wrapper = mount(TaskResultSharePage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(read).toHaveBeenCalledOnce()
    expect(read).toHaveBeenCalledWith('opaque_key')
    expect(wrapper.text()).toContain('月度总结')
    expect(wrapper.text()).toContain('本次最终总结')
    expect(wrapper.text()).toContain('总结.pdf')
    expect(wrapper.text()).not.toContain('sched_1')
    expect(replace).not.toHaveBeenCalled()
    expect(taskResultShareFileUrl('opaque_key', 'asset_0', 'ticket_1', true)).toContain('/files/asset_0?ticket=ticket_1&download=1')
    wrapper.unmount()
  })

  it('checks the tenant account and sends the owner to this run', async () => {
    token.mockReturnValueOnce('owner-token')
    read.mockResolvedValueOnce(ready).mockResolvedValueOnce({ ...ready, viewer_is_owner: true })
    const wrapper = mount(TaskResultSharePage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(token).toHaveBeenCalledWith('saas_token_tenant_1')
    expect(read.mock.calls).toEqual([['opaque_key'], ['opaque_key', 'owner-token', 'tenant_1']])
    expect(replace).toHaveBeenCalledWith({ path: '/t/tenant_1/tasks/sched_1', query: { runner_id: 'runner_1' } })
    wrapper.unmount()
  })

  it('shows the public result while the optional owner check is still pending', async () => {
    token.mockReturnValueOnce('owner-token')
    let complete!: (value: typeof ready) => void
    const ownerCheck = new Promise<typeof ready>(resolve => { complete = resolve })
    read.mockResolvedValueOnce(ready).mockReturnValueOnce(ownerCheck)
    const wrapper = mount(TaskResultSharePage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('本次最终总结')
    expect(wrapper.text()).not.toContain('正在加载任务结果')
    expect(replace).not.toHaveBeenCalled()
    complete({ ...ready, viewer_is_owner: true })
    await flushPromises()
    expect(replace).toHaveBeenCalledWith({ path: '/t/tenant_1/tasks/sched_1', query: { runner_id: 'runner_1' } })
    wrapper.unmount()
  })

  it('removes an already displayed public result if the owner check finds deletion', async () => {
    token.mockReturnValueOnce('owner-token')
    let fail!: (reason: Error) => void
    const ownerCheck = new Promise((_, reject) => { fail = reject })
    read.mockResolvedValueOnce(ready).mockReturnValueOnce(ownerCheck)
    const wrapper = mount(TaskResultSharePage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('本次最终总结')
    fail(new TaskResultShareError('任务已删除', 404))
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('任务已删除或链接已失效')
    expect(wrapper.text()).not.toContain('本次最终总结')
    wrapper.unmount()
  })

  it('keeps another account read-only and returns through the validated share path after login', async () => {
    token.mockReturnValueOnce('other-token')
    read.mockResolvedValueOnce(ready).mockResolvedValueOnce(ready)
    const wrapper = mount(TaskResultSharePage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(replace).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('本次最终总结')
    await wrapper.get('button').trigger('click')
    expect(push).toHaveBeenCalledWith({ path: '/t/tenant_1/login', query: { return_to: '/share/task-result/opaque_key' } })
    wrapper.unmount()
  })

  it('shows the expired link state when the task is gone', async () => {
    read.mockRejectedValueOnce(new TaskResultShareError('任务已删除', 404))
    const wrapper = mount(TaskResultSharePage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('任务已删除或链接已失效')
    expect(wrapper.text()).not.toContain('本次最终总结')
    wrapper.unmount()
  })

  it('keeps the public result when the optional account check fails', async () => {
    token.mockReturnValueOnce('expired-token')
    read.mockResolvedValueOnce(ready).mockRejectedValueOnce(new TaskResultShareError('会话过期', 401))
    const wrapper = mount(TaskResultSharePage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(read).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('本次最终总结')
    expect(wrapper.find('[role=alert]').exists()).toBe(false)
    expect(replace).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('offers retry after the anonymous result request fails temporarily', async () => {
    read.mockRejectedValueOnce(new Error('网络中断')).mockResolvedValueOnce(ready)
    const wrapper = mount(TaskResultSharePage, { global: { stubs: { AttachmentPreviewPanel: true } } })
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('网络中断')
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(read).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('本次最终总结')
    wrapper.unmount()
  })
})
