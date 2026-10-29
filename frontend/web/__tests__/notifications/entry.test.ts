import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
const request = vi.hoisted(() => vi.fn())
const findTask = vi.hoisted(() => vi.fn())
vi.mock('@/notifications/api', async () => {
  const actual = await vi.importActual('@/notifications/api')
  return { ...actual, notificationRequest: request }
})
vi.mock('@/api/scheduledTask', async () => {
  const actual = await vi.importActual('@/api/scheduledTask')
  return { ...actual, findScheduledTaskBySessionId: findTask }
})
import NotificationEntry from '@/notifications/NotificationEntry.vue'
import { NotificationApiError } from '@/notifications/api'
beforeEach(() => { localStorage.clear(); request.mockReset(); findTask.mockReset() })
async function entry(url: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/notification', component: NotificationEntry },
    { path: '/', component: { template: '<div>login</div>' } },
    { path: '/t/:tenant/login', component: { template: '<div>login</div>' } },
    { path: '/portal/notification-session', component: { template: '<div>global history</div>' } },
    { path: '/t/:tenant/chat/:agent?', component: { template: '<div>chat</div>' } },
    { path: '/t/:tenant/tasks/:task_id', component: { template: '<div>task view</div>' } },
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
  it('routes scheduled task paused notifications to the task view by task_id', async () => {
    // task.paused 资源（scheduled_task）自带 task_id，直接进入任务视图（设计 §6/§8.2）
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockResolvedValue({ event_type: 'task.paused', resource: { tenant_id: 'tenant-1', task_id: 'sched_pause1', session_id: 'task-session', name: '日报', status: 'paused' } })
    const { router } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(router.currentRoute.value.path).toBe('/t/tenant-1/tasks/sched_pause1')
    expect(findTask).not.toHaveBeenCalled()
  })
  it('routes runner-result scheduled notifications by the resource scheduled_task_id marker', async () => {
    // runner_result/runner_waiting 资源由资源解析透出 scheduled_task_id（二轮评审第 8 项）：
    // 直接路由任务视图，不再调任务列表匹配
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockResolvedValue({ event_type: 'task.completed', resource: { tenant_id: 'tenant-1', session_id: 'task-session', runner_id: 'runner-1', title: '日报', scheduled_task_id: 'sched_done1' } })
    const { router } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(findTask).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/t/tenant-1/tasks/sched_done1')
  })
  it('keeps chat terminal notifications on the session route when no task marker exists', async () => {
    // task.completed/task.failed 由 chat 与 scheduled runner 共用事件名（producer 同名生产）：
    // 资源无 scheduled_task_id（普通聊天会话）→ 按既有契约回聊天会话，不调任务列表
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockResolvedValue({ event_type: 'task.completed', resource: { tenant_id: 'tenant-1', session_id: 'chat-session', runner_id: 'runner-9', title: '聊天' } })
    const { router } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(findTask).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/t/tenant-1/chat')
    expect(router.currentRoute.value.query._load_session).toBe('chat-session')
  })
  it('shows a safe error when the scheduled task behind a runner notification is gone', async () => {
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockResolvedValue({ event_type: 'task.needs_action', resource: { tenant_id: 'tenant-1', session_id: 'task-session', runner_id: 'runner-1' } })
    const { router, wrapper } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(router.currentRoute.value.path).toBe('/notification')
    expect(wrapper.text()).toContain('关联的任务暂时无法解析')
  })
  it('shows a retryable error on transient request failure instead of falling back to chat', async () => {
    // 请求瞬时失败（网络异常/5xx）：不回退聊天页，展示可重试错误；重试成功后进入任务视图
    localStorage.setItem('saas_token_tenant-1', 'fake-test-token')
    request.mockRejectedValueOnce(new NotificationApiError(503))
    const { router, wrapper } = await entry('/notification?ref=message-1&tenant=tenant-1')
    expect(router.currentRoute.value.path).toBe('/notification')
    expect(wrapper.text()).toContain('通知操作失败')
    expect(wrapper.text()).toContain('重试')

    request.mockResolvedValue({ event_type: 'task.completed', resource: { tenant_id: 'tenant-1', session_id: 'task-session', runner_id: 'runner-1', scheduled_task_id: 'sched_retry1' } })
    const retry = wrapper.findAll('button').find(b => b.text() === '重试')
    expect(retry).toBeTruthy()
    await retry!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/t/tenant-1/tasks/sched_retry1')
  })
})
