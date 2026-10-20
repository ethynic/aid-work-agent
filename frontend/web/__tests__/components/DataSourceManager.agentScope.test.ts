/**
 * DataSourceManager 可用智能体配置交互测试（Phase 3，设计 §18.8）
 *
 * 覆盖：范围显示格式（全部/限定/N+1）、普通用户只读、弹框草稿保存/取消、
 * 全部与指定互斥（取消最后一位不隐式共享）、批量并发与部分失败统计与重试、
 * 导入批次草稿不写库与首张入库锁定、失效 ID 展示与移除、目录加载失败不覆盖。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { ref } from 'vue'

enableAutoUnmount(afterEach)

// ===== mocks（vi.mock 工厂会被提升，可变状态经 vi.hoisted 中转） =====

const ds = vi.hoisted(() => ({
  listSchemas: vi.fn(),
  listConnectors: vi.fn(),
  listRemoteTables: vi.fn(),
  importTables: vi.fn(),
  updateAgentScope: vi.fn(),
  saveSchema: vi.fn(),
  updateSchema: vi.fn(),
  deleteSchema: vi.fn(),
  listRelations: vi.fn(),
}))

const subagents = vi.hoisted(() => ({
  listSubagents: vi.fn(),
}))

const auth = vi.hoisted(() => ({
  adminRef: null as { value: { role: string } | null } | null,
}))

vi.mock('@/api/dataSource', () => ({
  listConnectors: ds.listConnectors,
  createConnector: vi.fn(),
  updateConnector: vi.fn(),
  deleteConnector: vi.fn(),
  testConnection: vi.fn(),
  testSavedConnector: vi.fn(),
  listRemoteTables: ds.listRemoteTables,
  importTables: ds.importTables,
  uploadExcelStream: vi.fn(),
  listSchemas: ds.listSchemas,
  saveSchema: ds.saveSchema,
  updateSchema: ds.updateSchema,
  updateAgentScope: ds.updateAgentScope,
  deleteSchema: ds.deleteSchema,
  inferRelations: vi.fn(),
  listRelations: ds.listRelations,
  addRelation: vi.fn(),
  deleteRelation: vi.fn(),
}))

vi.mock('@/api/subagent', () => ({
  listSubagents: subagents.listSubagents,
}))

vi.mock('@/api/knowledge', () => ({
  downloadDocument: vi.fn(),
}))

vi.mock('@/composables/useTenantAuth', async () => {
  const admin = ref<{ role: string } | null>(null)
  auth.adminRef = admin
  return {
    useTenantAuth: () => ({
      admin,
      tenant: ref(null),
      saasToken: ref('token'),
      isLoggedIn: ref(true),
      isLoading: ref(false),
      isInitialized: ref(true),
      init: vi.fn(),
      setLogin: vi.fn(),
      logout: vi.fn(),
      getCurrentTenantId: vi.fn(() => null),
      getAuthHeader: vi.fn(() => ({})),
    }),
  }
})

import DataSourceManager from '@/pages/DataSourceManager.vue'

// ===== 测试数据与工具 =====

const AGENT_CATALOG = [
  { agent_id: 'main', name: 'CEO智能体' },
  { agent_id: 'sales-assistant', name: '销售助手' },
  { agent_id: 'finance-assistant', name: '财务助手' },
]

function schemaRow(id: number, overrides: Record<string, any> = {}) {
  return {
    id,
    title: `表${id}`,
    source_type: 'excel',
    metadata: { source_info: 'file:/tmp/a.xlsx', columns: [{ name: 'a' }] },
    created_at: '2026-10-20T00:00:00',
    allowed_agent_ids: null,
    ...overrides,
  }
}

async function mountPage() {
  const wrapper = mount(DataSourceManager, {
    attachTo: document.body,
    global: { stubs: { teleport: true } },
  })
  await flushPromises()
  return wrapper
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find(b => b.text().trim() === text)
}

// 数据表行内按钮顺序：可用智能体（管理员）、编辑、删除
function scopeButtonOf(wrapper: ReturnType<typeof mount>, rowIndex: number) {
  return wrapper.findAll('tbody tr')[rowIndex].findAll('button')[0]
}

async function clickButton(wrapper: ReturnType<typeof mount>, text: string) {
  const btn = findButtonByText(wrapper, text)
  if (!btn) throw new Error(`button not found: ${text}`)
  await btn.trigger('click')
  await flushPromises()
}

const alertMock = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  if (!auth.adminRef) throw new Error('auth mock not initialized')
  auth.adminRef.value = { role: 'tenant_admin' }
  ds.listSchemas.mockResolvedValue({
    schemas: [schemaRow(1), schemaRow(2)],
    total: 2,
    has_outbound_knowledge_share: false,
  })
  ds.listConnectors.mockResolvedValue([])
  ds.listRelations.mockResolvedValue([])
  subagents.listSubagents.mockResolvedValue({ success: true, data: AGENT_CATALOG })
  vi.stubGlobal('alert', alertMock)
  vi.stubGlobal('confirm', vi.fn(() => false))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ===== 范围显示与权限 =====

describe('可用智能体列显示', () => {
  it('共享表显示「全部智能体」，单智能体显示名称，多个显示「名称 +N」', async () => {
    ds.listSchemas.mockResolvedValue({
      schemas: [
        schemaRow(1),
        schemaRow(2, { allowed_agent_ids: ['sales-assistant'] }),
        schemaRow(3, { allowed_agent_ids: ['sales-assistant', 'finance-assistant', 'main'] }),
      ],
      total: 3,
      has_outbound_knowledge_share: false,
    })
    const wrapper = await mountPage()

    const cells = wrapper.findAll('td')
    const texts = cells.map(c => c.text())
    expect(texts).toContain('全部智能体')
    expect(texts).toContain('销售助手')
    expect(texts).toContain('销售助手 +2')
  })

  it('名单中的 ID 不在目录时显示「名称（已不可用）」', async () => {
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1, { allowed_agent_ids: ['sales-assistant', 'legacy-agent'] })],
      total: 1,
      has_outbound_knowledge_share: false,
    })
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('销售助手 +1')
    // 失效 ID 打开弹框可见
    await clickButton(wrapper, '销售助手 +1')
    expect(wrapper.text()).toContain('legacy-agent（已不可用）')
  })

  it('目录加载失败：已有名称/范围保留，共享表不受影响，页面正常渲染', async () => {
    subagents.listSubagents.mockRejectedValue(new Error('network'))
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1, { allowed_agent_ids: ['sales-assistant'] })],
      total: 1,
      has_outbound_knowledge_share: false,
    })
    const wrapper = await mountPage()
    // 目录不可用时限定表以 ID+失效标识展示，绝不显示成共享
    expect(wrapper.text()).toContain('sales-assistant（已不可用）')
  })
})

describe('权限与编辑入口', () => {
  it('管理员：范围单元格是可点击按钮，打开配置弹框并回显当前范围', async () => {
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1, { allowed_agent_ids: ['sales-assistant'] })],
      total: 1,
      has_outbound_knowledge_share: false,
    })
    const wrapper = await mountPage()

    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('仅指定智能体')
    // 回显：指定模式下销售助手已勾选
    const sales = wrapper.find('input[aria-label="销售助手"]')
    expect((sales.element as HTMLInputElement).checked).toBe(true)
  })

  it('普通用户：范围只读文本、无选择列、无批量操作', async () => {
    auth.adminRef!.value = { role: 'user' }
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1, { allowed_agent_ids: ['sales-assistant'] })],
      total: 1,
      has_outbound_knowledge_share: false,
    })
    const wrapper = await mountPage()

    // 行内只剩编辑/删除，无范围编辑按钮
    const rowButtons = wrapper.findAll('tbody tr')[0].findAll('button').map(b => b.text().trim())
    expect(rowButtons).toEqual(['编辑', '删除'])
    expect(wrapper.text()).toContain('销售助手')
    // 无选择列与全选框
    expect(wrapper.find('input[aria-label="全选当前页"]').exists()).toBe(false)
  })

  it('角色未就绪：范围只读，不影响列表加载', async () => {
    auth.adminRef!.value = null
    const wrapper = await mountPage()
    const rowButtons = wrapper.findAll('tbody tr')[0].findAll('button').map(b => b.text().trim())
    expect(rowButtons).toEqual(['编辑', '删除'])
    expect(wrapper.text()).toContain('表1')
  })

  it('管理员范围控件是原生 button（自带 Tab/Enter/Space 激活语义）且带可见焦点样式', async () => {
    const wrapper = await mountPage()
    const btn = scopeButtonOf(wrapper, 0)
    // 原生 button 的 Enter/Space 激活是浏览器内建行为，jsdom 不模拟；
    // 这里断言元素身份（BUTTON + type=button）与焦点环类名，保证真实浏览器键盘可达
    expect(btn.element.tagName).toBe('BUTTON')
    expect(btn.attributes('type')).toBe('button')
    expect(btn.classes()).toContain('focus:ring-2')
  })
})

// ===== 单表弹框：草稿、互斥、取消 =====

describe('单表配置弹框', () => {
  it('草稿保存：修改勾选后点保存才调用 agent-scope 并更新行显示', async () => {
    ds.updateAgentScope.mockResolvedValue({
      success: true,
      doc_id: 2,
      allowed_agent_ids: ['sales-assistant', 'finance-assistant'],
    })
    const wrapper = await mountPage()

    // 打开第二张表（共享）
    await scopeButtonOf(wrapper, 1).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('全部智能体（默认）')

    // 点击名字即进入指定模式并选中
    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    await wrapper.find('input[aria-label="财务助手"]').setValue(true)
    await clickButton(wrapper, '保存')

    expect(ds.updateAgentScope).toHaveBeenCalledTimes(1)
    expect(ds.updateAgentScope).toHaveBeenCalledWith(2, ['sales-assistant', 'finance-assistant'])
    // 行数据已更新
    expect(wrapper.text()).toContain('销售助手 +1')
  })

  it('取消/关闭不写库，行数据不变', async () => {
    const wrapper = await mountPage()
    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()

    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    await clickButton(wrapper, '取消')

    expect(ds.updateAgentScope).not.toHaveBeenCalled()
    // 弹框关闭
    expect(wrapper.text()).not.toContain('全部智能体（默认）')
    // 重新打开：草稿已丢弃，仍是共享
    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()
    const allRadio = wrapper.findAll('input[name="agent-scope-choice"]')[0]
    expect((allRadio.element as HTMLInputElement).checked).toBe(true)
  })

  it('互斥：选「全部智能体」清空指定草稿，保存恢复共享（提交 null）', async () => {
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1, { allowed_agent_ids: ['sales-assistant'] })],
      total: 1,
      has_outbound_knowledge_share: false,
    })
    ds.updateAgentScope.mockResolvedValue({ success: true, doc_id: 1, allowed_agent_ids: [] })
    const wrapper = await mountPage()

    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()
    expect((wrapper.find('input[aria-label="销售助手"]').element as HTMLInputElement).checked).toBe(true)

    // 选全部 → 指定草稿清空
    const radios = wrapper.findAll('input[name="agent-scope-choice"]')
    await radios[0].setValue(true)
    expect((wrapper.find('input[aria-label="销售助手"]').element as HTMLInputElement).checked).toBe(false)

    await clickButton(wrapper, '保存')
    expect(ds.updateAgentScope).toHaveBeenCalledWith(1, null)
    expect(wrapper.text()).toContain('全部智能体')
  })

  it('取消最后一位保持「仅指定」并提示，不隐式变共享；此时不可保存', async () => {
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1, { allowed_agent_ids: ['sales-assistant'] })],
      total: 1,
      has_outbound_knowledge_share: false,
    })
    const wrapper = await mountPage()

    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()

    await wrapper.find('input[aria-label="销售助手"]').setValue(false)
    const radios = wrapper.findAll('input[name="agent-scope-choice"]')
    expect((radios[1].element as HTMLInputElement).checked).toBe(true) // 仍是指定模式
    expect(wrapper.text()).toContain('请选择至少一个智能体，或选择全部智能体')

    const saveBtn = findButtonByText(wrapper, '保存')
    expect(saveBtn!.attributes('disabled')).toBeDefined()
    expect(ds.updateAgentScope).not.toHaveBeenCalled()
  })

  it('指定模式且租户存在出向共享时显示条件文案', async () => {
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1)],
      total: 1,
      has_outbound_knowledge_share: true,
    })
    const wrapper = await mountPage()

    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('如该数据已对外共享')

    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    expect(wrapper.text()).toContain('如该数据已对外共享，指定后其他租户将无法使用。')
  })

  it('失效 ID 可主动移除后保存：移除后不能隐式变共享，需再选定目标', async () => {
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1, { allowed_agent_ids: ['legacy-agent'] })],
      total: 1,
      has_outbound_knowledge_share: false,
    })
    ds.updateAgentScope.mockResolvedValue({ success: true, doc_id: 1, allowed_agent_ids: ['sales-assistant'] })
    const wrapper = await mountPage()

    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()
    // 失效 ID 展示且默认保留勾选
    const legacy = wrapper.find('input[aria-label="legacy-agent（已不可用）"]')
    expect((legacy.element as HTMLInputElement).checked).toBe(true)

    await legacy.setValue(false)
    // 移除最后一位后保持指定模式且不可保存（不隐式共享）
    const saveBtn = findButtonByText(wrapper, '保存')
    expect(saveBtn!.attributes('disabled')).toBeDefined()

    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    await clickButton(wrapper, '保存')
    expect(ds.updateAgentScope).toHaveBeenCalledWith(1, ['sales-assistant'])
  })

  it('保存失败：保留弹框与草稿并提示原因', async () => {
    ds.updateAgentScope.mockRejectedValue(new Error('没有权限'))
    const wrapper = await mountPage()

    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()
    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    await clickButton(wrapper, '保存')

    // 弹框未关闭，草稿保留，错误可见
    expect(wrapper.text()).toContain('全部智能体（默认）')
    expect((wrapper.find('input[aria-label="销售助手"]').element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.text()).toContain('没有权限')
  })

  it('保存进行中：右上角关闭（X）不关闭弹框，保存完成后自动关闭并更新行', async () => {
    let resolveSave!: (v: { success: boolean; doc_id: number; allowed_agent_ids: string[] }) => void
    ds.updateAgentScope.mockImplementation(
      () => new Promise(res => { resolveSave = res })
    )
    const wrapper = await mountPage()

    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()
    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    await clickButton(wrapper, '保存')
    expect(findButtonByText(wrapper, '保存中...')).toBeDefined()
    expect(wrapper.find('input[name="agent-scope-choice"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('input[aria-label="销售助手"]').attributes('disabled')).toBeDefined()

    // 弹框头部图标按钮：全屏切换与关闭（X），取最后一个即 X
    const iconButtons = wrapper
      .findAll('button')
      .filter(b => !b.text().trim() && b.find('svg').exists())
    expect(iconButtons.length).toBeGreaterThanOrEqual(2)
    await iconButtons[iconButtons.length - 1].trigger('click')
    await flushPromises()

    // 保存进行中不允许任何关闭入口，弹框保持打开
    expect(findButtonByText(wrapper, '保存中...')).toBeDefined()

    resolveSave({ success: true, doc_id: 1, allowed_agent_ids: ['sales-assistant'] })
    await flushPromises()
    // 完成后弹框自动关闭，行显示已保存值
    expect(wrapper.text()).not.toContain('全部智能体（默认）')
    expect(wrapper.text()).toContain('销售助手')
  })

  it('名称列表支持搜索且已选项搜索后保留', async () => {
    // 选项超过 8 个才展示搜索框
    const manyAgents = Array.from({ length: 10 }, (_, i) => ({
      agent_id: `agent-${i}`,
      name: `智能体${i}`,
    }))
    manyAgents.push({ agent_id: 'finance-assistant', name: '财务助手' })
    subagents.listSubagents.mockResolvedValue({ success: true, data: manyAgents })

    const wrapper = await mountPage()
    await scopeButtonOf(wrapper, 0).trigger('click')
    await flushPromises()

    const search = wrapper.find('input[placeholder="搜索智能体名称..."]')
    expect(search.exists()).toBe(true)
    await search.setValue('财务')
    expect(wrapper.text()).not.toContain('智能体0')
    expect(wrapper.text()).toContain('财务助手')

    await wrapper.find('input[aria-label="财务助手"]').setValue(true)
    await search.setValue('')
    // 清空搜索后勾选保留
    expect((wrapper.find('input[aria-label="财务助手"]').element as HTMLInputElement).checked).toBe(true)
  })
})

// ===== 批量设置 =====

describe('批量设置可用智能体', () => {
  function mockScopeResults(failIds: number[]) {
    let inFlight = 0
    const tracker = { maxInFlight: 0, calls: [] as { id: number; ids: string[] | null }[] }
    ds.updateAgentScope.mockImplementation(async (id: number, ids: string[] | null) => {
      inFlight++
      tracker.maxInFlight = Math.max(tracker.maxInFlight, inFlight)
      await Promise.resolve()
      inFlight--
      tracker.calls.push({ id, ids: ids as string[] | null })
      if (failIds.includes(id)) throw new Error('boom')
      return { success: true, doc_id: id, allowed_agent_ids: ids ?? [] }
    })
    return tracker
  }

  it('选中后工具条出现，统一替换保存：成功更新全部行并反馈一次', async () => {
    const tracker = mockScopeResults([])
    const wrapper = await mountPage()

    // 勾选两张表
    await wrapper.find('input[aria-label="选择 表1"]').setValue(true)
    await wrapper.find('input[aria-label="选择 表2"]').setValue(true)
    expect(wrapper.text()).toContain('已选 2 张表')

    await clickButton(wrapper, '设置可用智能体')
    // 范围一致（均为共享）回显全部智能体；替换说明可见
    expect(wrapper.text()).toContain('将这 2 张表统一设为所选智能体可用，替换原设置')

    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    await clickButton(wrapper, '保存')

    expect(tracker.calls.map(c => c.id).sort()).toEqual([1, 2])
    expect(tracker.calls.every(c => c.ids && c.ids[0] === 'sales-assistant')).toBe(true)
    expect(alertMock).toHaveBeenCalledWith('已更新 2 张表')
    expect(wrapper.text()).toContain('销售助手')
    // 成功后清空选择，批量条消失
    expect(wrapper.text()).not.toContain('已选 2 张表')
  })

  it('最多 5 并发', async () => {
    const many = Array.from({ length: 12 }, (_, i) => schemaRow(i + 1))
    ds.listSchemas.mockResolvedValue({ schemas: many, total: 12, has_outbound_knowledge_share: false })
    const tracker = mockScopeResults([])
    const wrapper = await mountPage()

    await wrapper.find('input[aria-label="全选当前页"]').setValue(true)
    await clickButton(wrapper, '设置可用智能体')
    await clickButton(wrapper, '保存')

    expect(tracker.calls).toHaveLength(12)
    expect(tracker.maxInFlight).toBeLessThanOrEqual(5)
  })

  it('部分失败：真实成功/失败计数，失败表保留选中可只重试失败表', async () => {
    mockScopeResults([2])
    const wrapper = await mountPage()

    await wrapper.find('input[aria-label="选择 表1"]').setValue(true)
    await wrapper.find('input[aria-label="选择 表2"]').setValue(true)
    await clickButton(wrapper, '设置可用智能体')
    // 统一设为仅销售助手
    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    await clickButton(wrapper, '保存')

    expect(wrapper.text()).toContain('已更新 1 张表，1 张失败；可直接保存重试失败表')
    expect((wrapper.find('input[aria-label="销售助手"]').element as HTMLInputElement).checked).toBe(true)
    // 成功表显示已保存值，失败表保持原值
    expect(wrapper.text()).toContain('销售助手')
    expect(wrapper.text()).toContain('表2')

    // 只剩失败表选中，重试一次成功
    alertMock.mockClear()
    const retryTracker = mockScopeResults([])
    // 弹框和目标草稿保持，直接保存重试，不重新选择智能体。
    await clickButton(wrapper, '保存')

    expect(retryTracker.calls).toEqual([{ id: 2, ids: ['sales-assistant'] }]) // 仅重试失败表
    expect(alertMock).toHaveBeenCalledWith('已更新 1 张表')
  })

  it('混合范围批量打开时不预选目标，未选定不可保存', async () => {
    ds.listSchemas.mockResolvedValue({
      schemas: [schemaRow(1), schemaRow(2, { allowed_agent_ids: ['finance-assistant'] })],
      total: 2,
      has_outbound_knowledge_share: false,
    })
    const wrapper = await mountPage()

    await wrapper.find('input[aria-label="选择 表1"]').setValue(true)
    await wrapper.find('input[aria-label="选择 表2"]').setValue(true)
    await clickButton(wrapper, '设置可用智能体')

    const radios = wrapper.findAll('input[name="agent-scope-choice"]')
    expect((radios[0].element as HTMLInputElement).checked).toBe(false)
    expect((radios[1].element as HTMLInputElement).checked).toBe(false)
    const saveBtn = findButtonByText(wrapper, '保存')
    expect(saveBtn!.attributes('disabled')).toBeDefined()
    expect(ds.updateAgentScope).not.toHaveBeenCalled()
  })

  it('换页/搜索后清空选择', async () => {
    const wrapper = await mountPage()
    await wrapper.find('input[aria-label="选择 表1"]').setValue(true)
    expect(wrapper.text()).toContain('已选 1 张表')

    // 搜索清空
    const searchInput = wrapper.find('input[placeholder="搜索表名..."]')
    await searchInput.setValue('表1')
    await clickButton(wrapper, '搜索')
    expect(wrapper.text()).not.toContain('已选 1 张表')
  })
})

// ===== 导入批次 =====

describe('导入批次可用智能体', () => {
  const connectorRow = {
    id: 'c1',
    name: '测试库',
    db_type: 'mysql',
    host: 'h',
    port: 3306,
    database_name: 'db',
    username: 'u',
    is_active: true,
    imported_tables: [],
    last_sync_at: null,
    created_at: '',
  }

  async function openReviewViaConnectorImport(schemas: any[]) {
    // 连接器数据必须在挂载前就绪：连接器列表在 onMounted 加载一次
    ds.listConnectors.mockResolvedValue([connectorRow])
    ds.listRemoteTables.mockResolvedValue([
      { table_name: 't1', row_count: 1, columns: [] },
    ])
    ds.importTables.mockResolvedValue({ schemas })

    const wrapper = await mountPage()
    // 切到连接器页签
    await clickButton(wrapper, '数据连接器')
    await clickButton(wrapper, '导入')
    // 勾选远程表并导入
    const checkbox = wrapper.find('input[type="checkbox"]')
    await checkbox.setValue(true)
    await clickButton(wrapper, '导入选中表')
    await flushPromises()
    return wrapper
  }

  function schemaInfo(name: string) {
    return {
      table_name: name,
      description: '',
      source_info: 'mysql://c1/t1',
      columns: [{ name: 'a', semantic_name: '', data_type: 'text', description: '' }],
    }
  }

  it('默认共享零操作；更改后弹框保存只更新本批草稿不写库，名单随后续表提交；首张入库后锁定', async () => {
    ds.saveSchema
      .mockResolvedValueOnce({ success: true, existing: false, allowed_agent_ids: ['sales-assistant'] })
      .mockResolvedValueOnce({ success: true, existing: false, allowed_agent_ids: ['sales-assistant'] })
    const wrapper = await openReviewViaConnectorImport([schemaInfo('表A'), schemaInfo('表B')])

    // 紧凑摘要默认显示全部智能体
    expect(wrapper.text()).toContain('本批新表可用智能体：全部智能体')
    expect(wrapper.text()).not.toContain('销售助手')

    // 更改 → 选定名单 → 弹框保存
    await clickButton(wrapper, '更改')
    expect(wrapper.text()).toContain('设置本批新表可用智能体')
    await wrapper.find('input[aria-label="销售助手"]').setValue(true)
    await clickButton(wrapper, '保存')

    // 草稿只更新前端状态，不调用 agent-scope 写库
    expect(ds.updateAgentScope).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('本批新表可用智能体：销售助手')

    // 首张确认入库：名单随请求提交
    await clickButton(wrapper, '确认入库')
    expect(ds.saveSchema).toHaveBeenCalledTimes(1)
    expect(ds.saveSchema.mock.calls[0][0].allowed_agent_ids).toEqual(['sales-assistant'])

    // 首张成功后摘要锁定为只读，已绑定值继续随第二张提交
    expect(wrapper.text()).toContain('本批新表可用智能体：销售助手')
    expect(wrapper.text()).toContain('本批设置已锁定')
    expect(findButtonByText(wrapper, '更改')).toBeUndefined()

    await clickButton(wrapper, '确认入库')
    expect(ds.saveSchema).toHaveBeenCalledTimes(2)
    expect(ds.saveSchema.mock.calls[1][0].allowed_agent_ids).toEqual(['sales-assistant'])
  })

  it('取消/关闭丢弃未保存勾选；本批从未保存过则按共享入库（不传名单字段）', async () => {
    ds.saveSchema.mockResolvedValue({ success: true, existing: false, allowed_agent_ids: [] })
    const wrapper = await openReviewViaConnectorImport([schemaInfo('表A')])

    await clickButton(wrapper, '更改')
    await wrapper.find('input[aria-label="财务助手"]').setValue(true)
    // 取消丢弃
    await clickButton(wrapper, '取消')
    expect(wrapper.text()).toContain('本批新表可用智能体：全部智能体')

    await clickButton(wrapper, '确认入库')
    expect(ds.saveSchema).toHaveBeenCalledTimes(1)
    const payload = ds.saveSchema.mock.calls[0][0]
    expect(payload.allowed_agent_ids).toBeUndefined()
  })

  it('重复命中已有表：批次结束一条结果提示沿用原设置，不逐张弹窗', async () => {
    ds.saveSchema.mockResolvedValue({
      success: true,
      existing: true,
      allowed_agent_ids: ['finance-assistant'],
    })
    const wrapper = await openReviewViaConnectorImport([schemaInfo('旧表')])

    await clickButton(wrapper, '确认入库')
    expect(alertMock).toHaveBeenCalledTimes(1)
    expect(alertMock.mock.calls[0][0]).toContain('已存在，沿用原可用智能体设置')
    expect(alertMock.mock.calls[0][0]).toContain('旧表')
  })

  it('首张保存失败不锁定，更改入口仍可用', async () => {
    ds.saveSchema
      .mockRejectedValueOnce(new Error('保存失败'))
      .mockResolvedValueOnce({ success: true, existing: false, allowed_agent_ids: [] })
    const wrapper = await openReviewViaConnectorImport([schemaInfo('表A')])

    await clickButton(wrapper, '确认入库')
    expect(alertMock).toHaveBeenCalledWith('保存失败')
    expect(findButtonByText(wrapper, '更改')).toBeDefined()
    expect(wrapper.text()).not.toContain('本批设置已锁定')
  })

  it('普通用户/角色未就绪：本批摘要只读无更改入口，默认共享入库不受影响', async () => {
    for (const role of ['user', null]) {
      auth.adminRef!.value = role === null ? null : { role }
      ds.saveSchema.mockResolvedValue({ success: true, existing: false, allowed_agent_ids: [] })
      const wrapper = await openReviewViaConnectorImport([schemaInfo('表A')])

      expect(wrapper.text()).toContain('本批新表可用智能体：全部智能体')
      expect(findButtonByText(wrapper, '更改')).toBeUndefined()

      await clickButton(wrapper, '确认入库')
      expect(ds.saveSchema).toHaveBeenCalledTimes(1)
      expect(ds.saveSchema.mock.calls[0][0].allowed_agent_ids).toBeUndefined()
      ds.saveSchema.mockClear()
    }
  })

  it('导入审核内打开范围弹框：焦点进入并约束 Tab，关闭恢复更改按钮且未提交入库', async () => {
    const wrapper = await openReviewViaConnectorImport([schemaInfo('表A')])
    const change = findButtonByText(wrapper, '更改')!
    ;(change.element as HTMLElement).focus()
    await change.trigger('click')
    await flushPromises()
    const dialog = wrapper.find('[role="dialog"]')
    expect(document.activeElement).toBe(dialog.element)
    const controls = Array.from(dialog.element.querySelectorAll<HTMLElement>('button, input')).filter(el => !el.matches(':disabled'))
    const first = controls[0], last = controls[controls.length - 1]
    last.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(first)
    first.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(last)
    expect(ds.saveSchema).not.toHaveBeenCalled()
    await clickButton(wrapper, '取消')
    expect(document.activeElement).toBe(change.element)
    expect(ds.saveSchema).not.toHaveBeenCalled()
    await clickButton(wrapper, '确认入库')
    expect(ds.saveSchema).toHaveBeenCalledTimes(1)
  })

})


describe('编辑数据源定位', () => {
  it('编辑已有表使用业务表名并携带原 source 和 connector_id，不重复添加标题前缀', async () => {
    const source = { type: 'database', connector_id: 'c1', db_table_name: 'sales' }
    ds.listSchemas.mockResolvedValue({ schemas: [schemaRow(1, { title: '[数据表] sales', metadata: { table_name: 'sales', source, connector_id: 'c1', columns: [] } })], total: 1 })
    const wrapper = await mountPage()
    await clickButton(wrapper, '编辑')
    await clickButton(wrapper, '确认入库')
    expect(ds.updateSchema).toHaveBeenCalledWith(1, expect.objectContaining({ table_name: 'sales', source, connector_id: 'c1' }))
    expect(ds.saveSchema).not.toHaveBeenCalled()
  })
})


describe('旧附件定位兼容', () => {
  it('旧字符串source不作为结构化source回传，交由后端保留', async () => {
    ds.listSchemas.mockResolvedValue({ schemas: [schemaRow(1, { title: '[数据表] sales', metadata: { table_name: 'sales', source: 'chat-attachment', source_info: '/tmp/sales.xlsx', columns: [] } })], total: 1 })
    const wrapper = await mountPage()
    await clickButton(wrapper, '编辑')
    await clickButton(wrapper, '确认入库')
    const payload = ds.updateSchema.mock.calls[0][1]
    expect(payload.table_name).toBe('sales')
    expect(payload.source_info).toBe('/tmp/sales.xlsx')
    expect(payload).not.toHaveProperty('source')
  })
})
