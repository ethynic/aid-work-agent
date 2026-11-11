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
  updateSystemPrompt: vi.fn(),
  listVersions: vi.fn(),
  getVersion: vi.fn(),
  getSections: vi.fn(),
  saveSection: vi.fn(),
}))

vi.mock('@/api/agentDefinitions', () => ({
  listDefinitions: apiMocks.listDefinitions,
  getDefinition: apiMocks.getDefinition,
  createDefinition: vi.fn(),
  updateDefinition: vi.fn(),
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
  importDefinition: vi.fn(),
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
        FullscreenPicker: true,
        MenuIcon: true,
        BaseBadge: true,
      },
    },
  })
}

/** 组装 mock 数据 → 挂载 → 选中含 {{role}} 分段的员工（mock 必须在 mount 前就绪，onMounted 即拉列表） */
async function mountWithAgentSelected() {
  apiMocks.listDefinitions.mockResolvedValue({
    success: true, data: { items: [agentRow], total: 1, page: 1, page_size: 100 },
  })
  apiMocks.getDefinition.mockResolvedValue({ success: true, data: { ...agentRow } })
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
