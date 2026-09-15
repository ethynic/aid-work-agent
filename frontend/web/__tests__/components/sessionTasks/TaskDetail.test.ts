import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }), useRoute: () => ({ fullPath: '/t/acme/weixin-marketing/session-tasks/task-1', params: { taskId: 'task-1', tenant_id: 'acme' } }), onBeforeRouteLeave: vi.fn() }))
vi.mock('@/api/auth', () => ({ getAuthHeader: () => ({ 'X-Tenant-Id': 'acme' }) }))
import TaskDetail from '@/components/sessionTasks/TaskDetail.vue'
const spec = { goal: '确认需求', opening_text: null, completion_rule: { mode: 'rounds', rounds_target: 3 }, reply_policy: { style: '简洁', allowed_facts: [], forbidden_commitments: [] }, limits: { max_replies: 5, max_decisions: 10, max_cost_units: 100, expires_at: '2099-01-01T00:00:00Z', peer_wait_timeout_seconds: 86400 }, work_window: null }
const task = (status = 'draft') => ({ id: 'task-1', status, version: 7, input_version: 9, device_id: 'device-1', account_binding_id: 'account-1', conversation_binding_id: 'binding-1', draft_spec: status === 'draft' ? spec : null, spec, phase: 'waiting_peer' })
const response = (data: unknown) => ({ ok: true, status: 200, json: async () => ({ success: true, data }) }) as Response
function setup(status = 'draft', enabled = true, special?: (url: string, init?: RequestInit) => Promise<Response> | undefined) {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    const path = String(url); const custom = special?.(path, init); if (custom) return custom
    if (path.endsWith('/capabilities')) return response({ publish_enabled: enabled })
    if (path.includes('/timeline?')) return response({ batches: [], decisions: [], executions: [], messages: [] })
    if (path.endsWith('/confirm')) return response({ confirmation_id: 'confirmation-1' })
    if (init?.method === 'POST') return response({ task_id: 'task-1' })
    return response(task(status))
  })
  return { fetchMock, wrapper: mount(TaskDetail, { global: { stubs: { teleport: true, RouterLink: true } } }) }
}
describe('会话任务发布与控制行为', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })
  it('真实门禁关闭时禁止发布与运行控制', async () => {
    const { wrapper, fetchMock } = setup('draft', false); await flushPromises()
    const publish = wrapper.findAll('button').find(b => b.text() === '查看发布确认')!
    expect(publish.attributes('disabled')).toBeDefined()
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
    wrapper.unmount()
  })
  it('点击一次完成确认及发布，双击不会重复，携带冻结版本和幂等键', async () => {
    let release!: (value: Response) => void
    const { wrapper, fetchMock } = setup('draft', true, url => url.endsWith('/confirm') ? new Promise(resolve => { release = resolve }) : undefined)
    await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '查看发布确认')!.trigger('click')
    const button = wrapper.findAll('button').find(b => b.text() === '确认范围并发布')!
    await button.trigger('click'); await button.trigger('click')
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/confirm'))).toHaveLength(1)
    release(response({ confirmation_id: 'confirmation-1' })); await flushPromises()
    const writes = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/publish'))
    expect(writes).toHaveLength(1)
    expect(JSON.parse(String(writes[0][1]?.body))).toEqual({ expected_version: 7, confirmation_id: 'confirmation-1' })
    expect((writes[0][1]?.headers as Record<string, string>)['Idempotency-Key']).toBeTruthy()
    wrapper.unmount()
  })
  it('恢复要求明确水位并发送当前控制版本和输入版本', async () => {
    const { wrapper, fetchMock } = setup('paused'); await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === '恢复')!.trigger('click')
    let button = wrapper.findAll('button').find(b => b.text() === '确认恢复')!
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.findAll('select').find(select => select.find('option[value="fresh_baseline"]').exists())!.setValue('fresh_baseline'); await flushPromises(); button = wrapper.findAll('button').find(b => b.text() === '确认恢复')!; expect(button.attributes('disabled')).toBeUndefined(); await button.trigger('click'); await flushPromises()
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/resume'))!
    expect(JSON.parse(String(call[1]?.body))).toEqual({ expected_version: 7, resume_from: { mode: 'fresh_baseline', expected_input_version: 9 } })
    wrapper.unmount()
  })
  it('卸载后迟到响应不会重启轮询', async () => {
    let release!: (value: Response) => void
    const { wrapper, fetchMock } = setup('active', true, url => url.endsWith('/task-1') ? new Promise(resolve => { release = resolve }) : undefined)
    wrapper.unmount(); release(response(task('active'))); await flushPromises()
    const count = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchMock).toHaveBeenCalledTimes(count)
  })
  it('版本冲突不自动重新确认或发布，保留用户核对入口', async () => {
    const { wrapper, fetchMock } = setup('draft', true, url => url.endsWith('/confirm') ? Promise.resolve({ ok: false, status: 409, json: async () => ({ success: false, error: '版本已改变', code: 'CONFLICT' }) } as Response) : undefined)
    await flushPromises(); await wrapper.findAll('button').find(b => b.text() === '查看发布确认')!.trigger('click')
    await wrapper.findAll('button').find(b => b.text() === '确认范围并发布')!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('刷新核对最新版本')
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/publish'))).toHaveLength(0)
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/confirm'))).toHaveLength(1)
    wrapper.unmount()
  })
})
