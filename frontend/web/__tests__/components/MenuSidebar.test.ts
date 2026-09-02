/**
 * MenuSidebar 组件单测
 *
 * 验证 flyout 二级菜单的核心交互：
 * - 桌面 hover 打开 / click 切换
 * - hover 切换互斥
 * - mouseleave 延迟关闭（150ms 阈值）
 * - 子项点击跳转 + 关闭
 * - 路由变化关闭
 * - isCollapsed 屏蔽
 * - 移动端精简（经验中心/知识中心/业务数据/我的定时任务/设置 隐藏）
 * - 业务数据跳转分支（chat 模式 window.open）
 *
 * 测试范式参考 ImageGallery.test.ts（attachTo: document.body + document.querySelector 检索 Teleport 内容）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref, reactive, nextTick } from 'vue'

// ============== Mock 依赖 ==============

// mock useSession（避免触发真实 API）
const sessionsRef = ref<any[]>([])
const loadSessionsMock = vi.fn().mockResolvedValue(undefined)
vi.mock('@/composables/useSession', () => ({
  useSession: () => ({
    sessions: sessionsRef,
    currentSessionId: ref<string | null>(null),
    isLoading: ref(false),
    loadSessions: loadSessionsMock,
    removeSession: vi.fn(),
    renameSession: vi.fn(),
    selectSession: vi.fn(),
  }),
}))

// mock useTenantAuth
const tenantIsLoggedIn = ref(false)
const tenantAdmin = ref<any>(null)
const tenant = ref<any>(null)
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({
    admin: tenantAdmin,
    tenant,
    isLoggedIn: tenantIsLoggedIn,
    logout: vi.fn(),
  }),
}))

// mock useAgent
vi.mock('@/composables/useAgent', () => ({
  useAgent: () => ({
    isSessionRunning: () => false,
    hasSessionUnreadCompletion: () => false,
    removeStreamState: vi.fn(),
  }),
}))

// mock useTheme
vi.mock('@/composables/useTheme', () => ({
  useTheme: () => ({
    currentTheme: ref('blue'),
    setTheme: vi.fn(),
    getAvailableThemes: () => [{ name: 'blue', label: '商务蓝' }],
  }),
}))

// mock useDesktopUpdater
vi.mock('@/composables/useDesktopUpdater', () => ({
  useDesktopUpdater: () => ({
    state: ref({ status: 'disabled', currentVersion: '', availableVersion: null, percent: 0 }),
    isVisible: ref(false),
    label: ref(''),
    activate: vi.fn(),
  }),
}))

// mock vue-router（reactive 让 route.path 变化能触发 watch）
const routerPush = vi.fn()
const routeState = reactive({ path: '/', params: {} as Record<string, any>, query: {} as Record<string, any> })
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => routeState,
}))

// stub SettingsDialog（避免渲染复杂子组件）
vi.mock('@/components/SettingsDialog.vue', () => ({
  default: defineComponent({
    name: 'SettingsDialog',
    props: ['visible'],
    emits: ['close'],
    setup() {
      return () => h('div', { class: 'settings-dialog-stub' })
    },
  }),
}))

import MenuSidebar from '@/components/MenuSidebar.vue'
import type { SubagentListItem } from '@/api/subagent'

// ============== 测试数据 ==============

function makeSubagent(overrides: Partial<SubagentListItem> = {}): SubagentListItem {
  return {
    agent_id: 'trade-specialist',
    name: '外贸获客',
    display_name: '外贸获客',
    description: '外贸助手',
    type: 'builtin',
    business_pages: [
      { id: 'p1', title: '客户管理', icon: '', route: '/customers' },
      { id: 'p2', title: '订单管理', icon: '', route: '/orders' },
    ],
    ...overrides,
  }
}

// ============== Helper ==============

function mountSidebar(props: Record<string, any> = {}) {
  const div = document.createElement('div')
  div.id = 'test-mount'
  document.body.appendChild(div)
  return mount(MenuSidebar, {
    attachTo: div,
    props: {
      isCollapsed: false,
      isMobile: false,
      showHistory: true,
      showNewSession: true,
      availableSubagents: [],
      ...props,
    },
  })
}

/** 找到包含指定文本的 button */
function findButtonByText(wrapper: any, text: string): any {
  return wrapper.findAll('button').find((b: any) => b.text().includes(text))
}

/** 找到 flyout 面板（Teleport to body） */
function findFlyout(): HTMLElement | null {
  return document.body.querySelector('.z-\\[45\\]') as HTMLElement | null
}

describe('MenuSidebar - flyout 二级菜单', () => {
  let wrapper: ReturnType<typeof mount> | null = null

  beforeEach(() => {
    document.body.innerHTML = ''
    vi.clearAllMocks()
    loadSessionsMock.mockResolvedValue(undefined)
    sessionsRef.value = []
    tenantIsLoggedIn.value = false
    tenantAdmin.value = null
    tenant.value = null
    routeState.path = '/'
    routeState.params = {}
    routeState.query = {}
    routerPush.mockClear()
  })

  afterEach(() => {
    // 主动卸载组件，避免 Teleport 残留导致下一个用例 patch 出错
    if (wrapper) {
      wrapper.unmount()
      wrapper = null
    }
    document.body.innerHTML = ''
  })

  /** 包裹 mountSidebar，记录 wrapper 供 afterEach 卸载 */
  function mountAndTrack(props: Record<string, any> = {}) {
    wrapper = mountSidebar(props)
    return wrapper
  }

  /** 在租户模式下挂载（route.path 以 /t/ 开头，tenantAdmin 已登录） */
  function mountTenantSidebar(props: Record<string, any> = {}) {
    routeState.path = '/t/test-tenant/chat'
    tenantIsLoggedIn.value = true
    tenantAdmin.value = { username: 'admin', role: 'tenant_admin' }
    tenant.value = { company_name: '测试租户' }
    return mountAndTrack(props)
  }

  it('桌面 hover 经验中心：flyout 浮出含 3 个子项', async () => {
    const wrapper = mountTenantSidebar()
    await flushPromises()

    const trigger = findButtonByText(wrapper, '经验中心')
    expect(trigger).toBeTruthy()
    await trigger.trigger('mouseenter')
    await flushPromises()

    const flyout = findFlyout()
    expect(flyout).not.toBeNull()
    const flyoutText = flyout!.textContent || ''
    expect(flyoutText).toContain('工作日报')
    expect(flyoutText).toContain('工作成果')
    expect(flyoutText).toContain('外部接待客户')
  })

  it('桌面 click 经验中心：toggle 开/关', async () => {
    const wrapper = mountTenantSidebar()
    await flushPromises()

    const trigger = findButtonByText(wrapper, '经验中心')
    expect(findFlyout()).toBeNull()

    await trigger.trigger('click')
    await flushPromises()
    expect(findFlyout()).not.toBeNull()

    await trigger.trigger('click')
    await flushPromises()
    expect(findFlyout()).toBeNull()
  })

  it('hover 切换：从经验中心切到管理菜单，仅管理菜单内容在 DOM 中', async () => {
    const wrapper = mountTenantSidebar()
    await flushPromises()

    const expTrigger = findButtonByText(wrapper, '经验中心')
    await expTrigger.trigger('mouseenter')
    await flushPromises()
    expect(findFlyout()?.textContent || '').toContain('工作日报')

    const adminTrigger = findButtonByText(wrapper, '管理菜单')
    await adminTrigger.trigger('mouseenter')
    await flushPromises()

    const flyout = findFlyout()
    expect(flyout).not.toBeNull()
    const text = flyout!.textContent || ''
    expect(text).not.toContain('工作日报')
    expect(text).toContain('用户管理')
  })

  it('mouseleave 延迟关闭：150ms 内仍在，200ms 后消失', async () => {
    vi.useFakeTimers()
    try {
      const wrapper = mountTenantSidebar()
      await flushPromises()

      const trigger = findButtonByText(wrapper, '经验中心')
      await trigger.trigger('mouseenter')
      await flushPromises()
      expect(findFlyout()).not.toBeNull()

      await trigger.trigger('mouseleave')
      await flushPromises()
      // 100ms（< 150ms 阈值）仍在
      vi.advanceTimersByTime(100)
      await flushPromises()
      expect(findFlyout()).not.toBeNull()

      // 再过 100ms（累计 200ms > 150ms）应消失
      vi.advanceTimersByTime(100)
      await flushPromises()
      expect(findFlyout()).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('子项点击：跳转 + 关闭 flyout', async () => {
    const wrapper = mountTenantSidebar()
    await flushPromises()

    const trigger = findButtonByText(wrapper, '经验中心')
    await trigger.trigger('mouseenter')
    await flushPromises()

    // 在 flyout 内找「工作日报」按钮（document 范围，因为 flyout 是 Teleport to body）
    const dailyBtn = Array.from(document.querySelectorAll('button'))
      .find(b => b.textContent?.includes('工作日报')) as HTMLButtonElement | undefined
    expect(dailyBtn).toBeTruthy()
    dailyBtn!.click()
    await nextTick()
    await flushPromises()

    expect(routerPush).toHaveBeenCalledTimes(1)
    expect(routerPush.mock.calls[0][0]).toContain('/daily-report')
    expect(findFlyout()).toBeNull()
  })

  it('路由变化：flyout 自动关闭', async () => {
    const wrapper = mountTenantSidebar()
    await flushPromises()

    const trigger = findButtonByText(wrapper, '经验中心')
    await trigger.trigger('mouseenter')
    await flushPromises()
    expect(findFlyout()).not.toBeNull()

    // 模拟路由变化
    routeState.path = '/t/test-tenant/daily-report'
    await nextTick()
    await flushPromises()

    expect(findFlyout()).toBeNull()
  })

  it('isCollapsed 屏蔽：hover/click 都不触发 flyout', async () => {
    const wrapper = mountTenantSidebar({ isCollapsed: true })
    await flushPromises()

    const trigger = findButtonByText(wrapper, '经验中心')
    if (trigger) {
      await trigger.trigger('mouseenter')
      await flushPromises()
      expect(findFlyout()).toBeNull()

      await trigger.trigger('click')
      await flushPromises()
      expect(findFlyout()).toBeNull()
    }
    // 收起态下无论如何都不应出现 flyout
    expect(findFlyout()).toBeNull()
  })

  it('移动端精简：经验中心/知识中心/管理菜单/业务数据 trigger 不渲染；用户菜单内 我的定时任务/设置 不渲染', async () => {
    const wrapper = mountTenantSidebar({
      isMobile: true,
      availableSubagents: [makeSubagent()],
    })
    await flushPromises()

    const allButtons = wrapper.findAll('button')
    const texts = allButtons.map((b: any) => b.text())
    // 一级菜单 trigger 不应渲染
    expect(texts.some(t => t.includes('经验中心'))).toBe(false)
    expect(texts.some(t => t.includes('知识中心'))).toBe(false)
    expect(texts.some(t => t.includes('管理菜单'))).toBe(false)
    expect(texts.some(t => t.includes('外贸获客'))).toBe(false) // 业务数据 trigger

    // 新会话按钮和历史会话仍在
    expect(texts.some(t => t.includes('新会话'))).toBe(true)
    expect(wrapper.text()).toContain('历史会话')

    // 打开用户菜单
    const userMenuBtn = allButtons.find((b: any) => b.text().includes('admin'))
    expect(userMenuBtn).toBeTruthy()
    await userMenuBtn!.trigger('click')
    await flushPromises()

    // 用户菜单展开后，「我的定时任务」「设置」不应渲染；修改密码/退出登录仍在
    const userMenuBtns = Array.from(document.querySelectorAll('button'))
      .map(b => b.textContent?.trim() || '')
    expect(userMenuBtns).not.toContain('我的定时任务')
    expect(userMenuBtns).not.toContain('设置')
    expect(userMenuBtns).toContain('修改密码')
    expect(userMenuBtns).toContain('退出登录')
  })

  it('业务数据跳转分支：对话界面 click 业务页应调用 window.open 而非 router.push', async () => {
    // 路由包含 /chat/，isChatPage=true -> 应走 window.open 分支
    routeState.path = '/t/test-tenant/chat/trade-specialist'
    tenantIsLoggedIn.value = true
    tenantAdmin.value = { username: 'admin', role: 'tenant_admin' }
    tenant.value = { company_name: '测试租户' }

    const windowOpenSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    const wrapper = mountAndTrack({
      availableSubagents: [makeSubagent()],
    })
    await flushPromises()

    // hover 业务数据 trigger（外贸获客）
    const trigger = findButtonByText(wrapper, '外贸获客')
    expect(trigger).toBeTruthy()
    await trigger.trigger('mouseenter')
    await flushPromises()
    expect(findFlyout()).not.toBeNull()

    // 在 flyout 内点击业务页（客户管理）
    const pageEl = Array.from(document.querySelectorAll('div'))
      .find(d => d.textContent?.includes('客户管理')
        && d.className.includes('cursor-pointer')) as HTMLElement | undefined
    expect(pageEl).toBeTruthy()
    pageEl!.click()
    await nextTick()
    await flushPromises()

    expect(windowOpenSpy).toHaveBeenCalledTimes(1)
    const url = windowOpenSpy.mock.calls[0][0] as string
    expect(url).toContain('/customers')
    expect(url).toContain('expand_menu=trade-specialist')
    expect(routerPush).not.toHaveBeenCalled()

    windowOpenSpy.mockRestore()
  })

  it('连接中心：普通租户用户可见 trigger，子菜单仅含 API配置/本地工具（管理项隐藏）', async () => {
    // 普通用户（非管理员）
    routeState.path = '/t/test-tenant/chat'
    tenantIsLoggedIn.value = true
    tenantAdmin.value = { username: 'user1', role: 'user' }
    tenant.value = { company_name: '测试租户' }

    const wrapper = mountAndTrack()
    await flushPromises()

    // 连接中心 trigger 普通用户可见
    const trigger = findButtonByText(wrapper, '连接中心')
    expect(trigger).toBeTruthy()

    await trigger.trigger('mouseenter')
    await flushPromises()

    const flyout = findFlyout()
    expect(flyout).not.toBeNull()
    const flyoutText = flyout!.textContent || ''
    expect(flyoutText).toContain('API配置')
    expect(flyoutText).toContain('本地工具')
    // 管理项对普通用户隐藏
    expect(flyoutText).not.toContain('渠道配置')
    expect(flyoutText).not.toContain('企微个人RPA')
  })

  it('连接中心：管理员子菜单含全部 4 项（API配置/渠道配置/企微个人RPA/本地工具）', async () => {
    const wrapper = mountTenantSidebar()
    await flushPromises()

    const trigger = findButtonByText(wrapper, '连接中心')
    expect(trigger).toBeTruthy()
    await trigger.trigger('mouseenter')
    await flushPromises()

    const flyout = findFlyout()
    expect(flyout).not.toBeNull()
    const flyoutText = flyout!.textContent || ''
    expect(flyoutText).toContain('API配置')
    expect(flyoutText).toContain('渠道配置')
    expect(flyoutText).toContain('企微个人RPA')
    expect(flyoutText).toContain('本地工具')
  })

  it('连接中心：本地工具已从用户下拉菜单移除，子菜单点击可跳转', async () => {
    const wrapper = mountTenantSidebar()
    await flushPromises()

    // 用户下拉菜单不再含「本地工具」
    const userMenuBtn = findButtonByText(wrapper, 'admin')
    expect(userMenuBtn).toBeTruthy()
    await userMenuBtn!.trigger('click')
    await flushPromises()
    const userMenuBtns = Array.from(document.querySelectorAll('button'))
      .map(b => b.textContent?.trim() || '')
    expect(userMenuBtns).not.toContain('本地工具')

    // 连接中心 flyout 内点击「本地工具」跳转
    const trigger = findButtonByText(wrapper, '连接中心')
    await trigger.trigger('mouseenter')
    await flushPromises()
    const localToolsBtn = Array.from(document.querySelectorAll('button'))
      .find(b => b.textContent?.includes('本地工具')) as HTMLButtonElement | undefined
    expect(localToolsBtn).toBeTruthy()
    localToolsBtn!.click()
    await nextTick()
    await flushPromises()

    expect(routerPush).toHaveBeenCalledTimes(1)
    expect(routerPush.mock.calls[0][0]).toContain('/local-tools')
  })

  it('知识中心：管理员 hover 后 flyout 含知识库子项，点击跳转 /knowledge 并关闭', async () => {
    const wrapper = mountTenantSidebar()
    await flushPromises()

    const trigger = findButtonByText(wrapper, '知识中心')
    expect(trigger).toBeTruthy()
    await trigger.trigger('mouseenter')
    await flushPromises()

    const flyout = findFlyout()
    expect(flyout).not.toBeNull()
    const flyoutText = flyout!.textContent || ''
    expect(flyoutText).toContain('知识库')

    // 点击「知识库」子项跳转并关闭 flyout
    const kbBtn = Array.from(document.querySelectorAll('button'))
      .find(b => b.textContent?.includes('知识库')) as HTMLButtonElement | undefined
    expect(kbBtn).toBeTruthy()
    kbBtn!.click()
    await nextTick()
    await flushPromises()

    expect(routerPush).toHaveBeenCalledTimes(1)
    expect(routerPush.mock.calls[0][0]).toContain('/knowledge')
    expect(findFlyout()).toBeNull()
  })

  it('知识中心：普通用户（非管理员）不渲染 trigger', async () => {
    routeState.path = '/t/test-tenant/chat'
    tenantIsLoggedIn.value = true
    tenantAdmin.value = { username: 'user1', role: 'user' }
    tenant.value = { company_name: '测试租户' }

    const wrapper = mountAndTrack()
    await flushPromises()

    expect(findButtonByText(wrapper, '知识中心')).toBeFalsy()
  })
})
