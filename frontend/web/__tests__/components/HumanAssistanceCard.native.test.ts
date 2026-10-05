/** Native actions refresh the original Runner; legacy BAC events remain a separate path. */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import HumanAssistanceCard from '@/components/browser/HumanAssistanceCard.vue'
import type { BrowserHumanAssistance } from '@/types'
import { controlledRunnerFetch } from '../mocks/runnerFetch'

const current = vi.hoisted(() => ({ refresh: vi.fn() }))
vi.mock('@/composables/useAgent', () => ({ useAgent: () => ({ refreshRunner: current.refresh }) }))

const assistance = (state: BrowserHumanAssistance['state'] = 'controlling'): BrowserHumanAssistance => ({
  assistance_id: 'native-wait-A', run_id: 'native-run-A', continuation_id: 'fictional-bac-A',
  reason_code: 'PAGE_VERIFICATION', surface: 'server_web', title: '请完成页面验证',
  steps: ['在原页面完成操作', '完成后继续原任务'], completion_mode: 'confirm_only',
  completion_status: 'waiting', expires_at: '2099-01-01T00:00:00Z', state, view_available: true,
})

describe('Native assistance uses original Runner presentation', () => {
  let network: ReturnType<typeof controlledRunnerFetch>
  let wrappers: VueWrapper[]
  const props = () => ({ runnerId: 'runner-A', assistance: assistance(),
    authHeaders: { Authorization: 'Bearer fictional-A' } })
  beforeEach(() => {
    vi.useFakeTimers(); wrappers = []; current.refresh.mockReset().mockResolvedValue(undefined)
    network = controlledRunnerFetch(); vi.stubGlobal('fetch', network.fetch)
  })
  afterEach(async () => {
    for (const wrapper of wrappers) wrapper.unmount()
    network.detachOutstanding(); await flushPromises(); vi.unstubAllGlobals(); vi.useRealTimers()
  })
  const button = (wrapper: VueWrapper, label: string) => wrapper.findAll('button').find(item => item.text() === label)!

  it('native complete refreshes once without synthetic messages or legacy continuation polling', async () => {
    const wrapper = mount(HumanAssistanceCard, { props: props(), global: { stubs: { BrowserView: true } } })
    wrappers.push(wrapper)
    await button(wrapper, '完成并继续').trigger('click')
    network.pending[0].respond({ success: true, state: 'resume_queued', continuation_id: 'fictional-bac-A' })
    await flushPromises(); await vi.advanceTimersByTimeAsync(2500)
    expect(current.refresh).toHaveBeenCalledExactlyOnceWith('runner-A', props().authHeaders)
    expect(wrapper.emitted('continuation')).toBeUndefined()
    expect(wrapper.emitted('updated')).toBeUndefined()
    expect(network.requests).toHaveLength(1)
    expect(network.requests[0].url).toContain('/assistance/native-wait-A/complete')
  })

  it('a late action belonging to an old wait or subject cannot refresh or overwrite a new card', async () => {
    const wrapper = mount(HumanAssistanceCard, { props: props(), global: { stubs: { BrowserView: true } } })
    wrappers.push(wrapper)
    await button(wrapper, '完成并继续').trigger('click')
    const old = network.pending[0]
    await wrapper.setProps({ runnerId: 'runner-B', assistance: { ...assistance('pending'), assistance_id: 'native-wait-B' },
      authHeaders: { Authorization: 'Bearer fictional-B' } })
    old.respond({ success: true, state: 'resume_queued', continuation_id: 'fictional-bac-A' }); await flushPromises()
    expect(current.refresh).not.toHaveBeenCalled()
    expect(wrapper.emitted('continuation')).toBeUndefined()
    expect(wrapper.emitted('updated')).toBeUndefined()
    expect(wrapper.text()).toContain('开始接管')
  })

  it('unknown native cancellation requests a refresh but never fabricates cancelled terminal state', async () => {
    const wrapper = mount(HumanAssistanceCard, { props: props(), global: { stubs: { BrowserView: true } } })
    wrappers.push(wrapper)
    await button(wrapper, '取消任务').trigger('click')
    network.pending[0].respond({ success: true }); await flushPromises()
    expect(current.refresh).toHaveBeenCalledExactlyOnceWith('runner-A', props().authHeaders)
    expect(wrapper.emitted('updated')).toBeUndefined()
    expect(wrapper.text()).toContain('取消已请求，正在核对原任务状态。')
    await wrapper.setProps({ assistance: { ...assistance(), completion_status: 'verification_required', view_available: false } })
    expect(wrapper.text()).toContain('浏览器任务需要核对')
    expect(button(wrapper, '完成并继续').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '取消任务').attributes('disabled')).toBeUndefined()
  })
})
