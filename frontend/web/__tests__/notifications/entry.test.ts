import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
const request = vi.hoisted(() => vi.fn())
vi.mock('@/notifications/api', async () => {
  const actual = await vi.importActual('@/notifications/api')
  return { ...actual, notificationRequest: request }
})
import NotificationEntry from '@/notifications/NotificationEntry.vue'
import { NotificationApiError } from '@/notifications/api'
beforeEach(() => { localStorage.clear(); request.mockReset() })
async function entry(url: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/notification', component: NotificationEntry },
    { path: '/', component: { template: '<div>login</div>' } },
    { path: '/t/:tenant/login', component: { template: '<div>login</div>' } },
    { path: '/portal/notification-session', component: { template: '<div>global history</div>' } },
    { path: '/t/:tenant/chat/:agent?', component: { template: '<div>chat</div>' } },
  ] })
  await router.push(url); await router.isReady()
  const wrapper = mount(NotificationEntry, { global: { plugins: [router] } })
  await flushPromises()
  return { router, wrapper }
}
describe('notification click entry', () => {
  it('redirects unauthenticated users through a safe login return URL', async () => {
    const { router } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(router.currentRoute.value.path).toBe('/t/tenant-1/login')
    expect(router.currentRoute.value.query.return_to).toBe('/notification?ref=message-1&tenant=tenant-1')
    expect(request).not.toHaveBeenCalled()
  })
  it('navigates to an existing session and never uses the new-task _sid parameter', async () => {
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockResolvedValue({ resource: { tenant_id: 'tenant-1', session_id: 'old-session', subagent_id: 'assistant-1', instance_id: 'instance-1', runner_id: 'runner-1' } })
    const { router } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(request).toHaveBeenCalledWith('/messages/message-1', {}, { Authorization: 'Bearer fake-test-token', 'X-Tenant-Id': 'tenant-1' })
    expect(router.currentRoute.value.path).toBe('/t/tenant-1/chat/assistant-1')
    expect(router.currentRoute.value.query).toEqual({ _load_session: 'old-session', instance_id: 'instance-1', runner_id: 'runner-1' })
    expect(router.currentRoute.value.query._sid).toBeUndefined()
  })
  it('shows a safe forbidden error without resolving another account resource', async () => {
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockRejectedValue(new NotificationApiError(403))
    const { router, wrapper } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(router.currentRoute.value.path).toBe('/notification')
    expect(wrapper.text()).toContain('当前账号无权查看此通知')
  })
  it('rejects a resource returned for a different tenant', async () => {
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockResolvedValue({ resource: { tenant_id: 'other-tenant', session_id: 'secret-session' } })
    const { router, wrapper } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(router.currentRoute.value.path).toBe('/notification')
    expect(wrapper.text()).toContain('租户与当前账号不匹配')
    expect(wrapper.text()).not.toContain('secret-session')
  })
  it('restores a global notification through the dedicated read-only resource route', async () => {
    localStorage.setItem('portal_token', 'fake-global-token')
    request.mockResolvedValue({ resource: { tenant_id: null, session_id: 'global-session', runner_id: 'global-runner' } })
    const { router } = await entry('/notification?ref=global-message')
    expect(router.currentRoute.value.path).toBe('/portal/notification-session')
    expect(router.currentRoute.value.query).toEqual({ ref: 'global-message' })
    expect(request).toHaveBeenCalledWith('/messages/global-message', {}, { Authorization: 'Bearer fake-global-token' })
  })
  it('handles test notifications without a session safely', async () => {
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockResolvedValue({ event_type: 'test', resource: {} })
    const { wrapper } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(wrapper.text()).toContain('测试通知已成功到达')
  })
})
