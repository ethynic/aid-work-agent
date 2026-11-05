import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import WecomConversationDaily from '@/components/wecomConversation/WecomConversationDaily.vue'

const api = vi.hoisted(() => ({
  listConversations: vi.fn(), dailyReports: vi.fn(), getDailyPlan: vi.fn(),
  generateDailyReport: vi.fn(), getDailyExecution: vi.fn(), listDailyExecutions: vi.fn(),
  listDirectory: vi.fn(), previewDirectory: vi.fn(), addConversations: vi.fn(),
  saveDailyPlan: vi.fn(), pauseDailyPlan: vi.fn(), resumeDailyPlan: vi.fn(),
}))
const routeState = vi.hoisted(() => ({ route: null as { query: Record<string, string> } | null }))
vi.mock('@/api/wecomConversation', () => api)
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
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

const conversation = (id: number) => ({ id, name: `会话 ${id}`, daily_plan_task_id: null })
const report = (id: number, overview: string) => ({ conversation_id: id, name: `会话 ${id}`,
  timezone: 'Asia/Shanghai', daily_plan_task_id: null, summary: { overview }, coverage: null,
  snapshot_at: null, generated_at: null, source_runner_id: null, report_updated_at: null })
let wrapper: VueWrapper | null = null
let focusHost: HTMLElement | null = null

beforeEach(() => {
  vi.clearAllMocks()
  routeState.route!.query = {}
  api.getDailyPlan.mockResolvedValue({ plan: null })
  api.dailyReports.mockResolvedValue({ reports: [], has_more: false, next_cursor: null, last_generation: null })
  api.listDailyExecutions.mockResolvedValue({ executions: [], has_more: false, next_cursor: null })
  api.listDirectory.mockResolvedValue({ device_id: 'device-a', self_user_id: 'account-a', source_generation: 'gen-a', conversations: [], next_cursor: null })
  api.generateDailyReport.mockResolvedValue({ runner_id: 'runner-one', status: 'queued' })
  api.getDailyExecution.mockResolvedValue({ execution: { runner_id: 'runner-one', status: 'queued',
    accepted_at: '2026-11-04T08:00:00Z', target_count: 201, targets: [] } })
})
afterEach(() => { wrapper?.unmount(); wrapper = null; focusHost?.remove(); focusHost = null; vi.restoreAllMocks() })

describe('每日总结工作台', () => {
  it('新版日报只展示概览、文本跟进和已发生异常，保留消息证据', async () => {
    api.dailyReports.mockResolvedValue({ reports: [{ ...report(1, '孙晨咨询优惠和女鞋，确认购买两双。'),
      summary: { overview: '孙晨咨询优惠和女鞋，确认购买两双。',
        pending_human: [{ detail: '确认颜色和收件方式。', evidence: [1980, 1981] }],
        anomalies: [{ detail: '欢迎语重复、截断。', evidence: [1970] }] },
      coverage: { day_count: 15 }, generated_at: '2026-11-05T11:57:24',
      snapshot_at: '2026-11-05T11:56:38', source_runner_id: 'runner-saved' }],
      has_more: false, next_cursor: null, last_generation: null })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    const card = wrapper.find('article')
    expect(card.find('h4').classes()).toContain('text-xl')
    expect(card.text()).toContain('当日 15 条消息')
    expect(card.text()).toContain('确认颜色和收件方式。')
    expect(card.text()).toContain('欢迎语重复、截断。')
    expect(card.findAll('h5').map(heading => heading.text())).toEqual(['当日概览', '待跟进', '异常'])
    expect(card.text()).not.toContain('咨询与问题分类')
    expect(card.find('button[title="在会话记录中定位消息 1970"]').exists()).toBe(true)
    expect(card.find('details').attributes('open')).toBeUndefined()
  })

  it('旧版已保存报告在只读日期仍能看到原有分类和已处理内容', async () => {
    api.dailyReports.mockResolvedValue({ reports: [{ ...report(1, '旧版概览'),
      summary: { overview: '旧版概览', categories: [{ topic: '咨询', count: 1, detail: '店铺活动' }],
        handled: [{ topic: '回复', detail: '已告知活动', evidence: [1971] }],
        pending_human: [], noteworthy: [] } }],
      has_more: false, next_cursor: null, last_generation: null })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('咨询 ×1：店铺活动')
    expect(wrapper.text()).toContain('回复：已告知活动')
  })

  it('只有寒暄的日报只显示无实质内容，不空挂跟进和异常标题', async () => {
    api.dailyReports.mockResolvedValue({ reports: [{ ...report(1, '当日无实质内容'),
      summary: { overview: '当日无实质内容', pending_human: [], anomalies: [] } }],
      has_more: false, next_cursor: null, last_generation: null })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.find('article').text()).toContain('当日无实质内容')
    expect(wrapper.findAll('article h5').map(heading => heading.text())).toEqual(['当日概览'])
  })

  it('跨日生成的报告在辅助信息中保留日期', async () => {
    api.dailyReports.mockResolvedValue({ reports: [{ ...report(1, '跨日报告'),
      generated_at: '2020-01-02T11:57:24', snapshot_at: '2020-01-01T23:56:38' }],
      has_more: false, next_cursor: null, last_generation: null })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.find('article header').text()).toContain('生成于 2020/1/2')
    expect(wrapper.find('article header').text()).toContain('读取截至 2020/1/1')
  })

  it('添加目录只在单聊显示会话级平台，群聊不显示 unknown', async () => {
    api.listDirectory.mockResolvedValue({ device_id: 'device-a', self_user_id: 'account-a', source_generation: 'gen-a',
      conversations: [
        { session: 'R:group', name: '项目群', kind: 'group', peer_platform: 'unknown', added: false },
        { session: 'S:single', name: '客户单聊', kind: 'single', peer_platform: 'wechat', added: false },
      ], next_cursor: null })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text().includes('添加会话'))!.trigger('click')
    await flushPromises()
    const dialog = document.body.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('R:group')
    expect(dialog.textContent).not.toContain('unknown')
    expect(dialog.textContent).toContain('S:single · wechat')
  })

  it('读完超过 200 个会话后才允许默认全选提交，一次 Runner 含完整范围', async () => {
    const second = deferred<{ reports: ReturnType<typeof report>[]; has_more: boolean; next_cursor: null; last_generation: null }>()
    api.dailyReports.mockResolvedValueOnce({ reports: Array.from({ length: 200 }, (_, index) => report(index + 1, '已保存')),
      has_more: true, next_cursor: 200, last_generation: null }).mockReturnValueOnce(second.promise)
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(api.dailyReports).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ cursor: 200, limit: 200 }))
    expect(api.listConversations).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="generate-daily"]').attributes('disabled')).toBeDefined()

    second.resolve({ reports: [report(201, '已保存')], has_more: false, next_cursor: null, last_generation: null })
    await flushPromises()
    expect(wrapper.text()).toContain('全部会话（201）')
    expect(wrapper.find('[data-testid="generate-daily"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('[data-testid="generate-daily"]').trigger('click')
    await flushPromises()
    expect(api.generateDailyReport).toHaveBeenCalledOnce()
    expect(api.generateDailyReport.mock.calls[0]![0].conversation_ids).toEqual(Array.from({ length: 201 }, (_, index) => index + 1))
  })

  it('切换到昨日后，晚返回的今日报告不能覆盖当前日期', async () => {
    const stale = deferred<{ reports: ReturnType<typeof report>[]; has_more: boolean; next_cursor: null; last_generation: null }>()
    api.listConversations.mockResolvedValue({ conversations: [conversation(1)] })
    api.dailyReports.mockImplementationOnce(() => stale.promise)
      .mockResolvedValueOnce({ reports: [report(1, '昨日内容')], has_more: false, next_cursor: null, last_generation: null })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    const yesterdayButton = wrapper.findAll('button').find(button => button.text() === '昨日')
    expect(yesterdayButton).toBeDefined()
    await yesterdayButton!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('昨日内容')

    stale.resolve({ reports: [report(1, '过期今日内容')], has_more: false, next_cursor: null, last_generation: null })
    await flushPromises()
    expect(wrapper.text()).toContain('昨日内容')
    expect(wrapper.text()).not.toContain('过期今日内容')
    expect(wrapper.find('[data-testid="generate-daily"]').exists()).toBe(false)
    expect(wrapper.findAll('button').some(button => button.text() === '定时执行配置' || button.text().includes('添加会话'))).toBe(false)
  })

  it('今日提交尚未返回就切昨日，晚受理不会在历史页轮询或显示提交错误', async () => {
    const pending = deferred<{ runner_id: string }>()
    api.listConversations.mockResolvedValue({ conversations: [conversation(1)] })
    api.generateDailyReport.mockReturnValue(pending.promise)
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    await wrapper.find('[data-testid="generate-daily"]').trigger('click')
    const yesterdayButton = wrapper.findAll('button').find(button => button.text() === '昨日')!
    await yesterdayButton.trigger('click')
    pending.resolve({ runner_id: 'accepted-after-navigation' })
    await flushPromises()
    expect(api.getDailyExecution).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('accepted-after-navigation')
    expect(wrapper.find('[data-testid="generate-daily"]').exists()).toBe(false)
  })

  it('复用页面时响应通知日期和计划 ID 的路由变化', async () => {
    api.listConversations.mockResolvedValue({ conversations: [conversation(1)] })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    const yesterday = new Date(Date.now() - 86400000).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
    routeState.route!.query = { date: yesterday, task_id: 'scheduled-1' }
    await flushPromises()
    expect(wrapper.text()).toContain(`昨天 · ${yesterday}`)
    expect(api.dailyReports.mock.calls[api.dailyReports.mock.calls.length - 1]?.[1]).toMatchObject({ taskId: 'scheduled-1' })
    expect(wrapper.find('[data-testid="generate-daily"]').exists()).toBe(false)

    routeState.route!.query = {}
    await flushPromises()
    expect(wrapper.text()).toContain('今天 ·')
    expect(api.dailyReports.mock.calls[api.dailyReports.mock.calls.length - 1]?.[1]).toMatchObject({ taskId: undefined })
    expect(wrapper.find('[data-testid="generate-daily"]').exists()).toBe(true)
  })

  it('501 个会话和报告跨三页完整读取后，显示最后一项并提交完整手动范围', async () => {
    const finalReports = deferred<{ reports: ReturnType<typeof report>[]; has_more: boolean; next_cursor: null; last_generation: null }>()
    api.dailyReports.mockImplementation((_date: string, options: { cursor?: number }) => options.cursor === 400
      ? finalReports.promise
      : Promise.resolve({ reports: Array.from({ length: 200 }, (_, index) => report((options.cursor || 0) + index + 1, '旧摘要')),
        has_more: true, next_cursor: (options.cursor || 0) + 200, last_generation: null }))
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(api.listConversations).not.toHaveBeenCalled()
    expect(api.dailyReports).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ cursor: 400, limit: 200 }))
    expect(wrapper.find('[data-testid="generate-daily"]').attributes('disabled')).toBeDefined()

    finalReports.resolve({ reports: Array.from({ length: 101 }, (_, index) => report(index + 401, index === 100 ? '第 501 项摘要' : '旧摘要')),
      has_more: false, next_cursor: null, last_generation: null })
    await flushPromises()
    expect(wrapper.text()).toContain('全部会话（501）')
    expect(wrapper.text()).toContain('第 501 项摘要')
    expect(wrapper.find('[data-testid="generate-daily"]').attributes('disabled')).toBeUndefined()
    await wrapper.find('[data-testid="generate-daily"]').trigger('click')
    await flushPromises()
    expect(api.generateDailyReport.mock.calls[0]![0].conversation_ids).toEqual(Array.from({ length: 501 }, (_, index) => index + 1))
  })

  it('报告第二页未到时不允许提交，并将旧成功报告与本次失败分开展示', async () => {
    const next = deferred<{ reports: ReturnType<typeof report>[]; has_more: boolean; next_cursor: null; last_generation: unknown }>()
    api.listConversations.mockResolvedValue({ conversations: [conversation(1)] })
    api.dailyReports.mockResolvedValueOnce({ reports: [report(1, '此前成功')], has_more: true,
      next_cursor: 1, last_generation: { runner_id: 'runner-new', status: 'failed',
        accepted_at: '2026-11-04T08:00:00Z', target_count: 1, targets: [{
          conversation_id: 1, status: 'failed', phase: 'reading', error_code: 'READ_FAILED',
          reason_label: null, started_at: null, finished_at: null, diagnostic: 'safe',
        }] } }).mockImplementationOnce(() => next.promise)
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.find('[data-testid="generate-daily"]').attributes('disabled')).toBeDefined()

    next.resolve({ reports: [], has_more: false, next_cursor: null, last_generation: null })
    await flushPromises()
    expect(wrapper.text()).toContain('本次失败')
    expect(wrapper.text()).toContain('此前保存的成功报告')
    expect(wrapper.text()).toContain('此前成功')
    expect(wrapper.find('[data-testid="generate-daily"]').attributes('disabled')).toBeUndefined()
  })

  it('中断和未执行目标展示执行阶段，旧报告标明来源', async () => {
    api.listConversations.mockResolvedValue({ conversations: [conversation(1), conversation(2)] })
    api.dailyReports.mockResolvedValue({ reports: [
      { ...report(1, '旧概览'), source_runner_id: 'runner-old', generated_at: '2026-11-03T10:00:00Z' },
      report(2, '')], has_more: false, next_cursor: null, last_generation: {
      runner_id: 'runner-new', status: 'cancelled', accepted_at: '2026-11-04T08:00:00Z',
      target_count: 2, targets: [
        { conversation_id: 1, status: 'interrupted', phase: 'reading', reason_label: '执行已中断' },
        { conversation_id: 2, status: 'not_executed', phase: 'queued', reason_label: '未轮到该会话' },
      ],
    } })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('执行已中断')
    expect(wrapper.text()).toContain('未轮到该会话')
    expect(wrapper.text()).toContain('阶段 queued')
    expect(wrapper.text()).toContain('执行 runner-new')
    expect(wrapper.text()).toContain('已保存报告 · 执行 runner-old')
  })

  it('卡片与执行详情共用可读失败原因，保留安全错误码且不展示诊断原文', async () => {
    const execution = { runner_id: 'runner-errors', status: 'failed', accepted_at: '2026-11-04T08:00:00Z',
      target_count: 3, error_code: 'WECOM_SOURCE_MISMATCH', targets: [
        { conversation_id: 1, name: '读取群', status: 'failed', phase: 'reading', reason_label: null,
          error_code: 'READ_FAILED', diagnostic: 'secret message body' },
        { conversation_id: 2, name: '总结群', status: 'failed', phase: 'analyzing', reason_label: null,
          error_code: 'WECOM_SUMMARY_TRUNCATED', diagnostic: 'secret summary text' },
        { conversation_id: 3, name: '未执行群', status: 'not_executed', phase: 'queued', reason_label: null,
          error_code: null, diagnostic: 'secret source' },
      ] }
    api.dailyReports.mockResolvedValue({ reports: [report(1, ''), report(2, ''), report(3, '')],
      has_more: false, next_cursor: null, last_generation: execution })
    api.listDailyExecutions.mockResolvedValue({ executions: [execution], has_more: false, next_cursor: null })
    api.getDailyExecution.mockResolvedValue({ execution })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('会话记录读取失败，请稍后重试')
    expect(wrapper.text()).toContain('错误码 READ_FAILED')
    expect(wrapper.text()).toContain('总结结果被截断，请重试')
    expect(wrapper.text()).toContain('错误码 WECOM_SUMMARY_TRUNCATED')
    expect(wrapper.text()).toContain('企微账号或会话来源已变化')
    expect(wrapper.text()).toContain('错误码 WECOM_SOURCE_MISMATCH')
    await wrapper.findAll('button').find(button => button.text() === '查看执行记录')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '查看详情')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('读取群 · 本次失败 · 会话记录读取失败')
    expect(wrapper.text()).toContain('未执行群 · 未执行 · 企微账号或会话来源已变化')
    expect(wrapper.text()).not.toContain('secret message body')
    expect(wrapper.text()).not.toContain('secret summary text')
    expect(wrapper.text()).not.toContain('secret source')
  })

  it('主动取消时 reading/analyzing 的中断及尚未执行目标不误报失败', async () => {
    const execution = { runner_id: 'runner-cancel', status: 'cancelled', accepted_at: '2026-11-04T08:00:00Z',
      target_count: 3, error_code: null, targets: [
        { conversation_id: 1, status: 'interrupted', phase: 'reading', reason_label: null, error_code: null },
        { conversation_id: 2, status: 'interrupted', phase: 'analyzing', reason_label: null, error_code: null },
        { conversation_id: 3, status: 'not_executed', phase: 'queued', reason_label: null, error_code: null },
      ] }
    api.dailyReports.mockResolvedValue({ reports: [report(1, ''), report(2, ''), report(3, '')],
      has_more: false, next_cursor: null, last_generation: execution })
    api.listDailyExecutions.mockResolvedValue({ executions: [execution], has_more: false, next_cursor: null })
    api.getDailyExecution.mockResolvedValue({ execution })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.text().match(/本次执行已取消，当前会话未完成/g)).toHaveLength(2)
    expect(wrapper.text()).toContain('本次执行已取消，该会话未执行')
    expect(wrapper.text()).not.toContain('会话记录读取失败')
    expect(wrapper.text()).not.toContain('总结生成失败')
    await wrapper.findAll('button').find(button => button.text() === '查看执行记录')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '查看详情')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('未执行 · 本次执行已取消，该会话未执行')
  })

  it('单目标来源失效导致整轮失败时，中断卡片与详情继承安全全局原因和错误码', async () => {
    const execution = { runner_id: 'runner-source', status: 'failed', accepted_at: '2026-11-04T08:00:00Z',
      target_count: 1, error_code: 'WECOM_SOURCE_MISMATCH', targets: [
        { conversation_id: 1, name: '来源群', status: 'interrupted', phase: 'reading',
          reason_label: null, error_code: null, diagnostic: 'private read content' },
      ] }
    api.dailyReports.mockResolvedValue({ reports: [report(1, '')], has_more: false,
      next_cursor: null, last_generation: execution })
    api.listDailyExecutions.mockResolvedValue({ executions: [execution], has_more: false, next_cursor: null })
    api.getDailyExecution.mockResolvedValue({ execution })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('企微账号或会话来源已变化，本次执行已停止')
    expect(wrapper.text()).toContain('错误码 WECOM_SOURCE_MISMATCH')
    await wrapper.findAll('button').find(button => button.text() === '查看执行记录')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '查看详情')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('来源群 · 执行中断 · 企微账号或会话来源已变化')
    expect(wrapper.text()).not.toContain('private read content')
  })

  it('未知整轮错误码只给中性中断说明并保留安全码', async () => {
    api.dailyReports.mockResolvedValue({ reports: [report(1, '')], has_more: false,
      next_cursor: null, last_generation: { runner_id: 'runner-unknown', status: 'failed',
        accepted_at: '2026-11-04T08:00:00Z', target_count: 1, error_code: 'UNKNOWN_FATAL', targets: [
          { conversation_id: 1, status: 'interrupted', phase: 'analyzing', reason_label: null, error_code: null },
        ] } })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('执行中断，当前会话未完成')
    expect(wrapper.text()).toContain('错误码 UNKNOWN_FATAL')
    expect(wrapper.text()).not.toContain('总结生成失败')
  })

  it('日期窗口缺失只说明已知事实，不推断插件能力', async () => {
    api.dailyReports.mockResolvedValue({ reports: [report(1, '')], has_more: false,
      next_cursor: null, last_generation: { runner_id: 'runner-codes', status: 'failed',
        accepted_at: '2026-11-04T08:00:00Z', target_count: 1, targets: [
          { conversation_id: 1, status: 'failed', phase: 'reading', reason_label: null, error_code: 'WECOM_DAY_MODE_UNAVAILABLE' },
        ] } })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('日期读取结果缺少日期窗口')
    expect(wrapper.text()).toContain('错误码 WECOM_DAY_MODE_UNAVAILABLE')
    expect(wrapper.text()).not.toContain('插件不支持按日期读取')
  })

  it('快照失稳按真实 fatal 投影从整轮码解释单目标中断，卡片与详情均可读', async () => {
    const execution = { runner_id: 'runner-snapshot', status: 'failed', accepted_at: '2026-11-04T08:00:00Z',
      target_count: 1, error_code: 'SNAPSHOT_UNSTABLE', targets: [
        { conversation_id: 1, name: '快照群', status: 'interrupted', phase: 'reading',
          reason_label: null, error_code: null, diagnostic: 'private snapshot content' },
      ] }
    api.dailyReports.mockResolvedValue({ reports: [report(1, '')], has_more: false,
      next_cursor: null, last_generation: execution })
    api.listDailyExecutions.mockResolvedValue({ executions: [execution], has_more: false, next_cursor: null })
    api.getDailyExecution.mockResolvedValue({ execution })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('会话记录快照不稳定，请稍后重试')
    expect(wrapper.text()).toContain('错误码 SNAPSHOT_UNSTABLE')
    await wrapper.findAll('button').find(button => button.text() === '查看执行记录')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '查看详情')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('快照群 · 执行中断 · 会话记录快照不稳定')
    expect(wrapper.text()).not.toContain('private snapshot content')
    expect(wrapper.text()).not.toContain('会话读取来源发生变化')
  })

  it('今日未终态执行在切历史再回来后恢复单轮询，历史页不轮询', async () => {
    const timer = vi.spyOn(globalThis, 'setInterval')
    const clear = vi.spyOn(globalThis, 'clearInterval')
    const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
    api.dailyReports.mockImplementation((date: string) => Promise.resolve({ reports: [report(1, '已保存')],
      has_more: false, next_cursor: null, last_generation: date === today ? {
        runner_id: 'queued-runner', status: 'queued', accepted_at: '2026-11-04T08:00:00Z',
        target_count: 1, targets: [],
      } : null }))
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    expect(timer.mock.calls.filter(call => call[1] === 5000)).toHaveLength(1)
    await wrapper.findAll('button').find(button => button.text() === '昨日')!.trigger('click')
    await flushPromises()
    expect(clear).toHaveBeenCalled()
    expect(timer.mock.calls.filter(call => call[1] === 5000)).toHaveLength(1)
    await wrapper.findAll('button').find(button => button.text() === '今日')!.trigger('click')
    await flushPromises()
    expect(timer.mock.calls.filter(call => call[1] === 5000)).toHaveLength(2)
  })

  it('本次 A 终态后显示后续 B 的最新执行事实', async () => {
    api.dailyReports.mockResolvedValueOnce({ reports: [report(1, '此前报告')], has_more: false,
      next_cursor: null, last_generation: null }).mockResolvedValue({ reports: [report(1, '此前报告')],
      has_more: false, next_cursor: null, last_generation: { runner_id: 'runner-b', status: 'failed',
        accepted_at: '2026-11-04T09:00:00Z', target_count: 1, targets: [{ conversation_id: 1,
          status: 'failed', phase: 'reading', reason_label: 'B 读取失败' }] } })
    api.getDailyExecution.mockResolvedValue({ execution: { runner_id: 'runner-one', status: 'completed',
      accepted_at: '2026-11-04T08:00:00Z', target_count: 1, targets: [] } })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    await wrapper.find('[data-testid="generate-daily"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('B 读取失败')
    expect(wrapper.text()).toContain('执行 runner-b')
  })

  it('没有计划时也可创建专属默认配置，不携带旧通用任务字段', async () => {
    api.listConversations.mockResolvedValue({ conversations: [conversation(1)] })
    api.saveDailyPlan.mockResolvedValue({ plan: { task_id: 'sched-one', status: 'active',
      config: { target_day: 'yesterday', scope_mode: 'all' }, next_due_at: '2026-11-05T02:00:00' } })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    const config = wrapper.findAll('button').find(button => button.text() === '定时执行配置')
    await config!.trigger('click')
    await flushPromises()
    const save = [...document.body.querySelectorAll('[role="dialog"] button')].find(button => button.textContent === '保存配置')
    expect(save).toBeDefined()
    save!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(api.saveDailyPlan).toHaveBeenCalledWith({ target_day: 'yesterday', scope_mode: 'all',
      conversation_ids: [], hour: 2, minute: 0 }, false)
  })

  it('暂停计划的配置保存仍保持暂停，恢复由独立操作触发', async () => {
    api.listConversations.mockResolvedValue({ conversations: [conversation(1)] })
    const paused = { task_id: 'sched-paused', status: 'paused', config: { target_day: 'yesterday', scope_mode: 'all' },
      cron_expression: '0 2 * * *', interval_seconds: null, next_due_at: '2026-11-05T02:00:00' }
    api.getDailyPlan.mockResolvedValue({ plan: paused })
    api.saveDailyPlan.mockResolvedValue({ plan: paused })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '定时执行配置')!.trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')!.textContent).toContain('恢复后重新计算')
    const save = [...document.body.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(button => button.textContent === '保存配置')!
    save.click()
    await flushPromises()
    expect(api.saveDailyPlan).toHaveBeenCalledWith(expect.any(Object), true)
    expect(api.resumeDailyPlan).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('计划仍暂停')
  })

  it('暂停已成功但配置重查失败时，不按旧 active 状态提示恢复成功', async () => {
    api.dailyReports.mockResolvedValue({ reports: [report(1, '已保存')], has_more: false, next_cursor: null, last_generation: null })
    api.getDailyPlan.mockResolvedValueOnce({ plan: { task_id: 'sched-one', status: 'active',
      config: { target_day: 'yesterday', scope_mode: 'all' }, next_due_at: '2026-11-05T02:00:00' } })
      .mockRejectedValueOnce(new Error('计划读取暂不可用'))
    api.pauseDailyPlan.mockResolvedValue({ status: 'paused' })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === '定时执行配置')!.trigger('click')
    await flushPromises()
    ;[...document.body.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')].find(button => button.textContent === '暂停')!.click()
    await flushPromises()
    expect(api.pauseDailyPlan).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('操作已受理，配置重查失败')
    expect(document.body.querySelector('[role="dialog"]')!.textContent).toContain('自动总结：已暂停')
  })

  it('配置抽屉约束双向 Tab，Esc 和关闭按钮均把焦点还给配置入口', async () => {
    api.dailyReports.mockResolvedValue({ reports: [report(1, '已保存')], has_more: false, next_cursor: null, last_generation: null })
    focusHost = document.createElement('div')
    document.body.append(focusHost)
    wrapper = mount(WecomConversationDaily, { attachTo: focusHost,
      global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    const trigger = wrapper.findAll('button').find(button => button.text() === '定时执行配置')!
    await trigger.trigger('click')
    await flushPromises()
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')!
    const close = dialog.querySelector<HTMLButtonElement>('button[aria-label="关闭对话框"]')!
    const save = [...dialog.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === '保存配置')!
    expect(document.activeElement).toBe(dialog)
    close.focus()
    close.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(save)
    save.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(close)
    close.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(trigger.element)

    await trigger.trigger('click')
    await flushPromises()
    document.body.querySelector<HTMLButtonElement>('[role="dialog"] button[aria-label="关闭对话框"]')!.click()
    await flushPromises()
    expect(document.activeElement).toBe(trigger.element)
  })

  it('目录跨页保留选中项，搜索切换时旧页响应不能混入新候选', async () => {
    const oldPage = deferred<unknown>()
    const identity = { device_id: 'device-a', self_user_id: 'account-a', source_generation: 'gen-a' }
    api.listConversations.mockResolvedValue({ conversations: [conversation(1)] })
    api.listDirectory.mockResolvedValueOnce({ ...identity, conversations: [
      { session: 'R:first', name: '第一群', kind: 'group', peer_platform: 'wecom' }], next_cursor: 'cursor-2' })
      .mockResolvedValueOnce({ ...identity, conversations: [
        { session: 'R:second', name: '第二群', kind: 'group', peer_platform: 'wecom' }], next_cursor: 'cursor-3' })
      .mockReturnValueOnce(oldPage.promise)
      .mockResolvedValueOnce({ ...identity, conversations: [
        { session: 'R:search', name: '搜索群', kind: 'group', peer_platform: 'wecom' }], next_cursor: null })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text().includes('添加会话'))!.trigger('click')
    await flushPromises()
    const dialog = () => document.body.querySelector('[role="dialog"]')!
    ;(dialog().querySelector('input[aria-label="选择第一群"]') as HTMLInputElement).click()
    await flushPromises()
    ;[...dialog().querySelectorAll('button')].find(button => button.textContent === '加载更多')!.click()
    await flushPromises()
    ;(dialog().querySelector('input[aria-label="选择第二群"]') as HTMLInputElement).click()
    await flushPromises()
    expect(dialog().textContent).toContain('已选择 2 个会话')

    ;[...dialog().querySelectorAll('button')].find(button => button.textContent === '加载更多')!.click()
    const input = dialog().querySelector('input[aria-label="搜索会话名称"]') as HTMLInputElement
    input.value = '搜索'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    ;[...dialog().querySelectorAll('button')].find(button => button.textContent === '搜索')!.click()
    await flushPromises()
    oldPage.resolve({ ...identity, conversations: [{ session: 'R:stale', name: '旧结果', kind: 'group', peer_platform: 'wecom' }], next_cursor: null })
    await flushPromises()
    expect(dialog().textContent).toContain('搜索群')
    expect(dialog().textContent).not.toContain('旧结果')
    expect(dialog().textContent).toContain('已选择 0 个会话')
  })

  it('同名空会话可预览后按稳定 ID 添加，不以消息是否存在决定可选', async () => {
    const identity = { device_id: 'device-a', self_user_id: 'account-a', source_generation: 'gen-a' }
    const session = 'ws1.account-a.R%3Aempty'
    api.listConversations.mockResolvedValue({ conversations: [conversation(1)] })
    api.listDirectory.mockResolvedValue({ ...identity, conversations: [
      { session, name: '同名群', kind: 'group', peer_platform: 'wecom', same_name: true, added: false }], next_cursor: null })
    api.previewDirectory.mockResolvedValue({ ...identity, messages: [] })
    api.addConversations.mockResolvedValue({ conversations: [], added_count: 1 })
    wrapper = mount(WecomConversationDaily, { global: { provide: { toggleSidebar: () => {} }, stubs: { AppHeader: true } } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text().includes('添加会话'))!.trigger('click')
    await flushPromises()
    const dialog = document.body.querySelector('[role="dialog"]')!
    ;[...dialog.querySelectorAll('button')].find(button => button.textContent === '查看最近 3 条')!.click()
    await flushPromises()
    expect(dialog.textContent).toContain('暂无消息，可按会话类型和稳定 ID 区分')
    ;(dialog.querySelector('input[aria-label="选择同名群"]') as HTMLInputElement).click()
    await flushPromises()
    ;[...dialog.querySelectorAll('button')].find(button => button.textContent === '添加会话')!.click()
    await flushPromises()
    expect(api.addConversations).toHaveBeenCalledWith([session], {
      expected_device_id: 'device-a', expected_source_generation: 'gen-a',
    })
  })
})
