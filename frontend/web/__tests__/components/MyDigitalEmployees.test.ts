/**
 * MyDigitalEmployees 页面组件单元测试
 *
 * 验证：
 * - 渲染：mock listSubagents 返回 3 个数字员工（含 1 个 main），过滤 main 后只渲染 2 张卡片
 * - 描述截断：超过 30 字的描述被截断为 30 字 + "..."
 * - 点击卡片触发路由跳转
 * - 空状态：返回空数组时渲染「暂无可用的数字员工」提示
 * - 加载态：加载中显示 spinner
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'

// ============== Mock 依赖 ==============

// mock listSubagents 返回值
const listSubagentsMock = vi.fn()
vi.mock('@/api/subagent', () => ({
  listSubagents: (...args: any[]) => listSubagentsMock(...args),
}))

// mock useTenantAuth（默认未登录状态）
const tenantIsLoggedIn = ref(false)
const tenantAdmin = ref(null)
const tenantLogout = vi.fn()
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({
    admin: tenantAdmin,
    isLoggedIn: tenantIsLoggedIn,
    logout: tenantLogout,
  }),
}))

// mock vue-router
const routerPush = vi.fn()
const routePath = ref('/')
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ path: routePath.value, params: {} }),
}))

// 用一个极简的 AppHeader stub 代替真实组件，避免渲染复杂度
// 注意：vi.mock 是 hoisted 的，所以 stub 定义必须放在 factory 内部或使用 vi.hoisted
vi.mock('@/components/AppHeader.vue', () => ({
  default: defineComponent({
    name: 'AppHeader',
    props: ['title', 'isLoggedIn', 'user'],
    emits: ['toggle-sidebar', 'logout'],
    setup() {
      return () => h('div', { class: 'app-header-stub' }, 'AppHeader Stub')
    },
  }),
}))

// ============== 测试 ==============

import MyDigitalEmployees from '@/components/MyDigitalEmployees.vue'

describe('MyDigitalEmployees', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listSubagentsMock.mockReset()
    tenantIsLoggedIn.value = false
    tenantAdmin.value = null
    routePath.value = '/'
  })

  it('过滤 main CEO 智能体，只渲染 2 张卡片（返回 3 个含 1 个 main）', async () => {
    listSubagentsMock.mockResolvedValue({
      success: true,
      data: [
        { agent_id: 'main', name: 'CEO 智能体', description: '不应显示' },
        { agent_id: 'trade-specialist', name: '外贸获客', description: '帮助外贸业务员找客户' },
        { agent_id: 'travel-consultant', name: '旅游顾问', description: '帮助规划旅游行程' },
      ],
    })

    const wrapper = mount(MyDigitalEmployees, {
      global: {
        provide: {
          toggleSidebar: () => {},
        },
      },
    })

    await flushPromises()

    // 应只渲染 2 张卡片（main 被过滤）
    const cards = wrapper.findAll('.group')
    expect(cards.length).toBe(2)
    // 不应出现 main 的内容
    expect(wrapper.text()).not.toContain('CEO 智能体')
    expect(wrapper.text()).toContain('外贸获客')
    expect(wrapper.text()).toContain('旅游顾问')
  })

  it('描述超过 30 字被截断为 30 字 + "..."', async () => {
    const longDesc = '这是一段非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常非常长的描述文字' // 50 字
    listSubagentsMock.mockResolvedValue({
      success: true,
      data: [
        { agent_id: 'agent-1', name: '智能体A', description: longDesc },
      ],
    })

    const wrapper = mount(MyDigitalEmployees, {
      global: {
        provide: { toggleSidebar: () => {} },
      },
    })

    await flushPromises()

    const descEl = wrapper.find('.group p.text-sm')
    expect(descEl.exists()).toBe(true)
    const text = descEl.text()
    // 截断后应为前 30 字 + "..."
    expect(text.endsWith('...')).toBe(true)
    expect(text.length).toBe(33) // 30 字 + 3 个字符的 "..."
    // 不应包含完整的 50 字描述
    expect(text).not.toContain(longDesc)
  })

  it('点击卡片触发路由跳转（非租户模式：/chat/{agent_id}）', async () => {
    listSubagentsMock.mockResolvedValue({
      success: true,
      data: [
        { agent_id: 'trade-specialist', name: '外贸获客', description: '外贸助手' },
      ],
    })

    const wrapper = mount(MyDigitalEmployees, {
      global: {
        provide: { toggleSidebar: () => {} },
      },
    })

    await flushPromises()

    const card = wrapper.find('.group')
    expect(card.exists()).toBe(true)
    await card.trigger('click')

    expect(routerPush).toHaveBeenCalledTimes(1)
    expect(routerPush).toHaveBeenCalledWith('/chat/trade-specialist')
  })

  it('点击「立即使用」按钮也触发路由跳转（@click.stop 不应阻止按钮自身 click）', async () => {
    listSubagentsMock.mockResolvedValue({
      success: true,
      data: [
        { agent_id: 'travel-consultant', name: '旅游顾问', description: '旅游助手' },
      ],
    })

    const wrapper = mount(MyDigitalEmployees, {
      global: {
        provide: { toggleSidebar: () => {} },
      },
    })

    await flushPromises()

    const btn = wrapper.find('.group button')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')

    expect(routerPush).toHaveBeenCalledTimes(1)
    expect(routerPush).toHaveBeenCalledWith('/chat/travel-consultant')
  })

  it('空状态：返回空数组时渲染空状态提示', async () => {
    listSubagentsMock.mockResolvedValue({
      success: true,
      data: [],
    })

    const wrapper = mount(MyDigitalEmployees, {
      global: {
        provide: { toggleSidebar: () => {} },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('暂无可用的数字员工')
    expect(wrapper.text()).toContain('请联系管理员开通权限')
    // 不应有卡片
    expect(wrapper.findAll('.group').length).toBe(0)
  })

  it('加载态：listSubagents 未 resolve 时显示加载动画', async () => {
    // 不 resolve 的 promise，保持 loading 状态
    listSubagentsMock.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(MyDigitalEmployees, {
      global: {
        provide: { toggleSidebar: () => {} },
      },
    })

    await flushPromises()

    // 加载动画存在
    expect(wrapper.find('.animate-spin').exists()).toBe(true)
    // 卡片网格不应渲染
    expect(wrapper.findAll('.group').length).toBe(0)
  })

  it('列表加载失败时降级为空状态（不抛异常）', async () => {
    listSubagentsMock.mockRejectedValue(new Error('网络错误'))

    const wrapper = mount(MyDigitalEmployees, {
      global: {
        provide: { toggleSidebar: () => {} },
      },
    })

    await flushPromises()

    // 失败后应进入空状态（agents 为 []，loading=false）
    expect(wrapper.text()).toContain('暂无可用的数字员工')
    expect(wrapper.findAll('.group').length).toBe(0)
  })

  it('显示名优先使用 display_name，回退到 instance_name，再回退到 name', async () => {
    listSubagentsMock.mockResolvedValue({
      success: true,
      data: [
        { agent_id: 'a1', name: 'name-a', display_name: '显示名A', description: 'desc', instance_name: '实例名A' },
        { agent_id: 'a2', name: 'name-b', instance_name: '实例名B', description: 'desc' },
        { agent_id: 'a3', name: 'name-c', description: 'desc' },
      ],
    })

    const wrapper = mount(MyDigitalEmployees, {
      global: {
        provide: { toggleSidebar: () => {} },
      },
    })

    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('显示名A')    // a1 用 display_name
    expect(text).toContain('实例名B')    // a2 用 instance_name（无 display_name）
    expect(text).toContain('name-c')     // a3 用 name
    // 不应出现 a1/a2 的 name 字段
    expect(text).not.toContain('name-a')
    expect(text).not.toContain('name-b')
  })
})
