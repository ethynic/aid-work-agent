/**
 * TenantAgentCustomizeDialog 组件单测
 *
 * 验证关闭弹框时重置表单：form 在组件存活期间复用，若关闭时不清空，
 * 下次打开会在接口返回前先渲染上一个员工的显示名/扩展提示词。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const getInstanceMock = vi.fn()
vi.mock('@/api/tenantAgentInstances', () => ({
  getTenantAgentInstanceOrDefault: (...args: any[]) => getInstanceMock(...args),
  updateTenantAgentInstance: vi.fn(),
  createTenantAgentInstance: vi.fn(),
  deleteTenantAgentInstance: vi.fn(),
}))

vi.mock('@/api/subagentTemplates', () => ({
  listTemplates: vi.fn().mockResolvedValue({ success: true, data: [] }),
  uploadTemplate: vi.fn(),
  deleteTemplate: vi.fn(),
  getTemplateDownloadUrl: (fileId: string) => `/api/files/${fileId}/download`,
}))

// 打开弹框时会预查上一版（回退入口显隐），默认无可回退
const getPreviousExtraMock = vi.fn()
vi.mock('@/api/subagentExtra', () => ({
  getPreviousExtra: (...args: any[]) => getPreviousExtraMock(...args),
  rollbackExtra: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ resolve: () => ({ href: '/t/t1/knowledge' }) }),
}))

import TenantAgentCustomizeDialog from '@/components/TenantAgentCustomizeDialog.vue'

function mountDialog() {
  return mount(TenantAgentCustomizeDialog, {
    props: {
      modelValue: false,
      tenantId: 't1',
      agentId: 'a1',
      agentName: '员工A',
    },
    global: { stubs: { teleport: true } },
  })
}

const instanceA = {
  agent_id: 'a1',
  base_agent_id: 'a1',
  base_source: null,
  display_name: '员工A',
  description: 'A 的描述',
  extra_prompt: 'A 的扩展提示词',
  greeting_summary: 'A 的摘要',
  greeting_prompts: [{ label: '按钮A', message: '指令A' }],
  is_copy: false,
  status: 'active',
}

describe('TenantAgentCustomizeDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('关闭时重置 form：重新打开不显示上一个员工的数据', async () => {
    getInstanceMock.mockResolvedValue(instanceA)
    const wrapper = mountDialog()

    // 打开并等待实例数据回填
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    expect(wrapper.find('input[placeholder="留空则使用默认名称"]').element).toHaveProperty('value', '员工A')
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('A 的扩展提示词')

    // 关闭 -> 重置
    await wrapper.setProps({ modelValue: false })

    // 换成另一个员工重新打开：接口未返回前不应出现上一个员工的数据
    getInstanceMock.mockReturnValue(new Promise(() => {}))
    await wrapper.setProps({ modelValue: true, agentId: 'a2', agentName: '员工B' })
    await flushPromises()

    expect(wrapper.find('input[placeholder="留空则使用默认名称"]').element).toHaveProperty('value', '')
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.find('input[placeholder="留空则使用默认描述"]').element).toHaveProperty('value', '')
    expect(wrapper.find('input[placeholder="按钮文案，≤6 字"]').element).toHaveProperty('value', '')
  })

  it('上一版为空（清空态）仍显示回退入口，确认框提示回退后使用默认配置', async () => {
    // 独立评审 P1：清空是生效状态，链上记录为空版本；回退入口显隐以 version 为准，
    // 空串上一版与「无可回退」区分，确认框明确提示回退后使用默认配置
    getInstanceMock.mockResolvedValue(instanceA)
    getPreviousExtraMock.mockResolvedValue({ success: true, content: '', version: 2 })
    const wrapper = mountDialog()

    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    const rollbackBtn = wrapper.findAll('button').find(b => b.text().includes('回退上一版'))
    expect(rollbackBtn).toBeTruthy()

    await rollbackBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('使用默认配置')
    expect(wrapper.text()).not.toContain('内容预览如下')
  })

  it('上一版有内容时确认框展示全文预览', async () => {
    getInstanceMock.mockResolvedValue(instanceA)
    getPreviousExtraMock.mockResolvedValue({ success: true, content: '上一版提示词全文', version: 3 })
    const wrapper = mountDialog()

    await wrapper.setProps({ modelValue: true })
    await flushPromises()

    const rollbackBtn = wrapper.findAll('button').find(b => b.text().includes('回退上一版'))
    await rollbackBtn!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('内容预览如下')
    expect(wrapper.text()).toContain('上一版提示词全文')
  })
})
