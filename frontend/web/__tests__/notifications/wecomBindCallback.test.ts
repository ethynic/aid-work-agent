import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
const bindCallback = vi.hoisted(() => vi.fn())
const credentialGet = vi.hoisted(() => vi.fn())
vi.mock('@/api/notifications', () => ({
  notificationsAPI: { wecomBindCallback: bindCallback },
}))
vi.mock('@/platform/credentialStore', () => ({ credentialGet }))
import WecomBindCallback from '@/notifications/WecomBindCallback.vue'

// 企微绑定回调落地页（评审回炉 P2-4/P1-3）：发起页存 localStorage 的 token key
// 完成绑定；target_registered=false 时显式警示，不乐观提示成功
const CONTEXT = JSON.stringify({ token_key: 'saas_token_tenant-1', return_path: '/t/tenant-1/notifications' })

async function mountPage() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/notification/wecom-bind', component: WecomBindCallback },
    { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
  ] })
  await router.push('/notification/wecom-bind?code=c&state=s'); await router.isReady()
  const wrapper = mount(WecomBindCallback, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  localStorage.clear()
  bindCallback.mockReset(); credentialGet.mockReset()
})

describe('wecom bind callback landing page', () => {
  it('completes binding with the opener-stored token key and consumes the context', async () => {
    localStorage.setItem('wecom-bind-context', CONTEXT)
    credentialGet.mockReturnValue('fake-token')
    bindCallback.mockResolvedValue({ success: true, userid: 'zhangsan', name: '张三', target_registered: true })
    const wrapper = await mountPage()
    expect(credentialGet).toHaveBeenCalledWith('saas_token_tenant-1')
    expect(bindCallback).toHaveBeenCalledWith('c', 's', { Authorization: 'Bearer fake-token' })
    expect(wrapper.text()).toContain('绑定成功（张三）')
    expect(localStorage.getItem('wecom-bind-context')).toBeNull()
  })

  it('warns instead of optimistic success when target registration fails', async () => {
    localStorage.setItem('wecom-bind-context', CONTEXT)
    credentialGet.mockReturnValue('fake-token')
    bindCallback.mockResolvedValue({ success: true, userid: 'zhangsan', name: '张三', target_registered: false })
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('绑定成功，但通知目标登记失败')
    expect(wrapper.find('p[role="status"]').classes()).toContain('text-amber-600')
    expect(wrapper.text()).toContain('返回通知设置')
  })

  it('asks the user to log in again when no opener context token is available', async () => {
    credentialGet.mockReturnValue(null)
    const wrapper = await mountPage()
    expect(bindCallback).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('尚未登录')
  })
})
