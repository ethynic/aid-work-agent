/**
 * SSEManager verbose 帧解析测试（Phase 2，设计 §4/§8.2）
 *
 * 覆盖场景：
 * - verbose SSE 帧被解析为完整 VerboseMessage 并回调 onVerbose
 * - verbose 与 progress/response 隔离：不误触发 onProgress
 * - verbose 帧跨 chunk 边界时缓冲逻辑仍正确
 * - 非事件数据不误入 onVerbose
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { SSEManager } from '@/api/agent'

const VERBOSE_FRAME =
  '{"type":"verbose","eventId":"verbose_abc","data":"正在生成报价单，请耐心等待。","source":"policy","timestamp":1788144000000}'

function mockSSEFetch(chunks: string[]) {
  let call = 0
  const reader = {
    read: vi.fn().mockImplementation(() => {
      if (call < chunks.length) {
        const value = new TextEncoder().encode(chunks[call])
        call += 1
        return Promise.resolve({ done: false, value })
      }
      return Promise.resolve({ done: true, value: undefined })
    }),
  }
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    body: { getReader: () => reader },
  } as any)
}

describe('SSEManager verbose 帧解析', () => {
  let originalFetch: typeof globalThis.fetch

  beforeEach(() => {
    originalFetch = globalThis.fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('verbose 帧应解析出完整 VerboseMessage 并回调 onVerbose', async () => {
    mockSSEFetch([
      `data: ${VERBOSE_FRAME}\n\ndata: {"type":"response","data":"最终回复","timestamp":2}\n\n`,
    ])
    const onVerbose = vi.fn()
    const onResponse = vi.fn()
    const onProgress = vi.fn()

    await new SSEManager().connect(
      '测试消息', 'session_test', undefined, {},
      onProgress, onResponse, () => {}, () => {},
      undefined, undefined, undefined, undefined, undefined, undefined,
      onVerbose,
    )

    expect(onVerbose).toHaveBeenCalledTimes(1)
    expect(onVerbose).toHaveBeenCalledWith({
      eventId: 'verbose_abc',
      data: '正在生成报价单，请耐心等待。',
      source: 'policy',
      timestamp: 1788144000000,
    })
    // 隔离：verbose 不得误触发 onProgress（技术事件）
    expect(onProgress).not.toHaveBeenCalled()
    expect(onResponse).toHaveBeenCalledWith('最终回复')
  })

  it('verbose 帧跨 chunk 边界时仍能正确解析', async () => {
    const frame = `data: ${VERBOSE_FRAME}\n\n`
    const splitAt = Math.floor(frame.length / 2)
    mockSSEFetch([frame.slice(0, splitAt), frame.slice(splitAt)])
    const onVerbose = vi.fn()

    await new SSEManager().connect(
      '测试消息', 'session_test', undefined, {},
      () => {}, () => {}, () => {}, () => {},
      undefined, undefined, undefined, undefined, undefined, undefined,
      onVerbose,
    )

    expect(onVerbose).toHaveBeenCalledTimes(1)
    expect(onVerbose.mock.calls[0][0]).toMatchObject({
      eventId: 'verbose_abc',
      source: 'policy',
    })
  })

  it('未提供 onVerbose 时 verbose 帧被安全忽略', async () => {
    mockSSEFetch([`data: ${VERBOSE_FRAME}\n\n`])
    const onProgress = vi.fn()
    const onResponse = vi.fn()

    await new SSEManager().connect(
      '测试消息', 'session_test', undefined, {},
      onProgress, onResponse, () => {}, () => {},
    )

    expect(onProgress).not.toHaveBeenCalled()
    expect(onResponse).not.toHaveBeenCalled()
  })
})
