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

// 企微绑定回调落地页（评审回炉 P2-4/P1-3；复审 2/3）：发起页按 state 分键存
// localStorage 的 token key + 发起租户，本页只消费回调 state 对应的键并带
// X-Tenant-Id 回传；target_registered=false 时显式警示，不乐观提示成功
const CONTEXT = JSON.stringify({ token_key: 'saas_token_tenant-1', return_path: '/t/tenant-1/notifications', tenant: 'tenant-1' })
// 与落地页同款分键规则
const contextKey = (state: string) => `wecom-bind-context:${state}`

async function mountPage(query = 'code=c&state=s') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/notification/wecom-bind', component: WecomBindCallback },
    { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
  ] })
  await router.push(`/notification/wecom-bind?${query}`); await router.isReady()
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
    localStorage.setItem(contextKey('s'), CONTEXT)
    credentialGet.mockReturnValue('fake-token')
    bindCallback.mockResolvedValue({ success: true, userid: 'zhangsan', name: '张三', target_registered: true })
    const wrapper = await mountPage()
    expect(credentialGet).toHaveBeenCalledWith('saas_token_tenant-1')
    // 复审 3：带发起租户 X-Tenant-Id（平台管理员 subject 租户由该头决定）
    expect(bindCallback).toHaveBeenCalledWith('c', 's', {
      Authorization: 'Bearer fake-token', 'X-Tenant-Id': 'tenant-1',
    })
    expect(wrapper.text()).toContain('绑定成功（张三）')
    expect(localStorage.getItem(contextKey('s'))).toBeNull()
  })

  it('only consumes the context of the callback state and leaves other states intact', async () => {
    // 复审 2：两个标签页并发绑定各持不同 state，A 回调消费后 B 的上下文仍在
    localStorage.setItem(contextKey('s1'), CONTEXT)
    localStorage.setItem(contextKey('s2'), JSON.stringify({ token_key: 'saas_token_tenant-2', return_path: '/t/tenant-2/notifications', tenant: 'tenant-2' }))
    credentialGet.mockReturnValue('fake-token')
    bindCallback.mockResolvedValue({ success: true, userid: 'zhangsan', name: '张三', target_registered: true })
    await mountPage('code=c&state=s1')
    expect(bindCallback).toHaveBeenCalledWith('c', 's1', {
      Authorization: 'Bearer fake-token', 'X-Tenant-Id': 'tenant-1',
    })
    expect(localStorage.getItem(contextKey('s1'))).toBeNull()
    expect(localStorage.getItem(contextKey('s2'))).toBe(JSON.stringify({ token_key: 'saas_token_tenant-2', return_path: '/t/tenant-2/notifications', tenant: 'tenant-2' }))
    // B 的 state 回调照常取到自己的上下文
    bindCallback.mockClear(); credentialGet.mockClear()
    credentialGet.mockReturnValue('token-2')
    bindCallback.mockResolvedValue({ success: true, userid: 'lisi', name: '李四', target_registered: true })
    await mountPage('code=c&state=s2')
    expect(credentialGet).toHaveBeenCalledWith('saas_token_tenant-2')
    expect(bindCallback).toHaveBeenCalledWith('c', 's2', {
      Authorization: 'Bearer token-2', 'X-Tenant-Id': 'tenant-2',
    })
    expect(localStorage.getItem(contextKey('s2'))).toBeNull()
  })

  it('omits the tenant header for portal-initiated binds', async () => {
    localStorage.setItem(contextKey('s'), JSON.stringify({ token_key: 'saas_token', return_path: '/portal/notifications' }))
    credentialGet.mockReturnValue('fake-token')
    bindCallback.mockResolvedValue({ success: true, userid: 'zhangsan', name: '张三', target_registered: true })
    await mountPage()
    expect(bindCallback).toHaveBeenCalledWith('c', 's', { Authorization: 'Bearer fake-token' })
  })

  it('warns instead of optimistic success when target registration fails', async () => {
    localStorage.setItem(contextKey('s'), CONTEXT)
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
