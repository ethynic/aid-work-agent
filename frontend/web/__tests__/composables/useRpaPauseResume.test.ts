/**
 * useRpaPauseResume composable 单元测试
 *
 * 验证：
 * - 成功路径：confirm 通过 → 调 pause API → onSuccess 触发
 * - 失败路径：API 500 → 不调 onSuccess，toast 报错
 * - 取消 confirm → 不发请求
 * - 防重复点击：pausing 期间再次调用立即返回 false
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// mock vue-toastification 的 useToast
const toastMock = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}
vi.mock('vue-toastification', () => ({
  useToast: () => toastMock,
}))

// mock confirm
const confirmMock = vi.fn()
Object.defineProperty(globalThis, 'confirm', {
  value: confirmMock,
  writable: true,
})

import { useRpaPauseResume } from '@/composables/useRpaPauseResume'

describe('useRpaPauseResume', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    confirmMock.mockReturnValue(true)
  })

  it('pauseAccount 成功路径：confirm 通过 → 调 pause API → onSuccess 触发 → 返回 true', async () => {
    const onSuccess = vi.fn()
    const { pauseAccount, pausing } = useRpaPauseResume({ onSuccess })

    const ok = await pauseAccount('acc_001', '离职')

    expect(ok).toBe(true)
    expect(confirmMock).toHaveBeenCalled()
    expect(toastMock.success).toHaveBeenCalledWith('已暂停')
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(pausing.value).toBe(false)
  })

  it('pauseAccount confirm 取消时不发请求、不触发 onSuccess', async () => {
    confirmMock.mockReturnValue(false)
    const onSuccess = vi.fn()
    const { pauseAccount } = useRpaPauseResume({ onSuccess })

    const ok = await pauseAccount('acc_001')

    expect(ok).toBe(false)
    expect(onSuccess).not.toHaveBeenCalled()
    expect(toastMock.success).not.toHaveBeenCalled()
  })

  it('pauseAccount API 失败时 toast 报错、不触发 onSuccess', async () => {
    const { pauseAccount } = useRpaPauseResume()
    // MSW 默认 handler 会成功，这里用 fetch mock 覆盖返回 500
    const realFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: { success: false, message: '服务端错误' } }),
    }) as any

    try {
      const ok = await pauseAccount('acc_001')
      expect(ok).toBe(false)
      expect(toastMock.error).toHaveBeenCalledWith('服务端错误')
    } finally {
      globalThis.fetch = realFetch
    }
  })

  it('resumeAccount 成功路径', async () => {
    const onSuccess = vi.fn()
    const { resumeAccount, resuming } = useRpaPauseResume({ onSuccess })

    const ok = await resumeAccount('acc_001')

    expect(ok).toBe(true)
    expect(toastMock.success).toHaveBeenCalledWith('已恢复')
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(resuming.value).toBe(false)
  })

  it('pauseConversation 成功路径', async () => {
    const onSuccess = vi.fn()
    const { pauseConversation } = useRpaPauseResume({ onSuccess })

    const ok = await pauseConversation('rpa_bind_xxx')

    expect(ok).toBe(true)
    expect(toastMock.success).toHaveBeenCalledWith('已暂停')
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  it('resumeConversation 成功路径', async () => {
    const { resumeConversation } = useRpaPauseResume()
    const ok = await resumeConversation('rpa_bind_xxx')
    expect(ok).toBe(true)
    expect(toastMock.success).toHaveBeenCalledWith('已恢复')
  })

  it('pausing/resuming 期间再次调用立即返回 false（防重复点击）', async () => {
    const { pauseAccount, pausing } = useRpaPauseResume()
    // 让 fetch 慢一点，确保 pausing=true 期间发起第二次调用
    const realFetch = globalThis.fetch
    let resolveSlow: () => void
    const slowPromise = new Promise((resolve) => { resolveSlow = resolve as () => void })
    globalThis.fetch = vi.fn().mockImplementation(() => slowPromise.then(() => ({
      ok: true,
      status: 200,
      json: async () => ({ success: true, data: { scope: 'account', action: 'pause', affected: ['acc_001'] } }),
    }))) as any

    try {
      const p1 = pauseAccount('acc_001')
      // 等 microtask 让 pausing 置 true
      await Promise.resolve()
      expect(pausing.value).toBe(true)
      const ok2 = await pauseAccount('acc_002')
      expect(ok2).toBe(false)  // 被拒
      resolveSlow!()
      const ok1 = await p1
      expect(ok1).toBe(true)
    } finally {
      globalThis.fetch = realFetch
    }
  })
})
