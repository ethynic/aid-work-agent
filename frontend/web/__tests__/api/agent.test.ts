/**
 * SSEManager 错误处理测试
 *
 * 覆盖场景：
 * - HTTP 403 + NO_CREDIT：抛出带 code='NO_CREDIT' 的错误
 * - HTTP 403 其他原因：抛出带 code='FORBIDDEN' 的错误
 * - HTTP 其他错误：抛出通用错误
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { SSEManager } from '@/api/agent'

describe('SSEManager 错误处理', () => {
  let originalFetch: typeof globalThis.fetch

  beforeEach(() => {
    originalFetch = globalThis.fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('HTTP 403 + NO_CREDIT 应抛出带 code 标识的错误', async () => {
    // mock fetch 返回 403 + NO_CREDIT
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({
        success: false,
        error: '积分余额已耗尽',
        details: '积分余额已耗尽，无法继续对话，请联系管理员充值',
        code: 'NO_CREDIT',
      }),
    } as any)

    const sse = new SSEManager()
    let capturedError: any = null

    await sse.connect(
      '测试消息',
      'session_test',
      undefined,
      {},
      () => {},
      () => {},
      () => {},
      (err) => { capturedError = err },
    )

    expect(capturedError).not.toBeNull()
    expect(capturedError.code).toBe('NO_CREDIT')
    expect(capturedError.status).toBe(403)
    expect(capturedError.message).toContain('积分余额已耗尽')
  })

  it('HTTP 403 其他原因应抛出带 code=FORBIDDEN 的错误', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({
        success: false,
        error: '权限不足',
        code: 'FORBIDDEN',
      }),
    } as any)

    const sse = new SSEManager()
    let capturedError: any = null

    await sse.connect(
      '测试消息',
      'session_test',
      undefined,
      {},
      () => {},
      () => {},
      () => {},
      (err) => { capturedError = err },
    )

    expect(capturedError).not.toBeNull()
    expect(capturedError.code).toBe('FORBIDDEN')
    expect(capturedError.status).toBe(403)
    expect(capturedError.message).toContain('权限不足')
  })

  it('HTTP 500 应抛出通用错误（无 code）', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    } as any)

    const sse = new SSEManager()
    let capturedError: any = null

    await sse.connect(
      '测试消息',
      'session_test',
      undefined,
      {},
      () => {},
      () => {},
      () => {},
      (err) => { capturedError = err },
    )

    expect(capturedError).not.toBeNull()
    expect(capturedError.code).toBeUndefined()
    expect(capturedError.message).toContain('500')
  })

  it('工具事件应透传 displayName 与 toolCallId', async () => {
    const payload = [
      'data: {"type":"tool_start","toolName":"future_tool","toolArgs":{},"displayName":"未来工具","toolCallId":"call-1","timestamp":1}',
      'data: {"type":"tool_result","toolName":"future_tool","result":{"success":true},"success":true,"displayName":"未来工具","toolCallId":"call-1","timestamp":2}',
      '',
    ].join('\n\n')
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(payload) })
        .mockResolvedValueOnce({ done: true, value: undefined }),
    }
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: { getReader: () => reader },
    } as any)
    const onToolStart = vi.fn()
    const onToolResult = vi.fn()

    await new SSEManager().connect(
      '测试消息', 'session_test', undefined, {},
      () => {}, () => {}, () => {}, () => {},
      onToolStart, onToolResult,
    )

    expect(onToolStart).toHaveBeenCalledWith('future_tool', {}, '未来工具', 'call-1')
    expect(onToolResult).toHaveBeenCalledWith(
      'future_tool', { success: true }, true, '未来工具', 'call-1',
    )
  })
})
