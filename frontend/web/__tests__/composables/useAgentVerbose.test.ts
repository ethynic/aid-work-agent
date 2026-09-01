/**
 * useAgent live verbose 状态单元测试（Phase 2，设计 §8.2）
 *
 * 业务背景：后端每轮最多产生一条用户可见中间消息（verbose），前端按 session
 * 保存该 live 状态用于占位文案替换：
 * - 同 eventId 重复帧被覆盖去重（后端保证每轮一条，前端去重是防御）
 * - response / error / complete 后 live verbose 必须清除（response 后隐藏）
 * - 多会话 A/B 完全隔离：后台会话收到的 verbose 不得串到当前查看会话
 * - 「10 秒 mock 工具约 8 秒提示随后 final」场景用 fake timers 表达时间线
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---- mock SSEManager：按 sessionId 注册回调，测试手动驱动 SSE 事件 ----
interface StreamCallbacks {
  onProgress: (data: string) => void
  onResponse: (data: string) => void
  onComplete: () => void
  onError: (error: Error) => void
  onVerbose?: (message: any) => void
}

const streamRegistry = new Map<string, StreamCallbacks>()

vi.mock('@/api/agent', () => ({
  SSEManager: class {
    async connect(
      _message: string,
      sessionId: string,
      _files: any,
      _authHeaders: any,
      onProgress: (data: string) => void,
      onResponse: (data: string) => void,
      onComplete: () => void,
      onError: (error: Error) => void,
      _onToolStart?: unknown,
      _onToolResult?: unknown,
      _onThinking?: unknown,
      _onClarification?: unknown,
      _onImages?: unknown,
      _onBrowserHumanRequired?: unknown,
      onVerbose?: (message: any) => void,
    ): Promise<void> {
      streamRegistry.set(sessionId, {
        onProgress, onResponse, onComplete, onError, onVerbose,
      })
      return Promise.resolve()
    }
    disconnect(): void {}
  },
  uploadFile: vi.fn(),
}))

vi.mock('@/api/session', () => ({
  getSessionMessages: vi.fn().mockResolvedValue({ messages: [] }),
}))

vi.mock('@/composables/useDemoAuth', () => ({
  useDemoAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer demo' }) }),
}))
vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer tenant' }) }),
}))

let useAgent: typeof import('@/composables/useAgent').useAgent

function makeVerbose(eventId: string, data: string) {
  return { eventId, data, source: 'policy', timestamp: 1788144000000 }
}
function emitVerbose(sid: string, eventId: string, data: string) {
  streamRegistry.get(sid)?.onVerbose?.(makeVerbose(eventId, data))
}
function emitResponse(sid: string, data: string) {
  streamRegistry.get(sid)?.onResponse(data)
}
function emitComplete(sid: string) {
  streamRegistry.get(sid)?.onComplete()
}
function emitError(sid: string, message: string) {
  streamRegistry.get(sid)?.onError(new Error(message))
}

describe('useAgent live verbose（每轮一条中间提示）', () => {
  beforeEach(async () => {
    streamRegistry.clear()
    vi.clearAllMocks()
    vi.resetModules()
    ;({ useAgent } = await import('@/composables/useAgent'))
  })

  it('verbose 写入当前会话状态；同 eventId 重复帧被覆盖不累积', async () => {
    const agent = useAgent()
    await agent.sendMessage('出报价单', null, 'session_A')
    // 模拟用户正在查看 A（真实场景中 ChatContainer 会同步 sessionId）
    agent.sessionId.value = 'session_A'

    emitVerbose('session_A', 'verbose_1', '正在生成报价单，请耐心等待。')
    expect(agent.liveVerbose.value?.data).toBe('正在生成报价单，请耐心等待。')

    // 同 eventId 的后来帧覆盖（防御后端重发），仍是单条状态而非列表
    emitVerbose('session_A', 'verbose_1', '正在生成报价单，请稍候（更新）。')
    expect(agent.liveVerbose.value?.eventId).toBe('verbose_1')
    expect(agent.liveVerbose.value?.data).toBe('正在生成报价单，请稍候（更新）。')

    // 不同 eventId（防御：新一轮提示）同样覆盖，不残留上一条
    emitVerbose('session_A', 'verbose_2', '新的提示。')
    expect(agent.liveVerbose.value?.eventId).toBe('verbose_2')
    // 不进 debug 执行详情
    const assistant = agent.messages.value.find(m => m.role === 'assistant')!
    expect(assistant.progressMessages?.some(p => p.content.includes('报价单，请耐心等待'))).toBe(false)
  })

  it('response / error / complete 后 live verbose 被清除（response 后隐藏）', async () => {
    const agent = useAgent()
    await agent.sendMessage('长任务', null, 'session_A')
    agent.sessionId.value = 'session_A'

    emitVerbose('session_A', 'verbose_r1', '正在处理你的请求，请耐心等待。')
    expect(agent.liveVerbose.value).not.toBeNull()

    // response 开始 → 立即隐藏
    emitResponse('session_A', '最终回复')
    expect(agent.liveVerbose.value).toBeNull()

    // error 路径同样清除
    emitVerbose('session_A', 'verbose_r2', '还在处理。')
    emitError('session_A', '网络错误')
    expect(agent.liveVerbose.value).toBeNull()

    // complete 路径兜底清除
    emitVerbose('session_A', 'verbose_r3', '还在处理。')
    emitComplete('session_A')
    expect(agent.liveVerbose.value).toBeNull()
  })

  it('多会话 A/B 隔离：切换会话不串文案', async () => {
    const agent = useAgent()
    await agent.sendMessage('任务A', null, 'session_A')
    await agent.sendMessage('任务B', null, 'session_B')

    // A 在后台产生 verbose，当前查看 A（默认 sessionId 不变，显式切到 A）
    agent.sessionId.value = 'session_A'
    emitVerbose('session_A', 'verbose_a', 'A 的专属提示。')
    expect(agent.liveVerbose.value?.data).toBe('A 的专属提示。')

    // 切到 B：看到 B 的状态（null），不得看到 A 的文案
    await agent.switchSession('session_B')
    expect(agent.liveVerbose.value?.data).toBeUndefined()

    emitVerbose('session_B', 'verbose_b', 'B 的专属提示。')
    expect(agent.liveVerbose.value?.data).toBe('B 的专属提示。')

    // 切回 A：显示 A 自己的 verbose，未被 B 覆盖
    await agent.switchSession('session_A')
    expect(agent.liveVerbose.value?.data).toBe('A 的专属提示。')
    expect(agent.liveVerbose.value?.eventId).toBe('verbose_a')
  })

  it('新一轮 sendMessage 重置上一轮 verbose（fake timers 表达 8 秒提示时间线）', async () => {
    vi.useFakeTimers()
    try {
      const agent = useAgent()
      agent.sessionId.value = 'session_slow'
      await agent.sendMessage('执行 10 秒的 mock 工具', null, 'session_slow')

      // t≈8s：watchdog 提示到达，占位应展示 verbose 文案
      await vi.advanceTimersByTimeAsync(8000)
      emitVerbose('session_slow', 'verbose_slow', '正在处理你的请求，复杂任务可能需要一点时间，请耐心等待。')
      expect(agent.liveVerbose.value?.data).toContain('正在处理你的请求')

      // t≈10s：final 到达 → verbose 隐藏，正常展示 final
      await vi.advanceTimersByTimeAsync(2000)
      emitResponse('session_slow', '任务完成的结果')
      emitComplete('session_slow')
      expect(agent.liveVerbose.value).toBeNull()
      expect(agent.currentResponse.value).toBe('任务完成的结果')

      // 新一轮发送：重置上一轮残留
      await agent.sendMessage('再来一轮', null, 'session_slow')
      expect(agent.liveVerbose.value).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})
