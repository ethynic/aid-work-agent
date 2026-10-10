import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

const network = vi.hoisted(() => ({ getSession: vi.fn(), listSessions: vi.fn(), getLatestSession: vi.fn() }))
const agent = vi.hoisted(() => ({ send: vi.fn(), switch: vi.fn(), clear: vi.fn(), abort: vi.fn() }))
const initialization = vi.hoisted(() => ({ auth: vi.fn(), subagents: vi.fn() }))
const admin = ref<any>({ user_id: 'alice', tenant_id: 'tenant-one', username: 'Alice', role: 'user' })
const loggedIn = ref(true)
const messages = ref<any[]>([])
const agentSessionId = ref<string | null>(null)
vi.mock('@/api/session', async () => ({ ...await vi.importActual('@/api/session'), ...network }))
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ admin, isLoggedIn: loggedIn,
  tenant: ref({ tenant_id: 'tenant-one', company_name: 'Fixture' }), init: initialization.auth, logout: vi.fn() }) }))
vi.mock('@/composables/useAgent', () => ({ useAgent: () => ({ messages, sessionId: agentSessionId,
  isProcessing: ref(false), canStop: ref(false), currentFiles: ref([]), sendMessage: agent.send,
  clearSession: agent.clear, switchSession: agent.switch, clearSessionCache: vi.fn(), precacheNewSession: vi.fn(),
  uploadAttachment: vi.fn(), removeAttachment: vi.fn(), clearAttachments: vi.fn(), abortStreaming: agent.abort,
  inputHintState: ref('idle'), isWaitingHuman: ref(false), runnerFeedback: ref(null),
  isSessionRunning: () => false, hasSessionUnreadCompletion: () => false, removeStreamState: vi.fn() }) }))
vi.mock('@/composables/useCreditCheck', () => ({ useCreditCheck: () => ({ checkCreditBeforeAction: vi.fn(async () => true) }) }))
vi.mock('@/composables/useAttachmentPreview', () => ({ useAttachmentPreview: () => ({ previewAttachment: ref(null), isPreviewOpen: ref(false), closePreview: vi.fn() }) }))
vi.mock('@/composables/useSubagentList', () => ({ useSubagentList: () => ({ availableSubagents: ref([]), loadAvailableSubagents: initialization.subagents }) }))
vi.mock('@/composables/useMobile', () => ({ useMobile: () => ({ isMobile: ref(false) }) }))
vi.mock('@/composables/useTheme', () => ({ useTheme: () => ({ currentTheme: ref('blue'), setTheme: vi.fn(), getAvailableThemes: () => [] }) }))
vi.mock('@/composables/useDesktopUpdater', () => ({ useDesktopUpdater: () => ({ state: ref({ status: 'disabled' }), isVisible: ref(false), label: ref(''), activate: vi.fn() }) }))
vi.mock('@/api/saasTenant', () => ({ listConnectionSources: vi.fn(async () => ({ success: true, sources: [] })), listChannels: vi.fn(async () => ({ channels: [] })) }))
vi.mock('vue-toastification', () => ({ useToast: () => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }) }))

import ChatContainer from '@/components/ChatContainer.vue'
import MenuSidebar from '@/components/MenuSidebar.vue'
import { useSession } from '@/composables/useSession'

const validSession: any = { session_id: 'valid-session', tenant_id: 'tenant-one', user_id: 'alice', title: 'Valid session', context_data: {} }
const originalSession: any = { session_id: 'notification-session', tenant_id: 'tenant-one', user_id: 'alice', title: 'Old notification session', context_data: {} }
let wrappers: VueWrapper[]
beforeEach(() => {
  vi.clearAllMocks(); localStorage.clear(); wrappers = []
  admin.value = { user_id: 'alice', tenant_id: 'tenant-one', username: 'Alice', role: 'user' }; loggedIn.value = true
  localStorage.setItem('saas_token_tenant-one', 'fixture-alice')
  window.history.replaceState({}, '', '/t/tenant-one/chat')
  useSession().clearSessionCache(); agentSessionId.value = null; messages.value = []
  network.listSessions.mockResolvedValue({ sessions: [validSession], total: 1, page: 1, page_size: 20 })
  network.getLatestSession.mockResolvedValue({ session: null })
  initialization.auth.mockResolvedValue(undefined); initialization.subagents.mockResolvedValue(undefined)
  agent.switch.mockImplementation(async (id: string) => { agentSessionId.value = id; messages.value = [{ content: id }] })
  agent.clear.mockImplementation(() => { agentSessionId.value = null; messages.value = [] })
})
afterEach(() => { for (const wrapper of wrappers) wrapper.unmount() })

function deferred() {
  let resolve!: (value: any) => void
  let reject!: (reason: any) => void
  const promise = new Promise<any>((success, failure) => { resolve = success; reject = failure })
  return { promise, resolve, reject }
}
async function harness() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/t/:tenant/chat', name: 'tenant-chat', component: ChatContainer },
    { path: '/t/:tenant/chat/:subagent', name: 'tenant-chat-subagent', component: ChatContainer },
  ] })
  await router.push('/t/tenant-one/chat?_load_session=notification-session&runner_id=original-runner&_sid=do-not-send')
  await router.isReady()
  const fixture = defineComponent({ components: { MenuSidebar, ChatContainer }, template: '<div><MenuSidebar :is-collapsed="false" :available-subagents="[]"/><ChatContainer/></div>' })
  const wrapper = mount(fixture, { global: { plugins: [router], stubs: {
    AppHeader: true, MessageList: { props: ['messages'], template: '<section>{{ messages }}</section>' },
    ChatInput: { name: 'IndependentChatInput', props: ['disabled'], template: '<textarea :disabled="disabled" />' },
    SettingsDialog: true, AttachmentPreviewPanel: true,
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
async function navigate(wrapper: VueWrapper, action: 'new' | 'select') {
  const sidebar = wrapper.findComponent(MenuSidebar)
  if (action === 'new') {
    const button = sidebar.findAll('button').find(candidate => candidate.text() === '新会话')
    expect(button).toBeDefined(); await button!.trigger('click')
  } else {
    const row = sidebar.findAll('div.group').find(candidate => candidate.text().includes('Valid session'))
    expect(row).toBeDefined(); await row!.trigger('click')
  }
  await flushPromises()
}
function assertReady(wrapper: VueWrapper, router: ReturnType<typeof createRouter>, action: 'new' | 'select') {
  expect(router.currentRoute.value.query._load_session).toBeUndefined()
  expect(router.currentRoute.value.query.runner_id).toBeUndefined()
  expect(router.currentRoute.value.query._sid).toBeUndefined()
  expect(wrapper.findComponent(ChatContainer).find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.findComponent(ChatContainer).find('[role="status"]').exists()).toBe(false)
  expect(wrapper.find('textarea').attributes('disabled')).toBeUndefined()
  expect(useSession().currentSessionId.value).toBe(action === 'new' ? null : validSession.session_id)
  expect(agent.send).not.toHaveBeenCalled()
}

describe('independent actual chat/sidebar notification navigation', () => {
  it.each(['auth', 'subagents'] as const)('does not reopen latest history after new-session navigation during pending %s initialization', async stage => {
    const pending = deferred(); initialization[stage].mockReturnValueOnce(pending.promise)
    const { wrapper, router } = await harness()
    await navigate(wrapper, 'new')
    pending.resolve(undefined); await flushPromises()
    assertReady(wrapper, router, 'new')
    expect(network.getLatestSession).not.toHaveBeenCalled()
    expect(network.getSession).not.toHaveBeenCalled()
    expect(agent.switch).not.toHaveBeenCalled()
  })

  it.each(['new', 'select'] as const)('clears a failed notification link after sidebar %s navigation', async action => {
    network.getSession.mockRejectedValueOnce(new Error('Original resource unavailable'))
    const { wrapper, router } = await harness()
    expect(wrapper.findComponent(ChatContainer).find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
    await navigate(wrapper, action)
    assertReady(wrapper, router, action)
  })

  it.each(['new', 'select'] as const)('keeps sidebar %s navigation when pending notification fetch resolves late', async action => {
    const pending = deferred(); network.getSession.mockReturnValueOnce(pending.promise)
    const { wrapper, router } = await harness()
    expect(wrapper.findComponent(ChatContainer).find('[role="status"]').exists()).toBe(true)
    await navigate(wrapper, action)
    assertReady(wrapper, router, action)
    pending.resolve(originalSession); await flushPromises()
    assertReady(wrapper, router, action)
    expect(useSession().sessions.value.some(session => session.session_id === originalSession.session_id)).toBe(false)
    expect(agent.switch).not.toHaveBeenCalledWith(originalSession.session_id)
  })

  it('drops a pending original-session response after the authenticated account changes', async () => {
    const pending = deferred(); network.getSession.mockReturnValueOnce(pending.promise)
    const { wrapper } = await harness()
    admin.value = { ...admin.value, user_id: 'bob' }
    localStorage.setItem('saas_token_tenant-one', 'fixture-bob')
    pending.resolve(originalSession); await flushPromises()
    expect(useSession().sessions.value.some(session => session.session_id === originalSession.session_id)).toBe(false)
    expect(useSession().currentSessionId.value).not.toBe(originalSession.session_id)
    expect(agent.switch).not.toHaveBeenCalledWith(originalSession.session_id)
    expect(wrapper.text()).not.toContain('Old notification session')
  })

  it('drops a pending original-session response after its chat component unmounts', async () => {
    const pending = deferred(); network.getSession.mockReturnValueOnce(pending.promise)
    const { wrapper } = await harness()
    wrapper.unmount(); wrappers = []
    useSession().selectSession(validSession.session_id)
    pending.resolve(originalSession); await flushPromises()
    expect(useSession().currentSessionId.value).toBe(validSession.session_id)
    expect(useSession().sessions.value.some(session => session.session_id === originalSession.session_id)).toBe(false)
    expect(agent.switch).not.toHaveBeenCalledWith(originalSession.session_id)
  })
})
