/**
 * RunDetail 轮询终止测试（P3-A2，R55）
 *
 * - 非终态 2 秒轮询，进入终态（succeeded）后停止：不再发起新请求；
 * - 网络错误指数退避（2s→4s），恢复成功后退避间隔复位；
 * - 组件卸载后停止轮询（无新的 fetch）。
 *
 * API 层走真实 weixinMarketing.ts（fetch mock），覆盖 getRunDetail 解析。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useRoute: () => ({ path: '/t/acme/weixin-marketing/runs/44444444-4444-4444-4444-444444444444', params: { runId: '44444444-4444-4444-4444-444444444444' } }),
}))

vi.mock('vue-toastification', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}))

import RunDetail from '@/components/weixinMarketing/RunDetail.vue'

const RUN_ID = '44444444-4444-4444-4444-444444444444'

function runDetailData(state: string) {
  return {
    run: {
      id: RUN_ID,
      occurrence_id: '55555555-5555-5555-5555-555555555555',
      task_ref: '11111111-1111-1111-1111-111111111111',
      state,
      created_at: '2026-09-08T01:00:00Z',
      finished_at: state === 'running' ? null : '2026-09-08T01:00:30Z',
    },
    deliveries: [
      {
        id: '66666666-6666-6666-6666-666666666666',
        position: 0,
        state: state === 'succeeded' ? 'succeeded' : 'dispatched',
        effect: state === 'succeeded' ? 'applied' : null,
        phase: state === 'succeeded' ? 'verified' : null,
        latest_attempt: {
          attempt_id: '77777777-7777-7777-7777-777777777777',
          attempt_no: 1,
          effect: 'applied',
          phase: 'verified',
          safe_to_retry: false,
          evidence_ref: 'weixin-evidence:abc',
        },
      },
    ],
  }
}

function okResponse(data: unknown) {
  return { ok: true, status: 200, json: async () => ({ success: true, data }) } as unknown as Response
}

function mountRunDetail() {
  return mount(RunDetail, { global: { stubs: { teleport: true } } })
}

describe('RunDetail 轮询终止', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('进入终态后停止轮询（2s 间隔，无后续请求）', async () => {
    const states = ['running', 'succeeded']
    let call = 0
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      const state = states[Math.min(call, states.length - 1)]
      call++
      return okResponse(runDetailData(state))
    })

    const wrapper = mountRunDetail()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('执行中')

    // 第一次轮询（+2s）取回 succeeded → 停止
    await vi.advanceTimersByTimeAsync(2100)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('已成功')

    // 终态后不再轮询
    await vi.advanceTimersByTimeAsync(10000)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('网络错误退避（2s→4s）且成功后复位，卸载后停止', async () => {
    // 序列：初始 load 成功(running) → 轮询网络失败 → 轮询成功(running) → 轮询成功(succeeded)
    const outcomes: Array<{ fail: boolean; state?: string }> = [
      { fail: false, state: 'running' },
      { fail: true },
      { fail: false, state: 'running' },
      { fail: false, state: 'succeeded' },
    ]
    let call = 0
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      const outcome = outcomes[Math.min(call, outcomes.length - 1)]
      call++
      if (outcome.fail) throw new TypeError('network down')
      return okResponse(runDetailData(outcome.state!))
    })

    const wrapper = mountRunDetail()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // +2s：轮询网络失败 → 退避翻倍为 4s，出现降级提示
    await vi.advanceTimersByTimeAsync(2100)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('网络异常')

    // +2s：仍在退避窗口（4s 未到），不发请求
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    // 再 +2s（累计 4s）：恢复成功，退避复位 2s
    await vi.advanceTimersByTimeAsync(2100)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(3)

    // +2s：取回 succeeded → 停止轮询
    await vi.advanceTimersByTimeAsync(2100)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(4)
    expect(wrapper.text()).toContain('已成功')

    // 卸载后无任何新请求
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(10000)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('V-P1-2 卸载于在途轮询：AbortError 回落后不重排定时器（30s 后计数不增）', async () => {
    let firstCallDone = false
    let rejectInflight: (() => void) | null = null
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      if (firstCallDone) {
        // 第二次（轮询）挂起为受控延迟：模拟真实 fetch 被 abort 时以 AbortError 落回
        return new Promise((_resolve, reject) => {
          rejectInflight = () => reject(new DOMException('The operation was aborted.', 'AbortError'))
        })
      }
      firstCallDone = true
      return okResponse(runDetailData('running'))
    })

    const wrapper = mountRunDetail()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // +2s：触发一次轮询，使其处于在途状态
    await vi.advanceTimersByTimeAsync(2100)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(rejectInflight).toBeTruthy()

    // 卸载（abort 在途请求）→ 在途 promise 以 AbortError 落回
    wrapper.unmount()
    rejectInflight!()
    await flushPromises()

    // 30s 内不再有任何新请求（修复前会重排定时器复活轮询）
    await vi.advanceTimersByTimeAsync(30000)
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('V-P2-4 连续网络错误退避 30 秒封顶（不继续翻倍）', async () => {
    let call = 0
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      call++
      if (call === 1) return okResponse(runDetailData('running'))
      throw new TypeError('network down')
    })

    const wrapper = mountRunDetail()
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // 失败重试间隔：2s → 4s → 8s → 16s → 32s(封顶 30s) → 30s → 30s ...
    await vi.advanceTimersByTimeAsync(29000) // t=29s：polls at 2/6/14 → 4 calls
    expect(fetchMock).toHaveBeenCalledTimes(4)
    await vi.advanceTimersByTimeAsync(1500) // t=30.5s：第 5 次失败（间隔 16s）
    expect(fetchMock).toHaveBeenCalledTimes(5)
    await vi.advanceTimersByTimeAsync(29000) // t=59.5s：第 6 次未到（封顶 30s，t=60 才发）
    expect(fetchMock).toHaveBeenCalledTimes(5)
    await vi.advanceTimersByTimeAsync(1000) // t=60.5s：第 6 次失败
    expect(fetchMock).toHaveBeenCalledTimes(6)
    await vi.advanceTimersByTimeAsync(29000) // t=89.5s（避免 90s 边界误触发）：仍 6 次
    expect(fetchMock).toHaveBeenCalledTimes(6)
    await vi.advanceTimersByTimeAsync(1500) // t=91s：第 7 次（间隔仍 30s，未继续翻倍到 32/60）
    expect(fetchMock).toHaveBeenCalledTimes(7)
    // 封顶值在 UI 可见
    expect(wrapper.text()).toContain('退避至 30 秒')
  })
})
