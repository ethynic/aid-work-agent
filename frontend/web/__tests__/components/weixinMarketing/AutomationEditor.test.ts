/**
 * AutomationEditor 关键路径测试（P3-A2，R55 + 验证返工 V-P1-1/V-P1-3/V-P2-1/V-P2-4）
 *
 * 1. 编辑既有草稿：详情回填（含 draft_blocks）+ 无草稿时明示不可回显（不静默覆盖）；
 * 2. 保存 409 版本冲突：保留用户输入并显示服务器差异（getAutomation 二次拉取）；
 * 3. 脏检测关闭守卫：保存失败后关闭弹框时出现「保存/不保存/取消」确认；
 * 4. V-P1-1 创建成功流：先写缓存再 keepForm 应用详情，表单内容块保留、无覆盖警告；
 * 5. V-P1-3 试发确认：dirty（草稿≠已发布）时隐藏逐条正文，仅显示位置占位；
 * 6. V-P2-1 paused 状态发布按钮禁用并提示先恢复；
 * 7. 复审 P1-1：发布后（无草稿）按 active_* 回显（内容/触发/群绑定），试发可用；
 *    有草稿时 draft 优先、行为不变。
 *
 * API 层走真实 weixinMarketing.ts（fetch mock），同时覆盖 envelope→WeixinApiError 解析。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useRoute: () => ({ path: '/t/acme/weixin-marketing/automations', params: {} }),
}))

vi.mock('vue-toastification', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

import AutomationEditor from '@/components/weixinMarketing/AutomationEditor.vue'

const AUTOMATION_ID = '11111111-1111-1111-1111-111111111111'
const CREATED_ID = '99999999-9999-9999-9999-999999999999'
const BINDING_ID = '22222222-2222-2222-2222-222222222222'
const REVISION_ID = '33333333-3333-3333-3333-333333333333'
const ACTIVE_REVISION_ID = '44444444-4444-4444-4444-444444444444'

function envelope(data: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => ({ success: status < 400, data, error: status >= 400 ? '版本冲突：期望 3，实际 4' : undefined, code: status >= 400 ? 'CONFLICT' : undefined, field_errors: [] }) } as unknown as Response
}

/** 详情 fixture（P3-A1 契约：draft_blocks = draft revision 有序块，无草稿 null；复审 P1-1 增 active_*） */
function detailFixture(version: number, name: string, overrides: {
  automation?: Record<string, unknown>
  revisions?: Array<Record<string, unknown>>
  draft_trigger?: Record<string, unknown> | null
  draft_blocks?: Array<{ position: number; kind: 'text' | 'link' | 'image'; text_content?: string | null; url?: string | null; asset_id?: string | null }> | null
  active_blocks?: Array<{ position: number; kind: 'text' | 'link' | 'image'; text_content?: string | null; url?: string | null; asset_id?: string | null }> | null
  active_trigger?: Record<string, unknown> | null
  active_group_binding_id?: string | null
} = {}) {
  return {
    automation: {
      id: AUTOMATION_ID,
      name,
      status: 'draft',
      version,
      draft_revision_id: REVISION_ID,
      active_revision_id: null,
      created_at: '2026-09-01T08:00:00Z',
      updated_at: '2026-09-01T08:00:00Z',
      ...(overrides.automation || {}),
    },
    revisions: overrides.revisions ?? [
      { id: REVISION_ID, revision_no: 1, status: 'draft', group_binding_id: BINDING_ID },
    ],
    draft_trigger: 'draft_trigger' in overrides
      ? overrides.draft_trigger
      : {
          type: 'once',
          run_at: '2026-09-10T01:30:00Z',
          timezone: 'Asia/Shanghai',
          grace_seconds: 300,
        },
    recent_runs: [],
    draft_blocks: 'draft_blocks' in overrides ? overrides.draft_blocks : null,
    active_blocks: 'active_blocks' in overrides ? overrides.active_blocks : null,
    active_trigger: 'active_trigger' in overrides ? overrides.active_trigger : null,
    active_group_binding_id: 'active_group_binding_id' in overrides
      ? overrides.active_group_binding_id
      : null,
  }
}

function mountEditor(automationId: string | null = AUTOMATION_ID) {
  return mount(AutomationEditor, {
    props: { modelValue: true, automationId },
    global: { stubs: { teleport: true } },
  })
}

async function clickButton(wrapper: ReturnType<typeof mountEditor>, text: string) {
  const btn = wrapper.findAll('button').find(b => b.text() === text)
  expect(btn, `button "${text}" should exist`).toBeTruthy()
  await btn!.trigger('click')
}

const BLOCK_INPUT_SELECTOR = 'input[placeholder="单条消息正文（不支持换行）"]'
const NAME_INPUT_SELECTOR = 'input[placeholder="如：每日早报群推送"]'

describe('AutomationEditor 编辑/409/脏检测', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('回填详情（draft_blocks）并正常展示内容块编辑器', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      envelope(detailFixture(3, '早报推送', { draft_blocks: [{ position: 0, kind: 'text', text_content: '已存草稿正文' }] })),
    )
    const wrapper = mountEditor()
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect((wrapper.find(NAME_INPUT_SELECTOR).element as HTMLInputElement).value).toBe('早报推送')
    expect(wrapper.text()).toContain('baseVersion 3')
    // draft_blocks 契约回显：正文可见且无「无草稿可回显」警告
    expect((wrapper.find(BLOCK_INPUT_SELECTOR).element as HTMLInputElement).value).toBe('已存草稿正文')
    expect(wrapper.text()).not.toContain('无草稿内容块可回显')
  })

  it('无 draft_blocks 且无缓存时明示不可回显（不静默覆盖）', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(envelope(detailFixture(3, '早报推送')))
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.text()).toContain('无草稿内容块可回显')
    expect(wrapper.text()).toContain('保存后将作为新草稿')
  })

  it('保存 409 冲突：保留用户输入并显示服务器差异', async () => {
    let putDraftCalled = false
    let detailFetchCount = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any) => {
      const url = String(input)
      if (url.includes(`/automations/${AUTOMATION_ID}/draft`)) {
        putDraftCalled = true
        return envelope(null, 409)
      }
      if (url.includes(`/automations/${AUTOMATION_ID}`)) {
        detailFetchCount++
        // 首次打开 v3；409 后冲突详情拉取 v4（他人在并发窗口保存过）
        return envelope(detailFixture(detailFetchCount > 1 ? 4 : 3, detailFetchCount > 1 ? '他人改过的名字' : '早报推送'))
      }
      return envelope({})
    })

    const wrapper = mountEditor()
    await flushPromises()

    // 解锁内容块重录并录入一条文字（blocksUnknown 场景下保存必须显式解锁）
    await clickButton(wrapper, '重新录入全部内容块')
    await clickButton(wrapper, '加文字')
    const blockInput = wrapper.find(BLOCK_INPUT_SELECTOR)
    expect(blockInput.exists()).toBe(true)
    await blockInput.setValue('今日早报：市场上涨')
    await blockInput.trigger('change')

    // 修改名称制造明确差异
    const nameInput = wrapper.find(NAME_INPUT_SELECTOR)
    await nameInput.setValue('早报推送-我的修改')

    await clickButton(wrapper, '保存草稿')
    await flushPromises()

    expect(putDraftCalled).toBe(true)
    // 用户输入保留：名称与内容块未被服务器数据覆盖
    expect((wrapper.find(NAME_INPUT_SELECTOR).element as HTMLInputElement).value).toBe('早报推送-我的修改')
    expect((wrapper.find(BLOCK_INPUT_SELECTOR).element as HTMLInputElement).value).toBe('今日早报：市场上涨')
    // 冲突面板显示服务器版本与差异
    expect(wrapper.text()).toContain('并发修改')
    expect(wrapper.text()).toContain('服务器当前版本 4')
    expect(wrapper.text()).toContain('他人改过的名字')
  })

  it('保存失败后关闭弹框触发脏检测确认（useModalCloseGuard）', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any) => {
      const url = String(input)
      if (url.includes(`/automations/${AUTOMATION_ID}/draft`)) {
        return envelope(null, 409)
      }
      return envelope(detailFixture(3, '早报推送'))
    })

    const wrapper = mountEditor()
    await flushPromises()

    // 制造脏状态（不改任何东西时快照一致，不算脏）
    const nameInput = wrapper.find(NAME_INPUT_SELECTOR)
    await nameInput.setValue('脏状态名称')

    await clickButton(wrapper, '关闭')
    // 出现三选确认框（保存/不保存/取消），弹框保持打开
    expect(wrapper.text()).toContain('未保存的修改')
    expect(wrapper.text()).toContain('保存并关闭')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    // 取消：停留在弹框
    await clickButton(wrapper, '取消')
    expect(wrapper.text()).not.toContain('保存并关闭')
  })

  it('V-P1-1 创建成功：内容块保留在表单、无「无草稿可回显」警告、缓存未被污染', async () => {
    let createCalled = false
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any, init?: any) => {
      const url = String(input)
      const method = init?.method || 'GET'
      if (url.endsWith('/automations') && method === 'POST') {
        createCalled = true
        return envelope(detailFixture(1, '新建任务', {
          automation: { id: CREATED_ID, name: '新建任务', status: 'draft', version: 1 },
          draft_blocks: null,
        }))
      }
      if (url.endsWith('/devices')) {
        return envelope({ items: [{ device_id: 'd1', name: 'Dev1', online: true, weixin: { available: true } }], total: 1 })
      }
      if (url.includes('/group-bindings')) {
        return envelope({
          items: [{ id: BINDING_ID, device_id: 'd1', label: '测试群', state: 'complete' }],
          total: 1, page: 1, page_size: 100,
        })
      }
      return envelope({})
    })

    const wrapper = mountEditor(null)
    await flushPromises()

    // 经绑定选择器选一个 complete 绑定（select 模式真实链路）
    await clickButton(wrapper, '选择绑定')
    await flushPromises()
    await clickButton(wrapper, '选用')
    await flushPromises()
    expect(wrapper.text()).toContain('测试群')

    // 录入两条文字块
    const nameInput = wrapper.find(NAME_INPUT_SELECTOR)
    await nameInput.setValue('新建任务')
    await clickButton(wrapper, '加文字')
    await clickButton(wrapper, '加文字')
    const blockInputs = wrapper.findAll(BLOCK_INPUT_SELECTOR)
    expect(blockInputs.length).toBe(2)
    // 每次 change 后组件重渲染，须重新查询（旧元素引用上的事件不会生效）
    for (const [index, value] of ['第一条内容', '第二条内容'].entries()) {
      await wrapper.findAll(BLOCK_INPUT_SELECTOR)[index].setValue(value)
      await wrapper.findAll(BLOCK_INPUT_SELECTOR)[index].trigger('change')
    }

    await clickButton(wrapper, '保存草稿')
    await flushPromises()

    expect(createCalled).toBe(true)
    // V-P1-1：创建成功后表单两块内容保留，不被（尚为空的）缓存回读清空
    const afterInputs = wrapper.findAll(BLOCK_INPUT_SELECTOR)
    expect(afterInputs.length).toBe(2)
    expect((afterInputs[0].element as HTMLInputElement).value).toBe('第一条内容')
    expect((afterInputs[1].element as HTMLInputElement).value).toBe('第二条内容')
    expect(wrapper.text()).not.toContain('无草稿内容块可回显')
  })

  it('V-P1-3 试发确认：dirty（草稿≠已发布）时不显示草稿正文，仅位置占位', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      envelope(detailFixture(5, '已发布任务', {
        automation: { status: 'active', version: 5, draft_revision_id: REVISION_ID, active_revision_id: ACTIVE_REVISION_ID },
        draft_blocks: [{ position: 0, kind: 'text', text_content: '本地草稿正文XYZ' }],
      })),
    )

    const wrapper = mountEditor()
    await flushPromises()

    // 制造 dirty：本地草稿与已发布可能不一致
    const nameInput = wrapper.find(NAME_INPUT_SELECTOR)
    await nameInput.setValue('已发布任务-改')

    await clickButton(wrapper, '试发...')
    // 标题与占位明示「按已发布内容发送」；不展示本地草稿正文
    expect(wrapper.text()).toContain('按已发布内容发送')
    expect(wrapper.text()).toContain('请先发布草稿')
    const preview = wrapper.find('[data-testid="testsend-preview"]')
    expect(preview.exists()).toBe(true)
    expect(preview.text()).toContain('内容以已发布版本为准')
    expect(preview.text()).not.toContain('本地草稿正文XYZ')
  })

  it('V-P2-1 paused 状态：发布按钮禁用并提示先恢复', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      envelope(detailFixture(5, '暂停任务', {
        automation: { status: 'paused', version: 5 },
      })),
    )
    const wrapper = mountEditor()
    await flushPromises()

    const publishBtn = wrapper.findAll('button').find(b => b.text() === '发布')
    expect(publishBtn, 'publish button should exist').toBeTruthy()
    expect(publishBtn!.attributes('disabled')).toBeDefined()
    expect(publishBtn!.attributes('title')).toContain('先恢复')
  })

  it('复审 P1-1 发布后（无草稿）：按 active_* 回显内容/触发/群绑定，试发可用', async () => {
    const activeTrigger = {
      type: 'interval',
      start_at: '2026-09-10T00:00:00Z',
      interval_seconds: 900,
      timezone: 'Asia/Shanghai',
      grace_seconds: 120,
      miss_policy: 'skip_overlap',
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any) => {
      const url = String(input)
      if (url.includes('/group-bindings')) {
        return envelope({
          items: [
            { id: BINDING_ID, device_id: 'd1', label: '已发布测试群', state: 'complete' },
          ],
          total: 1, page: 1, page_size: 100,
        })
      }
      return envelope(detailFixture(4, '已发布未迭代任务', {
        automation: {
          status: 'active', version: 4,
          draft_revision_id: null, active_revision_id: ACTIVE_REVISION_ID,
        },
        revisions: [
          { id: ACTIVE_REVISION_ID, revision_no: 1, status: 'published', group_binding_id: BINDING_ID },
        ],
        draft_trigger: null,
        draft_blocks: [],
        active_blocks: [{ position: 1, kind: 'text', text_content: '已发布正文' }],
        active_trigger: activeTrigger,
        active_group_binding_id: BINDING_ID,
      }))
    })

    const wrapper = mountEditor()
    await flushPromises()

    // 内容块回显自已发布版本（非空、非「无草稿可回显」重录警告）
    expect((wrapper.find(BLOCK_INPUT_SELECTOR).element as HTMLInputElement).value).toBe('已发布正文')
    expect(wrapper.text()).not.toContain('无草稿内容块可回显')
    // 标注「已按已发布版本回显，保存将创建新草稿」
    expect(wrapper.text()).toContain('已按已发布版本回显')
    expect(wrapper.text()).toContain('保存将创建新草稿')
    // 触发配置回显 active_trigger（interval 900/宽限 120），不回退默认 once（300）
    const numberValues = wrapper.findAll('input[type="number"]').map(
      i => (i.element as HTMLInputElement).value,
    )
    expect(numberValues).toContain('900')
    expect(numberValues).toContain('120')
    // 群绑定回显原群（label + 已核验状态）
    expect(wrapper.text()).toContain('已发布测试群')
    expect(wrapper.text()).toContain('已核验')
    // 回显基线不算脏（绑定信息补全在快照前完成）
    expect(wrapper.text()).not.toContain('有未保存修改')

    // 试发按钮可用（内容来自 active 块）→ 打开确认框并展示已发布正文
    const testBtn = wrapper.findAll('button').find(b => b.text() === '试发...')
    expect(testBtn, 'test-send button should exist').toBeTruthy()
    expect(testBtn!.attributes('disabled')).toBeUndefined()
    await testBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('确认试发')
    const preview = wrapper.find('[data-testid="testsend-preview"]')
    expect(preview.exists()).toBe(true)
    expect(preview.text()).toContain('已发布正文')
    expect(fetchMock).toHaveBeenCalled()
  })

  it('复审 P1（2026-09-10）：多块试发提交 block_position 与所选条目一致（1 基，防错发）', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: any) => {
      const url = String(input)
      if (url.includes('/group-bindings')) {
        return envelope({
          items: [{ id: BINDING_ID, device_id: 'd1', label: '多块测试群', state: 'complete' }],
          total: 1, page: 1, page_size: 100,
        })
      }
      if (url.includes('/test-send')) {
        return envelope({ run_id: 'run-ts-2', occurrence_id: 'occ-ts-2', block_position: 2 })
      }
      return envelope(detailFixture(4, '多块已发布任务', {
        automation: {
          status: 'active', version: 4,
          draft_revision_id: null, active_revision_id: ACTIVE_REVISION_ID,
        },
        revisions: [
          { id: ACTIVE_REVISION_ID, revision_no: 1, status: 'published', group_binding_id: BINDING_ID },
        ],
        draft_trigger: null,
        draft_blocks: [],
        active_blocks: [
          { position: 1, kind: 'text', text_content: '第一条正文' },
          { position: 2, kind: 'text', text_content: '第二条正文' },
        ],
        active_trigger: { type: 'once', run_at: '2026-09-20T00:00:00Z', timezone: 'UTC' },
        active_group_binding_id: BINDING_ID,
      }))
    })

    const wrapper = mountEditor()
    await flushPromises()

    await clickButton(wrapper, '试发...')
    await flushPromises()
    expect(wrapper.text()).toContain('确认试发')

    // 选择第 2 条（条目下拉为 1 基编号；页面含群绑定等多个 select，按选项特征定位）
    const entrySelect = wrapper.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === '2')
        && s.findAll('option').some(o => o.text().includes('第二条正文')),
    )
    expect(entrySelect, 'entry select should exist').toBeTruthy()
    await entrySelect!.setValue('2')
    await flushPromises()
    // 预览展示第 2 条正文
    const preview = wrapper.find('[data-testid="testsend-preview"]')
    expect(preview.text()).toContain('第二条正文')
    expect(preview.text()).not.toContain('第一条正文')

    await clickButton(wrapper, '确认试发')
    await flushPromises()

    // 提交的 block_position 必须等于所选条目号（1 基，与后端内容块编号一致）
    const call = fetchMock.mock.calls.find(([input]: any[]) => String(input).includes('/test-send'))
    expect(call, 'test-send request should have been issued').toBeTruthy()
    const body = JSON.parse((call![1] as any).body)
    expect(body.block_position).toBe(2)
    expect(body.group_binding_id).toBe(BINDING_ID)
  })

  it('复审 P1-1 有草稿时 draft 优先：active_* 并存也不覆盖草稿回显', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      envelope(detailFixture(6, '已发布有草稿任务', {
        automation: {
          status: 'active', version: 6,
          draft_revision_id: REVISION_ID, active_revision_id: ACTIVE_REVISION_ID,
        },
        revisions: [
          { id: REVISION_ID, revision_no: 2, status: 'draft', group_binding_id: BINDING_ID },
          { id: ACTIVE_REVISION_ID, revision_no: 1, status: 'published', group_binding_id: BINDING_ID },
        ],
        draft_blocks: [{ position: 1, kind: 'text', text_content: '迭代中的草稿正文' }],
        active_blocks: [{ position: 1, kind: 'text', text_content: '线上已发布正文' }],
        active_trigger: {
          type: 'interval',
          start_at: '2026-09-10T00:00:00Z',
          interval_seconds: 900,
          timezone: 'Asia/Shanghai',
          grace_seconds: 120,
          miss_policy: 'skip_overlap',
        },
        active_group_binding_id: BINDING_ID,
      })),
    )
    const wrapper = mountEditor()
    await flushPromises()

    // 草稿内容优先回显，不展示「已按已发布版本回显」标注
    expect((wrapper.find(BLOCK_INPUT_SELECTOR).element as HTMLInputElement).value).toBe('迭代中的草稿正文')
    expect(wrapper.text()).not.toContain('已按已发布版本回显')
    expect(wrapper.text()).not.toContain('无草稿内容块可回显')
    // 触发用 draft_trigger（默认 once），不取 active interval（无 900 输入）
    const numberValues = wrapper.findAll('input[type="number"]').map(
      i => (i.element as HTMLInputElement).value,
    )
    expect(numberValues).not.toContain('900')
    // 无 active 回显即不发起绑定列表补全（仅详情一次请求）
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
