import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listDevices } from '@/api/localTools'
import { getFriendBatch, listFriendBatches, listGroups, previewFriends, startFriendBatch, type FriendBatch } from '@/api/weixinAgent'
import WeixinAgent from '@/components/weixinConversation/WeixinAgent.vue'

vi.mock('@/api/localTools', () => ({ listDevices: vi.fn() }))
vi.mock('@/api/weixinAgent', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/weixinAgent')>(),
  listGroups: vi.fn(), previewFriends: vi.fn(), startFriendBatch: vi.fn(), getFriendBatch: vi.fn(), listFriendBatches: vi.fn(),
}))
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ admin: null, isLoggedIn: true, logout: vi.fn() }),
}))

const filename = '联系清单.csv'
const result: FriendBatch = {
  id: 'batch-a', filename, created_at: '2026-11-26T02:00:00Z', state: 'completed', total: 5,
  counts: { request_sent: 1, skipped: 2, unknown: 1, failed: 1 },
  contacts: [
    { name: '小周', identifier: 'wx_test_a', status: 'request_sent', reason: null, submitted_at: '2026-11-26T02:01:00Z' },
    { name: '小陈', identifier: 'wx_test_b', status: 'unknown', reason: 'EXECUTION_UNKNOWN', submitted_at: null },
    { name: '小余', identifier: 'wx_test_config', status: 'failed', reason: 'CONFIG_MISSING', submitted_at: null },
  ],
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(listDevices).mockResolvedValue([{
    device_id: 'device-1', name: '测试电脑', platform: 'windows', runtime_version: '1.3.1',
    selected: true, status: 'active', online: true, last_seen_at: null, created_at: null,
  }])
  vi.mocked(listGroups).mockResolvedValue([{ id: 'group-a', name: '项目群', deleted_at: null }])
  vi.mocked(previewFriends).mockResolvedValue({
    filename,
    contacts: [
      { name: '小周', identifier: 'wx_test_a', verify_message: '本条', selected: true, exclusion: null },
      { name: '小陈', identifier: 'wx_test_b', verify_message: '', selected: true, exclusion: null },
      { name: '重复', identifier: 'wx_test_a', verify_message: '', selected: true, exclusion: 'DUPLICATE' },
      { name: '未填', identifier: '', verify_message: '', selected: true, exclusion: 'MISSING_IDENTIFIER' },
    ],
  })
  vi.mocked(listFriendBatches).mockResolvedValue({ batches: [result], has_more: false })
})

async function mountPage() {
  const emptyPage = { template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/t/:tenant_id/weixin-agent', component: emptyPage },
      { path: '/t/:tenant_id/local-tools', name: 'tenant-local-tools', component: emptyPage },
    ],
  })
  await router.push('/t/test/weixin-agent')
  const wrapper = mount(WeixinAgent, {
    global: { plugins: [router], stubs: { AppHeader: true }, provide: { toggleSidebar: vi.fn() } },
  })
  await flushPromises()
  const button = (text: string) => wrapper.findAll('button').find(button => button.text() === text)!
  return { wrapper, button }
}

describe('微信 Agent 批量加好友', () => {
  it('keeps the shared device and exclusions intact across workspace switches and submits selected contacts only once', async () => {
    let complete: (batch: FriendBatch) => void = () => undefined
    vi.mocked(startFriendBatch).mockReturnValue(new Promise(resolve => { complete = resolve }))
    const { wrapper, button } = await mountPage()
    try {
      expect(wrapper.find('h1').exists()).toBe(false)
      expect(button('群聊总结').attributes('aria-pressed')).toBe('true')
      expect(wrapper.text()).toContain('测试电脑 · 在线')
      await button('批量加好友').trigger('click')
      const workspace = wrapper.get('#weixin-workspace-friends')
      const input = workspace.get('input[type=file]')
      const file = new File(['演示'], filename, { type: 'text/csv' })
      Object.defineProperty(input.element, 'files', { value: [file] })
      await input.trigger('change')
      await flushPromises()
      expect(previewFriends).toHaveBeenCalledWith(file)
      expect(workspace.text()).toContain('已自动排除 2 条')
      expect(workspace.text()).toContain('清单内重复 1 条')
      expect(workspace.text()).toContain('缺少手机号或微信号 1 条')
      const checks = workspace.findAll('input[type=checkbox]')
      expect(checks.map(check => (check.element as HTMLInputElement).checked)).toEqual([true, true])
      await checks[1]!.setValue(false)
      await button('群聊总结').trigger('click')
      expect(wrapper.get('#weixin-workspace-groups').isVisible()).toBe(true)
      await button('批量加好友').trigger('click')
      expect(button('提交 1 条好友申请').exists()).toBe(true)
      await workspace.get('#weixin-friend-verification').setValue('批次验证语')
      const form = workspace.get('form')
      await form.trigger('submit')
      await form.trigger('submit')
      expect(startFriendBatch).toHaveBeenCalledTimes(1)
      expect(vi.mocked(startFriendBatch).mock.calls[0]![0]).toMatchObject({
        device_id: 'device-1', filename, verify_message: '批次验证语', contacts: [
          { identifier: 'wx_test_a', selected: true, verify_message: '本条' },
          { identifier: 'wx_test_b', selected: false },
          { identifier: 'wx_test_a', selected: false },
          { identifier: '', selected: false },
        ],
      })
      complete(result)
      await flushPromises()
      expect(workspace.text()).toContain('已提交 1 条 · 待核对 1 条')
      await workspace.get('select').setValue('request_sent')
      const results = workspace.findAll('table').find(table => table.text().includes('执行结果'))!
      expect(results.text()).not.toContain('小陈')
      expect(results.text()).toContain('2026/11/26 10:01:00')
    } finally {
      wrapper.unmount()
    }
  })

  it('reopens persisted application records after mounting a fresh page', async () => {
    vi.mocked(getFriendBatch).mockResolvedValue(result)
    const { wrapper, button } = await mountPage()
    try {
      await button('批量加好友').trigger('click')
      await button('申请记录').trigger('click')
      await flushPromises()
      await button('查看详情').trigger('click')
      await flushPromises()
      expect(getFriendBatch).toHaveBeenCalledWith(result.id, expect.any(AbortSignal))
      expect(wrapper.text()).toContain(filename)
      expect(wrapper.text()).toContain('请在微信中核对申请状态')
      expect(wrapper.text()).toContain('未提交 1 条')
      expect(wrapper.text()).toContain('微信视觉服务配置不可用')
    } finally {
      wrapper.unmount()
    }
  })

  it('prevents submitting the old list if importing its replacement fails', async () => {
    const { wrapper, button } = await mountPage()
    try {
      await button('批量加好友').trigger('click')
      const workspace = wrapper.get('#weixin-workspace-friends')
      const input = workspace.get('input[type=file]')
      const upload = async (name: string) => {
        const file = new File(['演示'], name, { type: 'text/csv' })
        Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
        await input.trigger('change')
        await flushPromises()
      }

      await upload(filename)
      expect(button('提交 2 条好友申请')).toBeDefined()
      await workspace.get('#weixin-friend-verification').setValue('原清单的验证语')

      vi.mocked(previewFriends).mockRejectedValueOnce(new Error('清单缺少「手机号或微信号」列。'))
      await upload('表头错误.csv')
      expect(workspace.get('[role="alert"]').text()).toContain('缺少「手机号或微信号」列')
      expect(workspace.text()).not.toContain(filename)
      expect(workspace.find('form').exists()).toBe(false)
      expect(startFriendBatch).not.toHaveBeenCalled()

      await upload(filename)
      expect(workspace.get<HTMLInputElement>('#weixin-friend-verification').element.value).toBe('')
    } finally {
      wrapper.unmount()
    }
  })
})
