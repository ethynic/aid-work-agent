import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { listDevices } from '@/api/localTools'
import * as api from '@/api/weixinAgent'
import WeixinAgent from '@/components/weixinConversation/WeixinAgent.vue'

vi.mock('@/api/localTools', () => ({ listDevices: vi.fn(), createWeixinSummary: vi.fn(), getWeixinSummary: vi.fn() }))
vi.mock('@/api/weixinAgent', async (importOriginal) => {
  const actual = await importOriginal<typeof api>()
  return { ...actual, listGroups: vi.fn(), listBatches: vi.fn(), getBatch: vi.fn(), startBatch: vi.fn(), retryGroup: vi.fn(),
    listSchedules: vi.fn(), listRobotRules: vi.fn(), saveSchedule: vi.fn(), setSchedulePaused: vi.fn() }
})
vi.mock('@/composables/useTenantAuth', () => ({ useTenantAuth: () => ({ admin: null, isLoggedIn: true, logout: vi.fn() }) }))

const groups = [
  { id: 'g-1', name: '合成产品群', deleted_at: null },
  { id: 'g-2', name: '合成实施群', deleted_at: null },
]
const batch: api.Batch = {
  id: 'batch-1', device_id: 'win-a', source: 'manual', title: null, retry_batch_id: null,
  start_at: '2026-11-22T03:00:00Z', end_at: '2026-11-23T03:00:00Z', created_at: '2026-11-23T03:00:00Z',
  state: 'partial', groups: [
    { group_id: 'g-1', name: '合成产品群', state: 'succeeded', summary: '确认进度与交付日期', message_count: 2 },
    { group_id: 'g-2', name: '合成实施群', state: 'failed', error_code: 'TARGET_AMBIGUOUS' },
  ],
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(listDevices).mockResolvedValue([{
    device_id: 'win-a', name: '工作电脑', platform: 'windows', runtime_version: null,
    selected: true, status: 'active', online: true, last_seen_at: null, created_at: null,
  }])
  vi.mocked(api.listGroups).mockResolvedValue(groups)
  vi.mocked(api.getBatch).mockResolvedValue(batch)
  vi.mocked(api.startBatch).mockResolvedValue(batch)
  vi.mocked(api.listBatches).mockResolvedValue({ batches: [batch], has_more: false })
  vi.mocked(api.listSchedules).mockResolvedValue([])
  vi.mocked(api.listRobotRules).mockResolvedValue([{ rule_id: 'test-rule', name: '项目通知', channels: ['wecom_bot'] }])
})

async function setup(query = '') {
  const page = { template: '<div />' }
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/t/:tenant_id/weixin-agent', component: page },
    { path: '/t/:tenant_id/local-tools', name: 'tenant-local-tools', component: page },
    { path: '/t/:tenant_id/notification-rules', name: 'tenant-notification-rules', component: page },
  ] })
  await router.push('/t/test/weixin-agent' + query)
  const wrapper = mount(WeixinAgent, { attachTo: document.body, global: { plugins: [router], stubs: { AppHeader: true, Teleport: true }, provide: { toggleSidebar: vi.fn() } } })
  await flushPromises()
  const click = async (label: string) => {
    await wrapper.findAll('button').find(button => button.text() === label)!.trigger('click')
    await flushPromises()
  }
  return { wrapper, router, click }
}

it('submits both selected groups with one Generate action and preserves the actual partial result', async () => {
  const { wrapper, router, click } = await setup()
  try {
    expect(wrapper.text()).not.toContain('同意')
    expect(wrapper.get('button[type=submit]').attributes('disabled')).toBeDefined()
    await wrapper.get('input[value="g-1"]').setValue(true)
    await wrapper.get('input[value="g-2"]').setValue(true)
    await click('最近 24 小时')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.startBatch).toHaveBeenCalledExactlyOnceWith({ device_id: 'win-a', group_ids: ['g-1', 'g-2'], preset: '24h' })
    expect(router.currentRoute.value.query.batch).toBe('batch-1')
    expect(wrapper.text()).toContain('确认进度与交付日期')
    expect(wrapper.text()).toContain('无法唯一确认同名目标')
  } finally { wrapper.unmount() }
})

it('filters only batches, leaves both original groups visible and retries only the failed group', async () => {
  const retry = { ...batch, id: 'retry-1', retry_batch_id: batch.id, groups: [batch.groups[1]!] }
  vi.mocked(api.retryGroup).mockResolvedValue(retry)
  vi.mocked(api.getBatch).mockImplementation(async id => id === retry.id ? retry : batch)
  const { wrapper, router, click } = await setup('?tab=history')
  try {
    expect(wrapper.find('#weixin-batch-title').exists()).toBe(false)
    await wrapper.get('select[aria-label="筛选群聊"]').setValue('g-2')
    await flushPromises()
    expect(api.listBatches).toHaveBeenLastCalledWith('g-2', 0)
    await wrapper.get('#weixin-history-batch-1').trigger('click')
    await flushPromises()
    expect(wrapper.find('select[aria-label="筛选群聊"]').exists()).toBe(false)
    expect(document.activeElement?.id).toBe('weixin-batch-title')
    expect(wrapper.text()).toContain('确认进度与交付日期')
    await click('返回总结历史')
    expect(wrapper.find('#weixin-batch-title').exists()).toBe(false)
    expect(wrapper.get('select[aria-label="筛选群聊"]').element).toHaveProperty('value', 'g-2')
    expect(document.activeElement?.id).toBe('weixin-history-batch-1')
    await wrapper.get('#weixin-history-batch-1').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('button').filter(button => button.text() === '仅重试此群')).toHaveLength(1)
    await click('仅重试此群')
    expect(api.retryGroup).toHaveBeenCalledExactlyOnceWith('batch-1', 'g-2')
    expect(router.currentRoute.value.query.batch).toBe('retry-1')
    expect(wrapper.text()).toContain('查看原批次')
  } finally { wrapper.unmount() }
})

it('opens a direct batch link at the detail with no history rows above it, and shows skipped notification and uncertain coverage', async () => {
  vi.mocked(api.getBatch).mockResolvedValue({
    ...batch, notification_status: 'skipped', groups: [{
      ...batch.groups[0]!, partial: true,
      summary: [
        '# 群聊总结', '', '## 关键讨论', '', '- **项目进展**已确认', '', '## 待办事项', '',
        '| 事项 | 负责人 |', '| --- | --- |', '| 确认交付日期 | 项目组 |', '', '![附件](/preview.png)',
      ].join('\n'),
    }],
  })
  const { wrapper, router, click } = await setup('?tab=history&batch=batch-1')
  try {
    expect(wrapper.find('#weixin-history-batch-1').exists()).toBe(false)
    expect(document.activeElement?.id).toBe('weixin-batch-title')
    const groupHeader = wrapper.get('header h3')
    expect(groupHeader.text()).toBe('合成产品群')
    expect(wrapper.get('.weixin-summary-markdown h1').text()).toBe('群聊总结')
    expect(wrapper.get('.weixin-summary-markdown h2').text()).toBe('关键讨论')
    expect(wrapper.get('.weixin-summary-markdown li strong').text()).toBe('项目进展')
    expect(wrapper.get('.weixin-summary-markdown table tbody td').text()).toBe('确认交付日期')
    expect(wrapper.find('.weixin-summary-markdown img').exists()).toBe(false)
    expect(wrapper.text()).toContain('通知按规则跳过')
    expect(wrapper.text()).toContain('实际覆盖起止时间无法确认')
    await click('返回总结历史')
    expect(router.currentRoute.value.query).toEqual({ tab: 'history' })
    expect(wrapper.get('#weixin-history-batch-1').attributes('aria-current')).toBe('true')
  } finally { wrapper.unmount() }
})

it('defaults schedules to no notifications and can distinctly select an existing robot rule', async () => {
  const { wrapper, click } = await setup()
  try {
    await click('定时总结')
    await click('新建定时任务')
    await wrapper.get('#weixin-schedule-title').setValue('周度跟踪')
    await wrapper.get('form input[value="g-1"]').setValue(true)
    expect(wrapper.text()).not.toContain('默认通知配置')
    await wrapper.get('#weixin-schedule-frequency').setValue('weekly')
    await wrapper.get('#weixin-schedule-day').setValue('1')
    const labels = wrapper.findAll('form label')
    await labels.find(label => label.text() === '发送执行结果通知')!.get('input').setValue(true)
    await wrapper.get('input[value="robot"]').setValue(true)
    await flushPromises()
    await wrapper.get('#weixin-notification-rule').setValue('test-rule')
    vi.mocked(api.saveSchedule).mockImplementation(async body => ({ ...body, id: 'schedule-1', next_due_at: '2026-11-24T10:00:00Z', paused: false }))
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.saveSchedule).toHaveBeenCalledExactlyOnceWith({
      title: '周度跟踪', device_id: 'win-a', group_ids: ['g-1'], preset: 'today', frequency: 'weekly',
      weekday: 1, hour: 18, minute: 0, notify: true, notification_rule_id: 'test-rule',
    }, undefined)
    expect(wrapper.text()).toContain('群机器人通知')
  } finally { wrapper.unmount() }
})
