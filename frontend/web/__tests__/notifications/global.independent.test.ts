import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { ref } from 'vue'

const boundaries = vi.hoisted(() => ({ resolve: vi.fn(), session: vi.fn(), history: vi.fn(), getRunner: vi.fn(),
  submit: vi.fn(), control: vi.fn(), cancel: vi.fn(), detach: vi.fn(), init: vi.fn(), logout: vi.fn() }))
const principal = ref({ user_id: 'global-admin', role: 'platform_admin' })
vi.mock('@/notifications/api', () => ({ notificationRequest: boundaries.resolve }))
vi.mock('@/api/session', () => ({ getSession: boundaries.session, getSessionMessages: boundaries.history }))
vi.mock('@/api/runner', () => ({ RunnerClient: class { get = boundaries.getRunner; submit = boundaries.submit; control = boundaries.control; cancel = boundaries.cancel; detach = boundaries.detach } }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ admin: principal, isLoggedIn: ref(true), init: boundaries.init, logout: boundaries.logout }) }))
import GlobalNotificationSession from '@/notifications/GlobalNotificationSession.vue'

const runner = { runner_id: 'original-runner', client_request_id: 'original-request', queue_order: 1,
  status: 'completed', session: { kind: 'web', session_id: 'original-session' },
  snapshot: { input: { text: 'original input' }, quickOptions: [{ label: 'Do another task', value: 'send again' }],
    browserAssistance: { kind: 'verification', url: 'https://provider.invalid' } },
  result: { output: 'Original result' } }
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear()
  principal.value = { user_id: 'global-admin', role: 'platform_admin' }
  window.history.replaceState({}, '', '/portal/notification-session')
  localStorage.setItem('portal_token', 'fixture-portal-token')
  boundaries.init.mockResolvedValue(undefined)
  boundaries.resolve.mockResolvedValue({ resource: { tenant_id: null, session_id: 'original-session', runner_id: 'original-runner' } })
  boundaries.session.mockResolvedValue({ session_id: 'original-session', tenant_id: null, user_id: 'global-admin', title: 'Original global session' })
  boundaries.history.mockResolvedValue({ messages: [] })
  boundaries.getRunner.mockResolvedValue(runner)
})
async function page() {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/portal/notification-session', component: GlobalNotificationSession }] })
  await router.push('/portal/notification-session?ref=notification-reference'); await router.isReady()
  const wrapper = mount(GlobalNotificationSession, { global: { plugins: [router], provide: { toggleSidebar: () => {} }, stubs: {
    AppHeader: { props: ['title'], template: '<header>{{ title }}</header>' },
    MessageItem: { name: 'IndependentMessageItem', props: ['message'], template: '<article>{{ message.content }}</article>' },
  } } })
  await flushPromises()
  return wrapper
}
describe('independent global notification read-only boundary', () => {
  it('loads only the original session and runner with no task submission or action controls', async () => {
    const wrapper = await page()
    expect(boundaries.resolve).toHaveBeenCalledWith('/messages/notification-reference', {}, { Authorization: 'Bearer fixture-portal-token' })
    expect(boundaries.session).toHaveBeenCalledWith('original-session')
    expect(boundaries.getRunner).toHaveBeenCalledWith('original-runner', { Authorization: 'Bearer fixture-portal-token' })
    expect(wrapper.text()).toContain('Original result')
    expect(wrapper.findAll('input,textarea')).toHaveLength(0)
    const renderedMessages = wrapper.findAllComponents({ name: 'IndependentMessageItem' })
    expect(renderedMessages).toHaveLength(2)
    for (const component of renderedMessages) {
      const message = component.props('message')
      expect(message).not.toHaveProperty('quickOptions')
      expect(message.browserAssistance).toBeUndefined()
      expect(message.runnerActive).toBe(false)
    }
    expect(boundaries.submit).not.toHaveBeenCalled()
    expect(boundaries.control).not.toHaveBeenCalled()
    expect(boundaries.cancel).not.toHaveBeenCalled()
    wrapper.unmount(); expect(boundaries.detach).toHaveBeenCalledOnce()
  })

  it.each(['user', 'tenant_admin'])('rejects role %s before accessing resource content', async role => {
    principal.value = { user_id: 'global-admin', role }
    const wrapper = await page()
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(boundaries.resolve).not.toHaveBeenCalled()
    expect(boundaries.session).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('rejects tenant-scoped resources on the global route before fetching their sessions', async () => {
    boundaries.resolve.mockResolvedValueOnce({ resource: { tenant_id: 'other-tenant', session_id: 'private-session', runner_id: 'private-runner' } })
    const wrapper = await page()
    expect(boundaries.session).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('private-session')
    expect(wrapper.findAll('article')).toHaveLength(0)
    wrapper.unmount()
  })

  it('rejects a session belonging to another user before fetching history and result', async () => {
    boundaries.session.mockResolvedValueOnce({ session_id: 'original-session', tenant_id: null, user_id: 'another-admin', title: 'Private title' })
    const wrapper = await page()
    expect(boundaries.history).not.toHaveBeenCalled()
    expect(boundaries.getRunner).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('Private title')
    wrapper.unmount()
  })

  it('suppresses results when portal credentials change during resource fetch', async () => {
    boundaries.getRunner.mockImplementationOnce(async () => {
      localStorage.setItem('portal_token', 'replacement-portal-token')
      return runner
    })
    const wrapper = await page()
    expect(wrapper.findAll('article')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Original result')
    wrapper.unmount()
  })

  it('rejects a returned runner for another session', async () => {
    boundaries.getRunner.mockResolvedValueOnce({ ...runner, session: { kind: 'web', session_id: 'another-session' } })
    const wrapper = await page()
    expect(wrapper.findAll('article')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Original result')
    wrapper.unmount()
  })
})
