import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
const mocks = vi.hoisted(() => ({ resource: vi.fn(), session: vi.fn(), messages: vi.fn(), runner: vi.fn() }))
vi.mock('@/notifications/api', () => ({ notificationRequest: mocks.resource }))
vi.mock('@/api/session', () => ({ getSession: mocks.session, getSessionMessages: mocks.messages }))
vi.mock('@/api/runner', () => ({ RunnerClient: class { get = mocks.runner; detach() {} } }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ admin: { value: { user_id: 'admin-1', role: 'platform_admin' } }, isLoggedIn: { value: true }, init: async () => {}, logout: async () => {} }) }))
import GlobalNotificationSession from '@/notifications/GlobalNotificationSession.vue'
const task = { runner_id: 'runner-1', client_request_id: 'request-1', queue_order: 1, session: { kind: 'web', session_id: 'global-session' }, status: 'completed', accepted_at: '2026-10-10T12:00:00', updated_at: '2026-10-10T12:01:00', result: { status: 'completed', output: '原任务结果' }, snapshot: { input: { text: '任务输入', message_id: 'input-1', attachments: [] } } }
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear(); window.history.replaceState({}, '', '/portal/notification-session')
  localStorage.setItem('portal_token', 'fake-test-token')
  mocks.resource.mockResolvedValue({ resource: { tenant_id: null, session_id: 'global-session', runner_id: 'runner-1' } })
  mocks.session.mockResolvedValue({ session_id: 'global-session', user_id: 'admin-1', tenant_id: null, title: '原会话名称' })
  mocks.messages.mockResolvedValue({ messages: [] }); mocks.runner.mockResolvedValue(task)
})
async function view() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/portal/notification-session', component: GlobalNotificationSession }] })
  await router.push('/portal/notification-session?ref=message-1'); await router.isReady()
  const wrapper = mount(GlobalNotificationSession, { global: { plugins: [router], provide: { toggleSidebar: () => {} }, stubs: { AppHeader: { props: ['title'], template: '<h1>{{title}}</h1>' }, MessageItem: { props: ['message'], template: '<p>{{message.content}}</p>' } } } })
  await flushPromises()
  return wrapper
}
describe('global notification session is a scoped read-only viewer', () => {
  it('validates the original global session and Runner before showing existing messages', async () => {
    const wrapper = await view()
    expect(mocks.session).toHaveBeenCalledWith('global-session')
    expect(mocks.runner).toHaveBeenCalledWith('runner-1', { Authorization: 'Bearer fake-test-token' })
    expect(wrapper.text()).toContain('原会话名称')
    expect(wrapper.text()).toContain('原任务结果')
    expect(wrapper.text()).toContain('只读查看')
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(wrapper.find('input').exists()).toBe(false)
  })
  it.each([{ tenant_id: 'tenant-1' }, { user_id: 'other-user' }])('rejects incompatible session scope %s without rendering title/content', async fields => {
    mocks.session.mockResolvedValue({ session_id: 'global-session', user_id: 'admin-1', tenant_id: null, title: '敏感名称', ...fields })
    const wrapper = await view()
    expect(wrapper.text()).not.toContain('敏感名称')
    expect(wrapper.text()).not.toContain('原任务结果')
    expect(mocks.messages).not.toHaveBeenCalled()
    expect(mocks.runner).not.toHaveBeenCalled()
  })
  it('rejects a Runner linked to another session without displaying its result', async () => {
    mocks.runner.mockResolvedValue({ ...task, session: { kind: 'web', session_id: 'other-session' } })
    const wrapper = await view()
    expect(wrapper.text()).not.toContain('原任务结果')
    expect(wrapper.text()).not.toContain('原会话名称')
    expect(wrapper.text()).toContain('资源权限')
  })
})
