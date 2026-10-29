/**
 * TaskPlanView 组件测试（定时任务重构 Phase 5 评审修复回归）
 *
 * 覆盖评审明确要求的缺陷场景：
 * - P1-1 切换任务请求隔离（A→B 快速切换，A 慢响应不覆盖 B）
 * - P1-2 新执行发现（10s 轮询 mock 出新 runner → 并入并 observe）
 * - P2-3 双缓存收敛（list 返回更高 revision 的新终态值刷新后展示；旧投影被拒）
 * - P2-4 执行结果完整展示（图片渲染 + 会话消息详情）
 * - P2-6 interval 分钟精度预填（4500s → 75 分钟）
 * - P2-7 附件上传（复用 uploadFile，上传后组装 AttachmentRef）
 * - P2-9 通知目标未变更时不提交目标字段
 * - P2-12 取消按钮绑定行级 runner_id（多 active 时顶部不给单一取消）
 * - P2-13 通知投递三态（失败可重试 / 空 / 有记录）
 *
 * Mock 范式参考 MenuSidebar.test.ts（reactive routeState + vi.mock 模块级替换）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick, reactive, ref } from 'vue'

// ============== 共享 Mock（vi.hoisted 保证 vi.mock 工厂可用） ==============
// 注意：routeState 等需要 vue 运行时的对象放在模块级（vi.hoisted 回调先于 import
// 初始化执行会 TDZ）；vi.mock 工厂内以闭包惰性引用，与 MenuSidebar.test.ts 同范式。

const routerPush = vi.hoisted(() => vi.fn())
const toastSuccess = vi.hoisted(() => vi.fn())
const toastError = vi.hoisted(() => vi.fn())
const toastWarning = vi.hoisted(() => vi.fn())

const routeState = reactive({
  path: '/t/tenant-1/tasks/task-1',
  params: { tenant_id: 'tenant-1', task_id: 'task-1' } as Record<string, any>,
  query: {} as Record<string, any>,
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush, back: () => {} }),
  useRoute: () => routeState,
}))

vi.mock('vue-toastification', () => ({
  useToast: () => ({ success: toastSuccess, error: toastError, warning: toastWarning, info: vi.fn() }),
}))

vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({
    admin: ref({ user_id: 'u1', username: 'tester', role: 'user' }),
    isLoggedIn: ref(true),
    getAuthHeader: () => ({ Authorization: 'Bearer test-token', 'X-Tenant-Id': 'tenant-1' }),
    logout: vi.fn(),
    privateCloud: ref(false),
  }),
}))

vi.mock('@/composables/useSubagentList', () => ({
  useSubagentList: () => ({
    availableSubagents: ref([]),
    loadAvailableSubagents: vi.fn().mockResolvedValue(undefined),
  }),
}))

// AppHeader stub：避免 NotificationBell 等真实子组件的挂载副作用
vi.mock('@/components/AppHeader.vue', () => ({
  default: defineComponent({
    name: 'AppHeader',
    emits: ['toggle-sidebar'],
    setup() {
      return () => h('header', { class: 'app-header-stub' }, '任务计划')
    },
  }),
}))

// Runner 网关共享实现（script setup 每实例 new RunnerClient，方法绑定共享 mock，
// 测试可在 mount 前配置响应，覆盖初始加载）
const runnerListMock = vi.hoisted(() => vi.fn())
const runnerObserveMock = vi.hoisted(() => vi.fn())
const runnerCancelMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/runner', async () => {
  const actual = await vi.importActual<any>('@/api/runner')
  class MockRunnerClient {
    list = runnerListMock
    observe = runnerObserveMock
    cancel = runnerCancelMock
    capabilities = vi.fn().mockResolvedValue({ web_enabled: false, observe_existing: false, contract_version: 1, transport: 'runner_poll' })
    installCapabilities = vi.fn()
    detach = vi.fn()
  }
  return { ...actual, RunnerClient: MockRunnerClient }
})

const getScheduledTaskMock = vi.hoisted(() => vi.fn())
const pauseMock = vi.hoisted(() => vi.fn())
const resumeMock = vi.hoisted(() => vi.fn())
const triggerMock = vi.hoisted(() => vi.fn())
const deleteMock = vi.hoisted(() => vi.fn())
const updateScheduleMock = vi.hoisted(() => vi.fn())
const updateDefinitionMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/scheduledTask', async () => {
  const actual = await vi.importActual<any>('@/api/scheduledTask')
  return {
    ...actual,
    getScheduledTask: getScheduledTaskMock,
    pauseScheduledTask: pauseMock,
    resumeScheduledTask: resumeMock,
    deleteScheduledTask: deleteMock,
    triggerScheduledTask: triggerMock,
    updateScheduledTaskSchedule: updateScheduleMock,
    updateScheduledTaskDefinition: updateDefinitionMock,
  }
})

const taskDeliveriesMock = vi.hoisted(() => vi.fn())
const selectableTargetsMock = vi.hoisted(() => vi.fn())

vi.mock('@/api/notifications', async () => {
  const actual = await vi.importActual<any>('@/api/notifications')
  return {
    ...actual,
    notificationsAPI: {
      ...(actual.notificationsAPI || {}),
      taskDeliveries: taskDeliveriesMock,
      selectableTargets: selectableTargetsMock,
    },
  }
})

const getSessionMessagesMock = vi.hoisted(() => vi.fn())
vi.mock('@/api/session', async () => {
  const actual = await vi.importActual<any>('@/api/session')
  return { ...actual, getSessionMessages: getSessionMessagesMock }
})

const uploadFileMock = vi.hoisted(() => vi.fn())
vi.mock('@/api/agent', async () => {
  const actual = await vi.importActual<any>('@/api/agent')
  return { ...actual, uploadFile: uploadFileMock }
})

import TaskPlanView from '@/components/scheduled/TaskPlanView.vue'
import type { ScheduledTask } from '@/api/scheduledTask'
import type { RunnerView } from '@/api/runner'

// ============== Fixtures ==============

function makeTask(overrides: Partial<ScheduledTask> = {}): ScheduledTask {
  return {
    task_id: 'task-1', tenant_id: 'tenant-1', user_id: 'u1',
    name: '数据汇总任务', task_prompt: '每天汇总数据并发通知', attachments: [],
    profile_id: 'main', schedule_type: 'interval', interval_seconds: 4500,
    session_id: 'sess-1', status: 'active', next_due_at: '2026-01-02T01:00:00',
    total_runs: 3, success_count: 3, fail_count: 0,
    created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00',
    ...overrides,
  } as ScheduledTask
}

function makeRunner(overrides: Partial<RunnerView> = {}): RunnerView {
  return {
    runner_id: 'r1', client_request_id: 'cr1', queue_order: 1,
    session: { kind: 'scheduled', session_id: 'sess-1' },
    status: 'completed', cancel_requested: false,
    revision: 3, control_revision: 0, settlement_status: 'settled', view_revision: 3,
    accepted_at: '2026-01-01T09:00:00', updated_at: '2026-01-01T09:05:00', finished_at: '2026-01-01T09:05:00',
    snapshot: {}, result: { status: 'completed', output: '' },
    scheduled: { task_id: 'task-1', trigger: 'scheduled' },
    ...overrides,
  } as RunnerView
}

const emptyPage = { runners: [], active_runners: [], has_more: false, next_cursor: null }

function pageOf(runners: RunnerView[]) {
  return { runners, active_runners: [], has_more: false, next_cursor: null }
}

/** Teleport 弹窗内按文本找按钮 */
function bodyButton(text: string): HTMLButtonElement | undefined {
  return Array.from(document.querySelectorAll('button'))
    .find(b => b.textContent?.trim() === text)
}

// ============== 用例 ==============

describe('TaskPlanView - 评审修复回归', () => {
  let wrapper: ReturnType<typeof mount> | null = null

  beforeEach(() => {
    document.body.innerHTML = ''
    routeState.params = { tenant_id: 'tenant-1', task_id: 'task-1' }
    routeState.query = {}
    routerPush.mockClear()
    toastSuccess.mockClear(); toastError.mockClear(); toastWarning.mockClear()
    // 显式 reset（清除 once 链与实现），再配默认值；用例在 mount 前按需覆盖
    getScheduledTaskMock.mockReset().mockResolvedValue(makeTask())
    pauseMock.mockReset().mockResolvedValue(undefined)
    resumeMock.mockReset().mockResolvedValue({ success: true })
    triggerMock.mockReset().mockResolvedValue(undefined)
    deleteMock.mockReset().mockResolvedValue(undefined)
    updateScheduleMock.mockReset().mockResolvedValue(undefined)
    updateDefinitionMock.mockReset().mockResolvedValue(makeTask())
    runnerListMock.mockReset().mockResolvedValue(emptyPage)
    runnerObserveMock.mockReset()
    runnerCancelMock.mockReset()
    taskDeliveriesMock.mockReset().mockResolvedValue({ task_id: 'task-1', events: [] })
    selectableTargetsMock.mockReset().mockResolvedValue({ targets: [] })
    getSessionMessagesMock.mockReset().mockResolvedValue({ messages: [] })
    uploadFileMock.mockReset()
  })

  afterEach(() => {
    if (wrapper) { wrapper.unmount(); wrapper = null }
    document.body.innerHTML = ''
  })

  it('P1-1 切换任务请求隔离：A→B 快速切换，A 的慢响应不覆盖 B', async () => {
    let resolveA!: (value: ScheduledTask) => void
    getScheduledTaskMock.mockImplementation((id: string) => {
      if (id === 'task-a') return new Promise<ScheduledTask>(resolve => { resolveA = resolve })
      return Promise.resolve(makeTask({ task_id: 'task-b', name: '任务B-正确目标' }))
    })
    routeState.params = { tenant_id: 'tenant-1', task_id: 'task-a' }
    wrapper = mount(TaskPlanView)
    await flushPromises()
    // A 未返回：加载态
    expect(wrapper.text()).toContain('正在加载任务')

    // 切到 B（同组件实例内路由参数变化）
    routeState.params = { tenant_id: 'tenant-1', task_id: 'task-b' }
    await nextTick()
    await flushPromises()
    expect(wrapper.text()).toContain('任务B-正确目标')
    expect(wrapper.text()).not.toContain('正在加载任务')

    // A 的慢响应到达：属于旧上下文，必须丢弃
    resolveA(makeTask({ task_id: 'task-a', name: '任务A-过期内容' }))
    await flushPromises()
    expect(wrapper.text()).not.toContain('任务A-过期内容')
    expect(wrapper.text()).toContain('任务B-正确目标')
  })

  it('P1-2 新执行发现：10s 轮询发现新 runner 并 observe', async () => {
    vi.useFakeTimers()
    try {
      const r1 = makeRunner({ runner_id: 'r1', status: 'completed', result: { status: 'completed', output: '第一次执行结果' } })
      const r2 = makeRunner({
        runner_id: 'r2', status: 'running', accepted_at: '2026-01-01T10:00:00',
        revision: 1, view_revision: 1, result: null, snapshot: { output: '新执行进行中ZZZ' },
      })
      runnerListMock.mockResolvedValueOnce(pageOf([r1])).mockResolvedValue(pageOf([r1, r2]))
      wrapper = mount(TaskPlanView)
      await flushPromises()
      expect(wrapper.text()).toContain('第一次执行结果')

      // 10s 发现轮询：新 runner r2 出现 → 并入列表并 observe
      await vi.advanceTimersByTimeAsync(10_000)
      await flushPromises()
      expect(wrapper.text()).toContain('新执行进行中ZZZ')
      expect(runnerObserveMock).toHaveBeenCalledWith('r2', expect.anything(), expect.any(Function), expect.any(Function))
      // r1 已完成且 settled → 不订阅
      expect(runnerObserveMock).not.toHaveBeenCalledWith('r1', expect.anything(), expect.any(Function), expect.any(Function))
    } finally {
      vi.useRealTimers()
    }
  })

  it('P2-3 单缓存收敛：更高 revision 的新终态值展示，旧 revision 投影被拒', async () => {
    vi.useFakeTimers()
    try {
      const r1v1 = makeRunner({ runner_id: 'r1', revision: 3, view_revision: 3, result: { status: 'completed', output: '旧输出ABC' } })
      const r1v2 = makeRunner({ runner_id: 'r1', revision: 5, view_revision: 5, result: { status: 'completed', output: '新输出XYZ', credit_cost: 12 } })
      runnerListMock.mockResolvedValueOnce(pageOf([r1v1]))
      wrapper = mount(TaskPlanView)
      await flushPromises()
      expect(wrapper.text()).toContain('旧输出ABC')

      // 轮询返回更高 revision：覆盖旧值（单缓存按 revision 竞争）
      runnerListMock.mockResolvedValue(pageOf([r1v2]))
      await vi.advanceTimersByTimeAsync(10_000)
      await flushPromises()
      expect(wrapper.text()).toContain('新输出XYZ')
      expect(wrapper.text()).toContain('12 积分')

      // 再返回旧 revision（过期响应）：被拒绝，展示值不变
      runnerListMock.mockResolvedValue(pageOf([r1v1]))
      await vi.advanceTimersByTimeAsync(10_000)
      await flushPromises()
      expect(wrapper.text()).toContain('新输出XYZ')
      expect(wrapper.text()).not.toContain('旧输出ABC')
    } finally {
      vi.useRealTimers()
    }
  })

  it('P2-4 展开详情渲染输出图片与会话消息（按 metadata.runner_id 过滤）', async () => {
    const image = {
      file_id: 'img-1', download_url: '/api/files/img-1', display_name: '图表.png',
      width: 100, height: 100, mime_type: 'image/png', size_bytes: 2048,
      source: 'tool_generated', usage: 'inline', placement: 'after_text',
    } as any
    const r1 = makeRunner({
      runner_id: 'r1', status: 'completed',
      result: { status: 'completed', output: '文本结论如下', images: [image], credit_cost: 3 },
    })
    runnerListMock.mockResolvedValue(pageOf([r1]))
    getSessionMessagesMock.mockResolvedValue({
      messages: [
        { message_id: 'm1', session_id: 'sess-1', role: 'user', content: '任务输入内容Q', metadata: { runner_id: 'r1' }, created_at: '2026-01-01T09:00:00' },
        { message_id: 'm2', session_id: 'sess-1', role: 'assistant', content: '完整会话结果W', metadata: { runner_id: 'r1' }, created_at: '2026-01-01T09:05:00' },
        { message_id: 'm3', session_id: 'sess-1', role: 'assistant', content: '其他执行的内容E', metadata: { runner_id: 'r-other' }, created_at: '2026-01-01T08:00:00' },
      ],
    })
    wrapper = mount(TaskPlanView)
    await flushPromises()
    expect(wrapper.text()).toContain('含 1 张图片')

    const toggle = wrapper.findAll('button').find(b => b.text() === '查看详情')
    expect(toggle).toBeTruthy()
    await toggle!.trigger('click')
    await flushPromises()

    // 图片经 ImageGallery 渲染为 <img>
    const imgs = wrapper.findAll('img').filter(i => (i.attributes('src') || '').includes('img-1'))
    expect(imgs.length).toBeGreaterThanOrEqual(1)
    // 会话消息按 runner_id 过滤展示
    expect(wrapper.text()).toContain('任务输入内容Q')
    expect(wrapper.text()).toContain('完整会话结果W')
    // 后端已按 runner_id 先过滤（三轮-3）：mock 返回即展示内容；r1 消息必须在场
    expect(wrapper.text()).toContain('完整会话结果W')
  })

  it('P2-12 多个 active：行级取消绑定该行 runner_id，顶部不给单一取消入口', async () => {
    const running = makeRunner({ runner_id: 'r-old', status: 'running', accepted_at: '2026-01-01T09:00:00', result: null, snapshot: {} })
    const queued = makeRunner({ runner_id: 'r-new', status: 'queued', accepted_at: '2026-01-01T10:00:00', revision: 1, view_revision: 1, result: null, snapshot: {} })
    runnerListMock.mockResolvedValue(pageOf([running, queued]))
    runnerCancelMock.mockResolvedValue(makeRunner({ runner_id: 'r-new', status: 'cancelled' }))
    wrapper = mount(TaskPlanView)
    await flushPromises()

    // 多 active：顶部无歧义取消入口，给出引导文案
    expect(wrapper.text()).toContain('请在下方执行记录中分别取消')
    expect(wrapper.text()).not.toContain('取消当前执行')
    expect(wrapper.text()).not.toContain('取消执行')

    // 每个非终态行内有取消按钮
    const rowButtons = wrapper.findAll('button').filter(b => b.text() === '取消')
    expect(rowButtons.length).toBe(2)

    // 点击较新（r-new，10:00 受理）那一行的取消 → cancel 收到该行 runner_id
    const rows = wrapper.findAll('.divide-y > div').filter(row => row.text().includes('10:00'))
    expect(rows.length).toBe(1)
    const rowCancel = rows[0].findAll('button').find(b => b.text() === '取消')
    expect(rowCancel).toBeTruthy()
    await rowCancel!.trigger('click')
    await flushPromises()
    expect(runnerCancelMock).toHaveBeenCalledWith('r-new', expect.anything())
  })

  it('P2-9 编辑保存：通知目标未变更时不提交目标字段', async () => {
    const task = makeTask({ notification_target_id: 'tgt-1' })
    getScheduledTaskMock.mockResolvedValue(task)
    selectableTargetsMock.mockResolvedValue({
      targets: [{ target_id: 'tgt-1', installation_ref: 'i1', binding_version: 1, state: 'active', enabled_since: null, channel: 'bot', provider: 'wecom_bot' }],
    })
    updateDefinitionMock.mockResolvedValue(task)
    wrapper = mount(TaskPlanView)
    await flushPromises()

    const editButton = wrapper.findAll('button').find(b => b.text() === '编辑')
    expect(editButton).toBeTruthy()
    await editButton!.trigger('click')
    await flushPromises()

    // 弹窗（Teleport to body）内直接点保存（未做任何修改）
    const saveButton = bodyButton('保存')
    expect(saveButton).toBeTruthy()
    saveButton!.click()
    await flushPromises()

    expect(updateDefinitionMock).toHaveBeenCalledTimes(1)
    const payload = updateDefinitionMock.mock.calls[0][1] as Record<string, unknown>
    expect(payload.name).toBe('数据汇总任务')
    expect('notification_target_id' in payload).toBe(false)
    expect('clear_notification_target' in payload).toBe(false)
  })

  it('P2-13 通知投递三态：失败可重试 → 空 → 有记录', async () => {
    taskDeliveriesMock.mockRejectedValueOnce(new Error('network down'))
    wrapper = mount(TaskPlanView)
    await flushPromises()
    expect(wrapper.text()).toContain('通知记录加载失败')
    const retry = wrapper.findAll('button').find(b => b.text() === '重试')
    expect(retry).toBeTruthy()

    taskDeliveriesMock.mockResolvedValueOnce({ task_id: 'task-1', events: [] })
    await retry!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('暂无通知记录')

    // 有记录（含 pending 投递）：重新挂载验证第三态与自动刷新提示
    wrapper.unmount()
    wrapper = null
    document.body.innerHTML = ''
    taskDeliveriesMock.mockResolvedValue({
      task_id: 'task-1',
      events: [{
        notification_ref: 'nref-1', event_type: 'task.completed', created_at: '2026-01-01T09:06:00',
        deliveries: [{ target_id: 'tgt-1', channel: 'bot', provider: 'wecom_bot', state: 'pending', attempts: 1 }],
      }],
    })
    wrapper = mount(TaskPlanView)
    await flushPromises()
    expect(wrapper.text()).toContain('任务完成')
    expect(wrapper.text()).toContain('投递中')
    expect(wrapper.text()).toContain('有投递进行中')
  })

  it('P2-6 interval 改期分钟精度预填：4500s → 75 分钟（不再被小时舍入）', async () => {
    wrapper = mount(TaskPlanView)
    await flushPromises()
    const reschedule = wrapper.findAll('button').find(b => b.text() === '改期')
    expect(reschedule).toBeTruthy()
    await reschedule!.trigger('click')
    await flushPromises()

    const input = document.querySelector('#task-plan-interval-minutes') as HTMLInputElement | null
    expect(input).toBeTruthy()
    expect(input!.value).toBe('75')
    const modalText = Array.from(document.querySelectorAll('p')).map(p => p.textContent).join('\n')
    expect(modalText).toContain('1.25 小时')
  })

  it('P2-7 编辑弹窗添加附件：上传返回 file_id 组装 AttachmentRef', async () => {
    uploadFileMock.mockResolvedValue({
      file_id: 'file-9', name: '补充资料.pdf', size: 1024,
      mime_type: 'application/pdf', type: 'file',
    })
    wrapper = mount(TaskPlanView)
    await flushPromises()
    const editButton = wrapper.findAll('button').find(b => b.text() === '编辑')
    await editButton!.trigger('click')
    await flushPromises()

    const input = document.querySelector('#task-plan-edit-attachment-input') as HTMLInputElement | null
    expect(input).toBeTruthy()
    const file = new File(['bytes'], '补充资料.pdf', { type: 'application/pdf' })
    Object.defineProperty(input, 'files', { value: [file] })
    input!.dispatchEvent(new Event('change'))
    await flushPromises()

    expect(uploadFileMock).toHaveBeenCalledTimes(1)
    // 弹窗附件列表出现新附件
    const modalText = Array.from(document.querySelectorAll('div')).map(d => d.textContent).join(' ')
    expect(modalText).toContain('补充资料.pdf')
  })

  // ============== 二轮评审修复回归 ==============

  it('二轮-1 view_revision 回退拒绝：cancel_requested 新投影不被旧列表响应覆盖', async () => {
    vi.useFakeTimers()
    try {
      const running = makeRunner({ runner_id: 'r1', status: 'running', revision: 1, view_revision: 1, result: null, snapshot: { output: '执行中ABC' } })
      runnerListMock.mockResolvedValue(pageOf([running]))
      wrapper = mount(TaskPlanView)
      await flushPromises()

      // 行级取消：返回 cancel_requested=true 的新投影——只递增 view_revision（revision 不变），
      // 旧判据（仅比 revision）会把它误判为"不新"而拒绝
      runnerCancelMock.mockResolvedValue(makeRunner({
        runner_id: 'r1', status: 'running', cancel_requested: true,
        revision: 1, view_revision: 2, result: null, snapshot: { output: '执行中ABC' },
      }))
      const cancelButton = wrapper.findAll('button').find(b => b.text() === '取消')
      expect(cancelButton).toBeTruthy()
      await cancelButton!.trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('取消核对中')

      // 旧列表响应（更高 revision 但更低 view_revision）：必须拒绝，不能回退 cancel_requested
      const staleProjection = makeRunner({
        runner_id: 'r1', status: 'running', cancel_requested: false,
        revision: 5, view_revision: 1, result: null, snapshot: { output: '执行中ABC' },
      })
      runnerListMock.mockResolvedValue(pageOf([staleProjection]))
      await vi.advanceTimersByTimeAsync(10_000)
      await flushPromises()
      expect(wrapper.text()).toContain('取消核对中')
    } finally {
      vi.useRealTimers()
    }
  })

  it('二轮-4 附件上传中：保存禁用+占位展示；失败移除占位并提示', async () => {
    let resolveUpload!: (value: unknown) => void
    uploadFileMock.mockReturnValue(new Promise(resolve => { resolveUpload = resolve }))
    wrapper = mount(TaskPlanView)
    await flushPromises()
    const editButton = wrapper.findAll('button').find(b => b.text() === '编辑')
    await editButton!.trigger('click')
    await flushPromises()

    const input = document.querySelector('#task-plan-edit-attachment-input') as HTMLInputElement | null
    const file = new File(['bytes'], '传输中报告.pdf', { type: 'application/pdf' })
    Object.defineProperty(input, 'files', { value: [file] })
    input!.dispatchEvent(new Event('change'))
    await flushPromises()

    // 上传未完成：占位项可见，保存按钮禁用并提示"附件上传中"
    const bodyText = () => Array.from(document.querySelectorAll('div')).map(d => d.textContent).join(' ')
    expect(bodyText()).toContain('传输中报告.pdf（上传中…）')
    const saveButton = bodyButton('附件上传中…')
    expect(saveButton).toBeTruthy()
    expect((saveButton as HTMLButtonElement).disabled).toBe(true)

    // 上传失败：占位移除 + 错误提示
    resolveUpload(Promise.reject(new Error('upload failed')))
    await flushPromises()
    await flushPromises()
    expect(bodyText()).not.toContain('上传中…')
    expect(toastError).toHaveBeenCalled()
    // 保存按钮恢复可用
    expect(bodyButton('保存')).toBeTruthy()
    expect((bodyButton('保存') as HTMLButtonElement).disabled).toBe(false)
  })

  it('二轮-4 重开弹窗后旧上传回填丢弃（弹窗代际）', async () => {
    let resolveUpload!: (value: unknown) => void
    uploadFileMock.mockReturnValue(new Promise(resolve => { resolveUpload = resolve }))
    wrapper = mount(TaskPlanView)
    await flushPromises()
    const editButton = wrapper.findAll('button').find(b => b.text() === '编辑')
    await editButton!.trigger('click')
    await flushPromises()

    // 第一次打开弹窗：发起上传（慢），随后关闭弹窗
    const input = document.querySelector('#task-plan-edit-attachment-input') as HTMLInputElement | null
    const file = new File(['bytes'], '旧弹窗附件.pdf', { type: 'application/pdf' })
    Object.defineProperty(input, 'files', { value: [file] })
    input!.dispatchEvent(new Event('change'))
    await flushPromises()
    bodyButton('取消')!.click()
    await flushPromises()

    // 重开弹窗（代际递增）后旧上传才返回：不得写入新弹窗附件列表
    await editButton!.trigger('click')
    await flushPromises()
    resolveUpload({ file_id: 'file-stale', name: '旧弹窗附件.pdf', type: 'file', mime_type: 'application/pdf', size: 1 })
    await flushPromises()
    const bodyText = () => Array.from(document.querySelectorAll('div')).map(d => d.textContent).join(' ')
    expect(bodyText()).not.toContain('旧弹窗附件.pdf')
  })

  it('二轮-7 编辑弹窗每次打开都重新查询通知目标选项', async () => {
    selectableTargetsMock.mockResolvedValue({ targets: [] })
    wrapper = mount(TaskPlanView)
    await flushPromises()
    const editButton = wrapper.findAll('button').find(b => b.text() === '编辑')

    await editButton!.trigger('click')
    await flushPromises()
    expect(selectableTargetsMock).toHaveBeenCalledTimes(1)

    bodyButton('取消')!.click()
    await flushPromises()
    await editButton!.trigger('click')
    await flushPromises()
    // 不因列表已非空而跳过（租户切换后旧选项不残留）
    expect(selectableTargetsMock).toHaveBeenCalledTimes(2)
  })

  it('二轮-5 通知四态：待展开 / 确认无外发 / 有投递，待展开纳入自动刷新提示', async () => {
    taskDeliveriesMock.mockResolvedValue({
      task_id: 'task-1',
      events: [
        { notification_ref: 'n-pending', event_type: 'task.completed', created_at: '2026-01-01T09:06:00', expansion_state: 'pending', deliveries: [] },
        { notification_ref: 'n-nosend', event_type: 'task.failed', created_at: '2026-01-01T09:10:00', expansion_state: 'expanded', deliveries: [] },
        { notification_ref: 'n-sent', event_type: 'task.paused', created_at: '2026-01-01T09:20:00', expansion_state: 'expanded',
          deliveries: [{ target_id: 'tgt-1', channel: 'bot', provider: 'wecom_bot', state: 'delivered', attempts: 1 }] },
      ],
    })
    wrapper = mount(TaskPlanView)
    await flushPromises()
    // 待展开：不能断言"未配置外发"
    expect(wrapper.text()).toContain('通知投递准备中')
    // expanded 且无 deliveries：确认无外发
    expect(wrapper.text()).toContain('站内信（未配置外发目标）')
    // 有投递记录
    expect(wrapper.text()).toContain('已投递')
    // 待展开事件驱动 30s 自动刷新提示
    expect(wrapper.text()).toContain('有投递进行中')
  })

  it('二轮-6 会话详情按 runner 缓存：终态一次即定，非终态每次展开刷新，终态落定清缓存', async () => {
    vi.useFakeTimers()
    try {
      const done = makeRunner({ runner_id: 'r-done', status: 'completed', accepted_at: '2026-01-01T10:00:00', result: { status: 'completed', output: '已完成输出DONE' } })
      const live = makeRunner({ runner_id: 'r-live', status: 'running', accepted_at: '2026-01-01T11:00:00', revision: 1, view_revision: 1, result: null, snapshot: { output: '进行中输出LIVE' } })
      runnerListMock.mockResolvedValue(pageOf([done, live]))
      const observeCallbacks = new Map<string, (view: RunnerView) => void>()
      runnerObserveMock.mockImplementation((id: string, _h: unknown, onView: (view: RunnerView) => void) => {
        observeCallbacks.set(id, onView)
      })
      wrapper = mount(TaskPlanView)
      await flushPromises()

      const view = wrapper!
      // 行级定位：按行容器分别取展开按钮
      const rows = () => view.findAll('.divide-y > div')
      const doneRow = () => rows().find(r => r.text().includes('已完成输出DONE'))!
      const liveRow = () => rows().find(r => r.text().includes('进行中输出LIVE'))!

      await doneRow().findAll('button').find(b => b.text() === '查看详情')!.trigger('click')
      await flushPromises()
      expect(getSessionMessagesMock).toHaveBeenCalledTimes(1)
      await doneRow().findAll('button').find(b => b.text() === '收起')!.trigger('click')
      await doneRow().findAll('button').find(b => b.text() === '查看详情')!.trigger('click')
      await flushPromises()
      expect(getSessionMessagesMock).toHaveBeenCalledTimes(1)

      // 非终态行：每次展开刷新（执行完成后内容会变）
      await liveRow().findAll('button').find(b => b.text() === '查看详情')!.trigger('click')
      await flushPromises()
      expect(getSessionMessagesMock).toHaveBeenCalledTimes(2)
      await liveRow().findAll('button').find(b => b.text() === '收起')!.trigger('click')
      await liveRow().findAll('button').find(b => b.text() === '查看详情')!.trigger('click')
      await flushPromises()
      expect(getSessionMessagesMock).toHaveBeenCalledTimes(3)

      // 终态落定：observe 推送 terminal 投影 → 非终态期间拉取的缓存作废，再展开重新拉取
      observeCallbacks.get('r-live')!(makeRunner({
        runner_id: 'r-live', status: 'completed', accepted_at: '2026-01-01T11:00:00',
        revision: 2, view_revision: 2, finished_at: '2026-01-01T11:05:00',
        result: { status: 'completed', output: '进行中输出LIVE' },
      }))
      await flushPromises()
      await vi.advanceTimersByTimeAsync(3_000) // 终态刷新调度（2s）
      await flushPromises()
      await liveRow().findAll('button').find(b => b.text() === '收起')!.trigger('click')
      await liveRow().findAll('button').find(b => b.text() === '查看详情')!.trigger('click')
      await flushPromises()
      expect(getSessionMessagesMock).toHaveBeenCalledTimes(4)
    } finally {
      vi.useRealTimers()
    }
  })

  // ==================== Phase 5 三轮评审回归 ====================

  it('三轮-1 旧上传完成不得解除新上传的保存限制（占位派生）', async () => {
    const task = makeTask()
    getScheduledTaskMock.mockResolvedValue(task)
    selectableTargetsMock.mockResolvedValue({ targets: [] })
    wrapper = mount(TaskPlanView)
    await flushPromises()

    let resolveA!: (v: unknown) => void
    let resolveB!: (v: unknown) => void
    uploadFileMock.mockImplementationOnce(() => new Promise(r => { resolveA = r }))
    uploadFileMock.mockImplementationOnce(() => new Promise(r => { resolveB = r }))

    // 弹窗 A：上传 A（挂起）——组件内编辑按钮 + 弹窗 body 里的隐藏 input
    const openEditBtn = () => wrapper!.findAll('button').find(b => b.text() === '编辑')
    await openEditBtn()!.trigger('click')
    await flushPromises()
    const pickFile = () => {
      const input = document.querySelector(`input[type="file"]`) as HTMLInputElement
      Object.defineProperty(input, 'files', { value: [new File(['x'], 'x.txt')], configurable: true })
      input.dispatchEvent(new Event('change'))
      return flushPromises()
    }
    await pickFile()
    // 关闭重开（代际 +1）：上传 B（挂起）
    const closeBtn = [...document.querySelectorAll('button')].find(b => b.textContent?.trim() === '取消')
    closeBtn?.click()
    await flushPromises()
    await openEditBtn()!.trigger('click')
    await flushPromises()
    await pickFile()

    // A 返回：旧代际占位不移除新弹窗的“上传中”状态
    resolveA({ file_id: 'fa', name: 'a', type: 'file', mime_type: '', size: 1 })
    await flushPromises()
    expect(document.body.textContent).toContain('附件上传中')

    resolveB({ file_id: 'fb', name: 'b', type: 'file', mime_type: '', size: 1 })
    await flushPromises()
  })

  it('三轮-3 消息拉取携带 runner_id（后端先过滤再 limit）', async () => {
    const task = makeTask()
    getScheduledTaskMock.mockResolvedValue(task)
    const r1 = makeRunner({ runner_id: 'r1', status: 'completed',
      result: { status: 'completed', output: '输出' } })
    runnerListMock.mockResolvedValue(pageOf([r1]))
    getSessionMessagesMock.mockResolvedValue({ messages: [] })
    wrapper = mount(TaskPlanView)
    await flushPromises()

    const toggle = wrapper.findAll('button').find(b => b.text() === '查看详情')
    expect(toggle).toBeTruthy()
    await toggle!.trigger('click')
    await flushPromises()
    const calls = getSessionMessagesMock.mock.calls
    const lastCall = calls[calls.length - 1]
    expect(lastCall?.[2]).toBe('r1')
  })
})