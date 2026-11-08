import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import WecomProxyTasks from '@/components/wecomConversation/WecomProxyTasks.vue'
import { WecomProxyApiError } from '@/api/wecomProxy'

// 覆盖七个 API 封装函数，保留 proxyErrorText/WecomProxyApiError 真实实现
const proxyApi = vi.hoisted(() => ({
  listProxyTasks: vi.fn(), getProxyTask: vi.fn(), createProxyTask: vi.fn(),
  editProxyTask: vi.fn(), deleteProxyTask: vi.fn(), startProxyTask: vi.fn(), stopProxyTask: vi.fn(),
}))
const conversationApi = vi.hoisted(() => ({ listDirectory: vi.fn() }))
const subagentApi = vi.hoisted(() => ({ listSubagents: vi.fn() }))
const routeState = vi.hoisted(() => ({ route: null as { query: Record<string, string> } | null }))
vi.mock('@/api/wecomProxy', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...proxyApi,
}))
vi.mock('@/api/wecomConversation', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ...conversationApi,
}))
vi.mock('@/api/subagent', () => subagentApi)
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({
  admin: { value: null }, isLoggedIn: { value: true }, logout: vi.fn(),
}) }))
vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  routeState.route = reactive({ query: {} })
  return { useRoute: () => routeState.route, useRouter: () => ({ push: vi.fn() }) }
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

const taskBase = {
  id: 7, tenant_id: 't1', user_id: 'u1', profile_id: 'main', instance_id: null,
  device_id: 'device-a', wecom_account_id: 'account-a', self_user_id: 'account-a',
  title: '售前群代理', agent_id: 'agent-a', instruction: '有咨询就回答',
  stop_requested: false, revision: 1, created_at: '2026-11-07T09:00:00Z',
  updated_at: '2026-11-08T09:00:00Z', deleted_at: null,
}
const stoppedTask = { ...taskBase, status: 'stopped', active_target_count: 2 }
const runningTask = { ...taskBase, id: 8, title: '运行中的任务', status: 'running', active_target_count: 1 }
const deletedTask = { ...taskBase, id: 9, title: '已删除任务', status: 'deleted', active_target_count: 0,
  deleted_at: '2026-11-08T08:00:00Z' }

let wrapper: VueWrapper | null = null

async function mountPage() {
  wrapper = mount(WecomProxyTasks, {
    global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } },
  })
  await flushPromises()
  return wrapper
}

function dialogs() {
  return [...document.body.querySelectorAll('[role="dialog"]')]
}

beforeEach(() => {
  vi.clearAllMocks()
  routeState.route!.query = {}
  proxyApi.listProxyTasks.mockResolvedValue({ tasks: [stoppedTask, runningTask] })
  proxyApi.getProxyTask.mockResolvedValue({ task: { ...stoppedTask, targets: [], batches: [] } })
  proxyApi.createProxyTask.mockResolvedValue({ task: stoppedTask })
  proxyApi.editProxyTask.mockResolvedValue({ task: stoppedTask })
  proxyApi.startProxyTask.mockResolvedValue({ task: runningTask })
  proxyApi.stopProxyTask.mockResolvedValue({ task: stoppedTask })
  proxyApi.deleteProxyTask.mockResolvedValue({ task: deletedTask })
  conversationApi.listDirectory.mockResolvedValue({
    device_id: 'device-a', self_user_id: 'account-a', source_generation: 'gen-a',
    conversations: [
      { session: 'ws1.acc.R%3Aone', name: '售前群', kind: 'group', peer_platform: 'wecom', added: true },
      { session: 'ws1.acc.S%3Atwo', name: '未登记单聊', kind: 'single', peer_platform: 'wechat', added: false },
    ],
    next_cursor: null,
  })
  subagentApi.listSubagents.mockResolvedValue({ success: true, data: [
    { agent_id: 'agent-a', name: '售前智能体', description: '', type: 'builtin' },
  ] })
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('企微代理任务页', () => {
  it('列表显示状态、标题、监控会话数、智能体；运行中禁编辑并说明原因', async () => {
    await mountPage()
    const rows = wrapper!.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.text()).toContain('已停止')
    expect(rows[0]!.text()).toContain('售前群代理')
    expect(rows[0]!.text()).toContain('2')
    expect(rows[1]!.text()).toContain('运行中')
    const runningEdit = rows[1]!.findAll('button').find(button => button.text() === '编辑')!
    expect(runningEdit.attributes('disabled')).toBeDefined()
    expect(runningEdit.attributes('title')).toBe('运行中不可编辑，请先停止')
    expect(rows[1]!.findAll('button').map(button => button.text())).toContain('停止')
    const stoppedDelete = rows[0]!.findAll('button').find(button => button.text() === '删除')
    expect(stoppedDelete).toBeDefined()
  })

  it('勾选显示已删除任务后按 includeDeleted 只读查看，已删除行只有详情', async () => {
    await mountPage()
    proxyApi.listProxyTasks.mockResolvedValue({ tasks: [stoppedTask, runningTask, deletedTask] })
    await wrapper!.find('[data-testid="include-deleted"]').setValue()
    await flushPromises()
    expect(proxyApi.listProxyTasks).toHaveBeenLastCalledWith({ includeDeleted: true, limit: 50, offset: 0 })
    const rows = wrapper!.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[2]!.text()).toContain('已删除')
    expect(rows[2]!.findAll('button').map(button => button.text())).toEqual(['详情'])
  })

  it('创建向导校验必填项；只允许已登记会话；提交时如实展示设备检查进行中', async () => {
    const pending = deferred<{ task: typeof stoppedTask }>()
    proxyApi.createProxyTask.mockReturnValueOnce(pending.promise)
    await mountPage()
    await wrapper!.find('[data-testid="create-proxy"]').trigger('click')
    await flushPromises()
    expect(subagentApi.listSubagents).toHaveBeenCalled()
    let dialog = dialogs().find(item => item.textContent!.includes('任务标题'))!

    // 缺会话与智能体时先本地校验，不发起请求
    ;(dialog.querySelector('input[aria-label="任务标题"]') as HTMLInputElement).value = '售前群代理'
    ;(dialog.querySelector('input[aria-label="任务标题"]') as HTMLInputElement).dispatchEvent(new Event('input', { bubbles: true }))
    ;(dialog.querySelector('textarea[aria-label="任务指令"]') as HTMLTextAreaElement).value = '有客户咨询就用一句话回答'
    ;(dialog.querySelector('textarea[aria-label="任务指令"]') as HTMLTextAreaElement).dispatchEvent(new Event('input', { bubbles: true }))
    ;[...dialog.querySelectorAll('button')].find(button => button.textContent === '创建任务')!.click()
    await flushPromises()
    expect(proxyApi.createProxyTask).not.toHaveBeenCalled()
    expect(dialog.textContent).toContain('请至少选择 1 个监控会话')

    // 会话目录：未登记会话禁选
    ;[...dialog.querySelectorAll('button')].find(button => button.textContent === '从本地目录选择会话')!.click()
    await flushPromises()
    const picker = dialogs().find(item => item.textContent!.includes('仅已登记会话'))!
    expect((picker.querySelector('input[aria-label="选择未登记单聊"]') as HTMLInputElement).disabled).toBe(true)
    ;(picker.querySelector('input[aria-label="选择售前群"]') as HTMLInputElement).click()
    await flushPromises()
    ;[...picker.querySelectorAll('button')].find(button => button.textContent === '完成选择')!.click()
    await flushPromises()

    const select = dialog.querySelector('select[aria-label="选择智能体"]') as HTMLSelectElement
    select.value = 'agent-a'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    ;[...dialog.querySelectorAll('button')].find(button => button.textContent === '创建任务')!.click()
    await flushPromises()
    expect(proxyApi.createProxyTask).toHaveBeenCalledWith({
      title: '售前群代理', instruction: '有客户咨询就用一句话回答',
      agent_id: 'agent-a', sessions: ['ws1.acc.R%3Aone'],
    })
    // 提交进行中如实说明后端正在执行 doctor＋标定真实检查
    expect(dialogs()[0]!.textContent).toContain('正在创建并执行设备与账号真实检查')

    pending.reject(new WecomProxyApiError('', 'PROXY_CLIENT_UPGRADE', 409))
    await flushPromises()
    expect(document.body.querySelector('[data-testid="form-error"]')!.textContent).toContain('客户端需升级')
  })

  it('停止确认说明当前批次结束才落已停止；确认后调用停止并刷新', async () => {
    await mountPage()
    await wrapper!.findAll('tbody tr')[1]!.findAll('button').find(button => button.text() === '停止')!.trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-testid="confirm-message"]')!.textContent).toContain('当前正在执行的批次将结束或进入等待')
    ;[...document.body.querySelectorAll('button')].find(button => button.textContent === '确认')!.click()
    await flushPromises()
    await flushPromises()
    expect(proxyApi.stopProxyTask).toHaveBeenCalledWith(8)
    expect(proxyApi.listProxyTasks).toHaveBeenCalledTimes(2)
  })

  it('软删除确认文案说明历史仍可只读查看，且仅已停止任务可删', async () => {
    await mountPage()
    await wrapper!.findAll('tbody tr')[0]!.findAll('button').find(button => button.text() === '删除')!.trigger('click')
    await flushPromises()
    const message = document.body.querySelector('[data-testid="confirm-message"]')!.textContent!
    expect(message).toContain('退出监控')
    expect(message).toContain('只读查看')
    expect(message).toContain('企微原聊天不受影响')
    ;[...document.body.querySelectorAll('button')].find(button => button.textContent === '确认')!.click()
    await flushPromises()
    expect(proxyApi.deleteProxyTask).toHaveBeenCalledWith(7)
  })

  it('详情展示目标游标位置与批次 end_json：回复文本、本机客户端已记录发送、未回复原因', async () => {
    routeState.route!.query = { task: '7' }
    proxyApi.getProxyTask.mockResolvedValue({ task: { ...stoppedTask, targets: [
      { id: 1, tenant_id: 't1', user_id: 'u1', task_id: 7, session: 'ws1.acc.R%3Aone', web_session_id: 'wecom_proxy_x',
        source_generation: 'gen-a', wb1_cursor: 'wb1.cursor-value', baseline_origin: 'initial',
        processed_position: 100, latest_seen_msg_id: 110, idle_read_count: 3, status: 'active', halted_reason: null,
        halted_at: null, created_at: '', updated_at: '' },
      { id: 2, tenant_id: 't1', user_id: 'u1', task_id: 7, session: 'ws1.acc.R%3Ahalt', web_session_id: null,
        source_generation: 'gen-a', wb1_cursor: 'wb1.y', baseline_origin: 'edit_join_read',
        processed_position: 5, latest_seen_msg_id: 6, status: 'halted', halted_reason: '零发送重试再失败',
        halted_at: '', created_at: '', updated_at: '' },
    ], batches: [
      { id: 31, task_id: 7, session: 'ws1.acc.R%3Aone', batch_end_msg_id: 100, agent_id: 'agent-a',
        web_session_id: 'wecom_proxy_x', runner_id: 'runner-one', status: 'succeeded', retry_count: 0,
        credit_cost: 0.42,
        end_json: { schema_version: 1, needs_reply: true, replies: [
          { inbound_msg_ids: [97, 99], text: '您好，两款都有货。', receipt_id: 'rcpt-1',
            verification: 'local-record', verification_detail: { method: 'local-record', message_id: 110, source_generation: 'gen-a' } },
        ], unanswered: [{ msg_id: 98, reason: 'send_failed', cli_error: { code: 'SEND_TIMEOUT', invocation_id: 'inv-1' } }] },
        cli_error: null, created_at: '2026-11-08T09:00:00Z', updated_at: '', started_at: null, finished_at: '2026-11-08T09:05:00Z' },
      { id: 30, task_id: 7, session: 'ws1.acc.R%3Ahalt', batch_end_msg_id: 6, agent_id: 'agent-a',
        web_session_id: null, runner_id: 'runner-two', status: 'failed', retry_count: 1, end_json: null,
        credit_cost: 0.55,
        cli_error: 'CLI 发送超时', created_at: '2026-11-08T08:00:00Z', updated_at: '', started_at: null, finished_at: null },
    ] } })
    await mountPage()
    expect(proxyApi.getProxyTask).toHaveBeenCalledWith(7, { batchLimit: 50 })
    expect(wrapper!.find('[data-testid="detail-title"]').text()).toBe('售前群代理')

    // 目标列表：游标、位置、基线来源、停住原因、空闲读取列
    expect(wrapper!.text()).toContain('wb1.cursor-value'.slice(0, 8))
    expect(wrapper!.text()).toContain('100')
    expect(wrapper!.text()).toContain('创建初选')
    expect(wrapper!.text()).toContain('编辑加入')
    expect(wrapper!.text()).toContain('零发送重试再失败')
    expect(wrapper!.text()).toContain('空闲读取')

    // 批次 31：回复文本与 CLI 证据仅称“本机客户端已记录发送”
    const okBatch = wrapper!.find('[data-testid="batch-31"]')
    expect(okBatch.text()).toContain('您好，两款都有货。')
    expect(okBatch.text()).toContain('本机客户端已记录发送')
    expect(okBatch.text()).toContain('回执 rcpt-1')
    expect(okBatch.text()).toContain('本地库消息 #110')
    expect(okBatch.text()).toContain('回答积分 0.42')
    expect(okBatch.text()).toContain('消息 #98：发送失败')
    expect(okBatch.text()).toContain('SEND_TIMEOUT')
    expect(okBatch.text()).not.toContain('对方已收到')

    // 批次 30：失败与重试计数如实展示
    const failBatch = wrapper!.find('[data-testid="batch-30"]')
    expect(failBatch.text()).toContain('已重试 1 次')
    expect(failBatch.text()).toContain('CLI 发送超时')
    // 空闲读取与回答积分按任务累计展示（后端聚合投影）
    expect(wrapper!.text()).toContain('按任务累计展示')
  })

  it('详情中无需回复批次展示判定，运行中任务禁编辑', async () => {
    routeState.route!.query = { task: '8' }
    proxyApi.getProxyTask.mockResolvedValue({ task: { ...runningTask, targets: [], batches: [
      { id: 32, task_id: 8, session: 'ws1.acc.R%3Aone', batch_end_msg_id: 120, agent_id: 'agent-a',
        web_session_id: null, runner_id: 'runner-three', status: 'succeeded', retry_count: 0,
        end_json: { schema_version: 1, needs_reply: false, replies: [], unanswered: [] },
        cli_error: null, created_at: '', updated_at: '', started_at: null, finished_at: null },
    ] } })
    await mountPage()
    expect(wrapper!.text()).toContain('本批判定无需回复')
    const editButton = wrapper!.findAll('button').find(button => button.text() === '编辑')!
    expect(editButton.attributes('disabled')).toBeDefined()
    expect(editButton.attributes('title')).toBe('运行中不可编辑，请先停止')
  })

  it('停止后编辑预填配置，保存只携带变更差异', async () => {
    proxyApi.getProxyTask.mockResolvedValue({ task: { ...stoppedTask, targets: [
      { id: 1, task_id: 7, session: 'ws1.acc.R%3Aone', status: 'active', wb1_cursor: 'wb1.x',
        baseline_origin: 'initial', processed_position: 1, latest_seen_msg_id: 1, halted_reason: null },
    ], batches: [] } })
    await mountPage()
    await wrapper!.findAll('tbody tr')[0]!.findAll('button').find(button => button.text() === '编辑')!.trigger('click')
    await flushPromises()
    const dialog = dialogs()[0]!
    expect((dialog.querySelector('input[aria-label="任务标题"]') as HTMLInputElement).value).toBe('售前群代理')
    // 已选会话以稳定标识展示（目录名需在目录加载后回填）
    expect(dialog.textContent).toContain('ws1.acc.')
    expect(dialog.textContent).toContain('读取当前最新消息水位')

    ;(dialog.querySelector('textarea[aria-label="任务指令"]') as HTMLTextAreaElement).value = '新指令'
    ;(dialog.querySelector('textarea[aria-label="任务指令"]') as HTMLTextAreaElement).dispatchEvent(new Event('input', { bubbles: true }))
    ;[...dialog.querySelectorAll('button')].find(button => button.textContent === '保存修改')!.click()
    await flushPromises()
    expect(proxyApi.editProxyTask).toHaveBeenCalledWith(7, { title: '售前群代理', instruction: '新指令' })
    // 会话未变化时不携带 add/remove；智能体选择在编辑中被禁用
    const call = proxyApi.editProxyTask.mock.calls[0]![1] as Record<string, unknown>
    expect('add_sessions' in call).toBe(false)
    expect('remove_sessions' in call).toBe(false)
    expect((dialog.querySelector('select[aria-label="选择智能体"]') as HTMLSelectElement).disabled).toBe(true)
  })

  it('列表运行信息列：最近检查、最新消息、空闲读取、回答积分与最近错误截断悬停', async () => {
    proxyApi.listProxyTasks.mockResolvedValue({ tasks: [{ ...stoppedTask,
      last_checked_at: '2026-11-08T10:00:00+08:00', latest_seen_msg_id: 120,
      idle_read_count: 5, credit_cost: 0.6, last_error: 'PROXY_READ_FAILED' }] })
    await mountPage()
    const header = wrapper!.find('thead')
    expect(header.text()).toContain('最近检查')
    expect(header.text()).toContain('最新消息')
    expect(header.text()).toContain('空闲读取')
    expect(header.text()).toContain('回答积分')
    expect(header.text()).toContain('最近错误')
    const row = wrapper!.findAll('tbody tr')[0]!
    expect(row.text()).toContain('5')
    expect(row.text()).toContain('0.60')
    expect(row.text()).toContain('120')
    const errorCell = row.find('.truncate')
    expect(errorCell.attributes('title')).toBe('PROXY_READ_FAILED')
  })

  it('任务列表加载更多：按返回条数判 has_more，追加渲染且不再显示按钮', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({
      ...stoppedTask, id: index + 1, title: `任务 ${index + 1}` }))
    proxyApi.listProxyTasks.mockResolvedValueOnce({ tasks: firstPage })
      .mockResolvedValueOnce({ tasks: [{ ...stoppedTask, id: 51, title: '任务 51' }] })
    await mountPage()
    expect(wrapper!.find('[data-testid="load-more-tasks"]').exists()).toBe(true)
    expect(wrapper!.text()).toContain('任务 50')
    await wrapper!.find('[data-testid="load-more-tasks"]').trigger('click')
    await flushPromises()
    expect(proxyApi.listProxyTasks).toHaveBeenLastCalledWith({ includeDeleted: false, limit: 50, offset: 50 })
    expect(wrapper!.text()).toContain('任务 51')
    expect(wrapper!.find('[data-testid="load-more-tasks"]').exists()).toBe(false)
  })

  it('详情批次加载更多：按 offset 追加渲染新页批次', async () => {
    routeState.route!.query = { task: '7' }
    const batch = (id: number) => ({ id, task_id: 7, session: 'ws1.acc.R%3Aone',
      batch_end_msg_id: 100 + id, agent_id: 'agent-a', web_session_id: null,
      runner_id: `runner-${id}`, status: 'succeeded', retry_count: 0, credit_cost: 0,
      end_json: null, cli_error: null, created_at: '', updated_at: '', started_at: null, finished_at: null })
    proxyApi.getProxyTask.mockResolvedValueOnce({ task: { ...stoppedTask, targets: [],
      batches: [batch(52), batch(51)], batches_has_more: true } })
      .mockResolvedValueOnce({ task: { ...stoppedTask, targets: [],
        batches: [batch(50)], batches_has_more: false } })
    await mountPage()
    expect(wrapper!.find('[data-testid="load-more-batches"]').exists()).toBe(true)
    await wrapper!.find('[data-testid="load-more-batches"]').trigger('click')
    await flushPromises()
    expect(proxyApi.getProxyTask).toHaveBeenLastCalledWith(7, { batchLimit: 50, batchOffset: 2 })
    expect(wrapper!.find('[data-testid="batch-50"]').exists()).toBe(true)
    expect(wrapper!.find('[data-testid="batch-52"]').exists()).toBe(true)
    expect(wrapper!.find('[data-testid="load-more-batches"]').exists()).toBe(false)
  })
})
