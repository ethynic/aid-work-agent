/**
 * AgentDefinitionManager 保存确认链路单测
 *
 * 独立评审 P2：生效确认与变更说明弹窗必须都在第一个写请求之前，
 * 任一弹窗取消时不得产生任何保存请求（分段若先落库，取消也会影响运行时提示词）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({
  listDefinitions: vi.fn(),
  getDefinition: vi.fn(),
  createDefinition: vi.fn(),
  updateDefinition: vi.fn(),
  importDefinition: vi.fn(),
  updateSystemPrompt: vi.fn(),
  listVersions: vi.fn(),
  getVersion: vi.fn(),
  getSections: vi.fn(),
  saveSection: vi.fn(),
}))

vi.mock('@/api/agentDefinitions', () => ({
  listDefinitions: apiMocks.listDefinitions,
  getDefinition: apiMocks.getDefinition,
  createDefinition: apiMocks.createDefinition,
  updateDefinition: apiMocks.updateDefinition,
  deleteDefinition: vi.fn(),
  updateSystemPrompt: apiMocks.updateSystemPrompt,
  listVersions: apiMocks.listVersions,
  getVersion: apiMocks.getVersion,
  diffVersions: vi.fn(),
  rollbackSystemPrompt: vi.fn(),
  listToolsMeta: vi.fn().mockResolvedValue({ success: true, data: [] }),
  listSkillsMeta: vi.fn().mockResolvedValue({ success: true, data: [] }),
  listReplyStylesMeta: vi.fn().mockResolvedValue({ success: true, data: [] }),
  listRecapTasksMeta: vi.fn().mockResolvedValue({ success: true, data: { tasks: [], when_options: [] } }),
  getSections: apiMocks.getSections,
  saveSection: apiMocks.saveSection,
  optimizeSection: vi.fn(),
  exportDefinition: vi.fn(),
  importDefinition: apiMocks.importDefinition,
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}))

vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ admin: { value: null }, isLoggedIn: { value: false } }),
}))

vi.mock('@/components/chat/toolbar-buttons/registry', () => ({
  listToolbarButtonMeta: () => [],
}))

import AgentDefinitionManager from '@/components/AgentDefinitionManager.vue'

const agentRow = {
  agent_id: 'emp_a',
  name: '员工A',
  description: '',
  status: 'active',
  latest_version: 2,
  tools: { inherit: true },
  skills: {},
}

function mountManager() {
  return mount(AgentDefinitionManager, {
    global: {
      stubs: {
        teleport: true,
        AppHeader: true,
        PageMetaSelector: true,
        MenuIcon: true,
        BaseBadge: true,
      },
    },
  })
}

/** 组装 mock 数据 → 挂载 → 选中含 {{role}} 分段的员工（mock 必须在 mount 前就绪，onMounted 即拉列表） */
async function mountWithAgentSelected(tools: Record<string, unknown> = agentRow.tools, others: typeof agentRow[] = []) {
  apiMocks.listDefinitions.mockResolvedValue({
    success: true, data: { items: [agentRow, ...others], total: 1 + others.length, page: 1, page_size: 100 },
  })
  apiMocks.getDefinition.mockResolvedValue({ success: true, data: { ...agentRow, tools } })
  apiMocks.listVersions.mockResolvedValue({
    success: true, data: {
      items: [
        { version: 2, content: '', commit_message: null, created_at: '' },
        { version: 1, content: '', commit_message: null, created_at: '' },
      ],
    },
  })
  // 模板含 {{role}} 占位符 → 出现「role」分段编辑区
  apiMocks.getVersion.mockResolvedValue({ success: true, data: { version: 2, content: '模板 {{role}}' } })
  apiMocks.getSections.mockResolvedValue({ success: true, data: [{ section_key: 'role', content: '原分段' }] })

  const wrapper = mountManager()
  await flushPromises()
  const row = wrapper.findAll('.cursor-pointer').find(el => el.text().includes('员工A'))
  await row!.trigger('click')
  await flushPromises()
  return wrapper
}

function runtimeCheckboxes(wrapper: ReturnType<typeof mountManager>) {
  return wrapper.findAll('label')
    .filter(label => label.text() === '允许本机 Runtime 插件工具')
    .map(label => label.get<HTMLInputElement>('input[type="checkbox"]'))
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

describe('AgentDefinitionManager 本机 Runtime 插件工具', () => {
  beforeEach(() => vi.clearAllMocks())

  it('在两处同步勾选、保存和重开，并保留已经部署的工具配置', async () => {
    const original = {
      inherit: false,
      allowed: ['http_api', 'boss_send_to'],
      additional: ['read'],
      excluded: ['sample_mcp_excluded'],
      existing_policy: { value: 'keep' },
    }
    const wrapper = await mountWithAgentSelected(original)
    expect(runtimeCheckboxes(wrapper).map(field => field.element.checked)).toEqual([false])
    const toolsLabel = wrapper.findAll('label').find(label => label.text() === '工具配置')!
    toolsLabel.element.parentElement!.querySelector<HTMLButtonElement>('button[title="全屏选择"]')!.click()
    await flushPromises()
    const [fullscreen, inline] = runtimeCheckboxes(wrapper)
    expect([fullscreen.element.checked, inline.element.checked]).toEqual([false, false])
    await fullscreen.setValue(true)
    expect(inline.element.checked).toBe(true)

    apiMocks.updateDefinition.mockImplementation(async (_id, { tools }) => {
      apiMocks.getDefinition.mockResolvedValue({ success: true, data: { ...agentRow, tools } })
      return { success: true }
    })
    const save = wrapper.findAll('button').find(button => button.text() === '保存定义')!
    await save.trigger('click')
    await flushPromises()
    const enabled = { ...original, allow_local_runtime_tools: true }
    expect(apiMocks.updateDefinition).toHaveBeenLastCalledWith('emp_a', expect.objectContaining({ tools: enabled }))

    const close = wrapper.findAll('button').find(button => button.text() === '完成')!
    await close.trigger('click')
    const row = wrapper.findAll('.cursor-pointer').find(el => el.text().includes('员工A'))!
    await row.trigger('click')
    await flushPromises()
    expect(runtimeCheckboxes(wrapper).map(field => field.element.checked)).toEqual([true])
    await runtimeCheckboxes(wrapper)[0].setValue(false)
    await save.trigger('click')
    await flushPromises()
    expect(apiMocks.updateDefinition).toHaveBeenLastCalledWith('emp_a',
      expect.objectContaining({ tools: { ...original, allow_local_runtime_tools: false } }))
    wrapper.unmount()
  })

  it('继承开启时仍可配置，且不会把导入定义中的字符串当成开启', async () => {
    const wrapper = await mountWithAgentSelected({ inherit: true, allow_local_runtime_tools: 'true' })
    expect(runtimeCheckboxes(wrapper).map(field => field.element.checked)).toEqual([false])
    wrapper.unmount()
  })

  it('新建员工不会沿用上一个员工的开启状态', async () => {
    const wrapper = await mountWithAgentSelected({ allow_local_runtime_tools: true })
    expect(runtimeCheckboxes(wrapper)[0].element.checked).toBe(true)
    const created = { ...agentRow, agent_id: 'emp_new', name: '新员工', tools: {} }
    apiMocks.createDefinition.mockImplementation(async () => {
      apiMocks.listDefinitions.mockResolvedValue({ success: true, data: { items: [agentRow, created] } })
      apiMocks.getDefinition.mockResolvedValue({ success: true, data: created })
      return { success: true }
    })
    await wrapper.findAll('button').find(button => button.text() === '新建智能体')!.trigger('click')
    await wrapper.get('input[placeholder="如 after-sales-v2"]').setValue(created.agent_id)
    await wrapper.get('input[placeholder="如 售后服务助手"]').setValue(created.name)
    await wrapper.get('textarea[placeholder^="输入初始 System Prompt"]').setValue('新员工的提示词')
    await wrapper.findAll('button').find(button => button.text() === '创建')!.trigger('click')
    await flushPromises()
    expect(apiMocks.createDefinition.mock.calls[0][0].tools).toBeUndefined()
    expect(runtimeCheckboxes(wrapper)[0].element.checked).toBe(false)
    wrapper.unmount()
  })

  it('导入的布尔值在重新打开的工具配置中生效', async () => {
    const wrapper = await mountWithAgentSelected()
    const imported = { ...agentRow, agent_id: 'emp_import', tools: { allow_local_runtime_tools: true } }
    apiMocks.importDefinition.mockImplementation(async () => {
      apiMocks.listDefinitions.mockResolvedValue({ success: true, data: { items: [imported] } })
      apiMocks.getDefinition.mockResolvedValue({ success: true, data: imported })
      return { ok: true, status: 201, body: { success: true, data: { agent_id: imported.agent_id } } }
    })
    const upload = wrapper.get<HTMLInputElement>('input[type="file"][accept=".json,application/json"]')
    Object.defineProperty(upload.element, 'files', { value: [{
      text: async () => JSON.stringify({ format: 'agent_definition_export', version: 1, definition: imported }),
    }] })
    await upload.trigger('change')
    await flushPromises()
    expect(apiMocks.importDefinition).toHaveBeenCalledWith(expect.objectContaining({ definition: imported }))
    expect(runtimeCheckboxes(wrapper)[0].element.checked).toBe(true)
    wrapper.unmount()
  })

  it('切换时阻止旧表单保存，迟到的详情不会把员工 A 的权限写给员工 B', async () => {
    const first = { ...agentRow, tools: { inherit: false, allowed: ['http_api'], allow_local_runtime_tools: true } }
    const second = { ...agentRow, agent_id: 'emp_b', name: '员工B', tools: { inherit: false, allowed: ['read'] } }
    const wrapper = await mountWithAgentSelected(first.tools, [second])
    const row = (name: string) => wrapper.findAll('.cursor-pointer').find(el => el.text().includes(name))!
    const save = wrapper.findAll('button').find(button => button.text() === '保存定义')!
    const staleFirst = deferred<{ success: true; data: typeof first }>()
    const pendingSecond = deferred<{ success: true; data: typeof second }>()
    apiMocks.getDefinition.mockImplementation(id => id === first.agent_id ? staleFirst.promise : pendingSecond.promise)

    await row(second.name).trigger('click')
    expect(save.attributes('disabled')).toBeDefined()
    // A queued click can reach the handler even if the button is now disabled.
    save.element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(apiMocks.updateDefinition).not.toHaveBeenCalled()

    await row(first.name).trigger('click')
    await row(second.name).trigger('click')
    pendingSecond.resolve({ success: true, data: second })
    await flushPromises()
    staleFirst.resolve({ success: true, data: first })
    await flushPromises()

    expect(runtimeCheckboxes(wrapper)[0].element.checked).toBe(false)
    expect(save.attributes('disabled')).toBeUndefined()
    apiMocks.updateDefinition.mockResolvedValue({ success: true })
    await save.trigger('click')
    await flushPromises()
    expect(apiMocks.updateDefinition).toHaveBeenCalledExactlyOnceWith(second.agent_id, expect.objectContaining({
      tools: { ...second.tools, allow_local_runtime_tools: false },
    }))
    wrapper.unmount()
  })

  it('切回同一员工时只采用最后一次请求返回的工具配置', async () => {
    const second = { ...agentRow, agent_id: 'emp_b', name: '员工B' }
    const wrapper = await mountWithAgentSelected(agentRow.tools, [second])
    const row = (name: string) => wrapper.findAll('.cursor-pointer').find(el => el.text().includes(name))!
    const stale = { ...agentRow, tools: { inherit: true, allow_local_runtime_tools: true } }
    const first = deferred<{ success: true; data: typeof stale }>()
    apiMocks.getDefinition.mockImplementationOnce(() => first.promise)
      .mockResolvedValueOnce({ success: true, data: second })
      .mockResolvedValueOnce({ success: true, data: agentRow })

    await row(agentRow.name).trigger('click')
    await row(second.name).trigger('click')
    await row(agentRow.name).trigger('click')
    await flushPromises()
    first.resolve({ success: true, data: stale })
    await flushPromises()

    expect(runtimeCheckboxes(wrapper)[0].element.checked).toBe(false)
    wrapper.unmount()
  })
})

function findSaveButton(wrapper: any) {
  return wrapper.findAll('button').find((b: any) => b.text().trim() === '保存')
}

function findSectionTextarea(wrapper: any) {
  return wrapper.find('textarea[placeholder="输入 role 的内容..."]')
}

describe('AgentDefinitionManager 保存确认链路', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    vi.spyOn(window, 'prompt').mockReturnValue(null)
  })

  it('生效确认弹窗取消：不发分段保存、也不发提示词保存', async () => {
    const wrapper = await mountWithAgentSelected()

    // 修改分段变量（修复前：此改动会先于确认弹窗落库）
    const sectionTextarea = findSectionTextarea(wrapper)
    expect(sectionTextarea).toBeTruthy()
    await sectionTextarea.setValue('改过的分段')

    findSaveButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith('保存后提示词将立即生效，是否保存？')
    expect(apiMocks.saveSection).not.toHaveBeenCalled()
    expect(apiMocks.updateSystemPrompt).not.toHaveBeenCalled()
  })

  it('确认后变更说明取消：仍不发任何保存请求', async () => {
    const wrapper = await mountWithAgentSelected()

    const sectionTextarea = findSectionTextarea(wrapper)
    await sectionTextarea.setValue('改过的分段')

    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(window, 'prompt').mockReturnValue(null) // 取消变更说明

    findSaveButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(apiMocks.saveSection).not.toHaveBeenCalled()
    expect(apiMocks.updateSystemPrompt).not.toHaveBeenCalled()
  })

  it('两个弹窗都确认：先存分段、再保存提示词', async () => {
    const wrapper = await mountWithAgentSelected()

    const sectionTextarea = findSectionTextarea(wrapper)
    await sectionTextarea.setValue('改过的分段')

    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(window, 'prompt').mockReturnValue('测试说明')
    apiMocks.saveSection.mockResolvedValue({ success: true, data: {} })
    apiMocks.updateSystemPrompt.mockResolvedValue({ success: true, data: {} })

    findSaveButton(wrapper)!.trigger('click')
    await flushPromises()

    expect(apiMocks.saveSection).toHaveBeenCalledWith('emp_a', 'role', '改过的分段')
    expect(apiMocks.updateSystemPrompt).toHaveBeenCalledWith('emp_a', {
      content: '模板 {{role}}',
      commit_message: '测试说明',
    })
  })
})
