/**
 * useAgent 多会话后台流式单元测试
 *
 * 业务背景：旧架构全局只有一份流式状态，切换会话必须中断正在进行的 Agent 任务，
 * 用户切回后看不到已生成内容。重构为 per-session 状态池后：
 * - 后台会话的 SSE 事件只能写入自己的状态，绝不串到当前查看的会话
 * - 切回正在进行的会话能看到实时累积内容
 * - 后台完成的会话在会话列表显示「完成小点」，查看后消失
 * - 某会话进行中不阻塞其他会话发消息
 *
 * 这些测试守住的是「会话间流式状态隔离」这一核心业务承诺，
 * 一旦状态池实现退回全局单份状态，测试应立即失败。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ---- mock SSEManager：按 sessionId 注册回调，测试手动驱动 SSE 事件 ----
interface StreamCallbacks {
  onProgress: (data: string) => void
  onResponse: (data: string) => void
  onComplete: () => void
  onError: (error: Error) => void
  onToolStart: (toolName: string, toolArgs: object, displayName?: string, toolCallId?: string) => void
  onToolResult: (toolName: string, result: any, success: boolean, displayName?: string, toolCallId?: string) => void
  disconnected: boolean
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
      onToolStart: StreamCallbacks['onToolStart'],
      onToolResult: StreamCallbacks['onToolResult'],
    ): Promise<void> {
      streamRegistry.set(sessionId, {
        onProgress, onResponse, onComplete, onError,
        onToolStart, onToolResult, disconnected: false,
      })
      // 回调已注册到 registry，测试后续手动驱动 SSE 事件；
      // 立即 resolve 让 sendMessage 返回（状态翻转只由回调触发，不受 resolve 影响）
      return Promise.resolve()
    }
    disconnect(): void {
      // 状态翻转由 useAgent.abortStreaming 自身完成，无需模拟 AbortError 路径
    }
  },
  uploadFile: vi.fn(),
}))

vi.mock('@/api/session', () => ({
  getSessionMessages: vi.fn().mockResolvedValue({ messages: [] }),
}))

vi.mock('@/composables/useTenantAuth', () => ({
  useTenantAuth: () => ({ getAuthHeader: () => ({ Authorization: 'Bearer tenant' }) }),
}))

// fetch（abortStreaming 通知后端 cancel）mock
const fetchMock = vi.fn().mockResolvedValue({ ok: true })
Object.defineProperty(globalThis, 'fetch', { value: fetchMock, writable: true })

// useAgent 含模块级状态池，每个测试需 resetModules + 动态 import 获得干净状态
let useAgent: typeof import('@/composables/useAgent').useAgent

/** 辅助：向指定会话推送 SSE 事件 */
function emitResponse(sid: string, data: string) {
  streamRegistry.get(sid)?.onResponse(data)
}
function emitComplete(sid: string) {
  streamRegistry.get(sid)?.onComplete()
}
function emitToolStart(
  sid: string, toolName: string, displayName: string, toolCallId: string,
) {
  streamRegistry.get(sid)?.onToolStart(toolName, {}, displayName, toolCallId)
}
function emitToolResult(
  sid: string, toolName: string, result: any, success: boolean,
  displayName: string, toolCallId: string,
) {
  streamRegistry.get(sid)?.onToolResult(
    toolName, result, success, displayName, toolCallId,
  )
}

describe('useAgent 多会话后台流式', () => {
  beforeEach(async () => {
    streamRegistry.clear()
    vi.clearAllMocks()
    vi.resetModules()
    ;({ useAgent } = await import('@/composables/useAgent'))
  })

  it('后台会话的 SSE 事件写入自己的状态，不串到当前查看的会话', async () => {
    const agent = useAgent()

    // A 会话发消息，开始流式
    await agent.sendMessage('任务A', null, 'session_A')
    expect(streamRegistry.has('session_A')).toBe(true)

    // 切换到 B 会话（DB 无历史消息）
    await agent.switchSession('session_B')

    // A 在后台继续收流
    emitResponse('session_A', 'A的回复内容')

    // 当前查看 B：消息列表不应包含 A 的内容
    expect(agent.messages.value.map(m => m.content)).not.toContain('A的回复内容')
    expect(agent.messages.value.length).toBe(0)
  })

  it('切回正在进行的会话，显示其实时累积内容', async () => {
    const agent = useAgent()

    await agent.sendMessage('任务A', null, 'session_A')
    await agent.switchSession('session_B')
    emitResponse('session_A', '第一段')
    emitResponse('session_A', '第二段')

    // 切回 A
    await agent.switchSession('session_A')

    const contents = agent.messages.value.map(m => m.content)
    expect(contents).toContain('任务A')
    expect(contents).toContain('第一段第二段')
    // A 仍在进行中
    expect(agent.isSessionRunning('session_A')).toBe(true)
  })

  it('后台完成的会话标记完成小点，查看后小点消失', async () => {
    const agent = useAgent()

    await agent.sendMessage('任务A', null, 'session_A')
    await agent.switchSession('session_B')

    // A 在后台完成
    emitComplete('session_A')
    expect(agent.hasSessionUnreadCompletion('session_A')).toBe(true)
    expect(agent.isSessionRunning('session_A')).toBe(false)

    // 用户切回 A 查看 → 小点消失
    await agent.switchSession('session_A')
    expect(agent.hasSessionUnreadCompletion('session_A')).toBe(false)
  })

  it('当前查看的会话完成时不标记完成小点', async () => {
    const agent = useAgent()

    await agent.sendMessage('任务A', null, 'session_A')
    // 模拟用户正在查看 A（真实场景中 ChatContainer 会同步 sessionId）
    agent.sessionId.value = 'session_A'
    emitComplete('session_A')

    expect(agent.hasSessionUnreadCompletion('session_A')).toBe(false)
    expect(agent.isSessionRunning('session_A')).toBe(false)
  })

  it('某会话进行中不阻塞其他会话发消息，但同一会话重复发送被拒绝', async () => {
    const agent = useAgent()

    await agent.sendMessage('任务A', null, 'session_A')
    expect(agent.isSessionRunning('session_A')).toBe(true)

    // A 进行中，B 仍可发消息
    await agent.sendMessage('任务B', null, 'session_B')
    expect(streamRegistry.has('session_B')).toBe(true)
    expect(agent.isSessionRunning('session_B')).toBe(true)

    // 同一会话 A 在处理中，重复发送被拒绝（registry 中仍是第一次的回调）
    const callbacksBefore = streamRegistry.get('session_A')
    await agent.sendMessage('任务A第二次', null, 'session_A')
    expect(streamRegistry.get('session_A')).toBe(callbacksBefore)
  })

  it('abortStreaming 只停止当前查看的会话，后台会话继续运行', async () => {
    const agent = useAgent()

    await agent.sendMessage('任务A', null, 'session_A')
    await agent.sendMessage('任务B', null, 'session_B')

    // 当前查看 B（最后一次 sendMessage 的 overrideSessionId 不改变查看会话，
    // 显式切换到 B）
    await agent.switchSession('session_B')
    await agent.abortStreaming()

    expect(agent.isSessionRunning('session_B')).toBe(false)
    expect(agent.isSessionRunning('session_A')).toBe(true)

    // 后端 cancel 通知使用 B 的 sessionId
    // 注：测试环境 fetch 被 MSW 包装，mock 收到的是 FetchRequest 对象而非 (url, options)
    expect(fetchMock).toHaveBeenCalled()
    const firstArg = fetchMock.mock.calls[0][0]
    const cancelUrl = typeof firstArg === 'string' ? firstArg : firstArg.url
    expect(cancelUrl).toContain('/chat/session_B/cancel')
  })

  it('switchSession 不再中断正在进行的流式连接', async () => {
    const agent = useAgent()

    await agent.sendMessage('任务A', null, 'session_A')
    await agent.switchSession('session_B')
    await agent.switchSession('session_C')

    // A 的流仍然存活
    expect(agent.isSessionRunning('session_A')).toBe(true)
    emitResponse('session_A', '仍在生成')
    emitComplete('session_A')
    expect(agent.hasSessionUnreadCompletion('session_A')).toBe(true)
  })

  it('同名工具按 toolCallId 独立关联，并按结果形状消费文件和快捷选项', async () => {
    const agent = useAgent()
    await agent.sendMessage('执行两个同名工具', null, 'session_tools')
    agent.sessionId.value = 'session_tools'

    emitToolStart('session_tools', 'future_tool', '未来工具', 'call-1')
    emitToolStart('session_tools', 'future_tool', '未来工具', 'call-2')
    emitToolResult(
      'session_tools', 'future_tool',
      { success: true, file_id: 'hidden', visible: false },
      true, '未来工具', 'call-1',
    )
    emitToolResult(
      'session_tools', 'future_tool',
      {
        success: true,
        file_id: 'file-2',
        file_name: 'report.pdf',
        data: { options: [
          { key: '1', label: '选项一' },
          { key: '2', label: '选项二' },
        ] },
      },
      true, '未来工具', 'call-2',
    )

    const assistant = agent.messages.value.find(message => message.role === 'assistant')!
    const toolProgress = assistant.progressMessages!.filter(
      progress => progress.type === 'tool_start' || progress.type === 'tool_result',
    )
    expect(toolProgress.map(progress => progress.toolCallId)).toEqual([
      'call-1', 'call-2', 'call-1', 'call-2',
    ])
    expect(toolProgress.every(progress => progress.displayName === '未来工具')).toBe(true)
    expect(assistant.downloadableFiles?.map(file => file.file_id)).toEqual(['file-2'])
    expect(assistant.quickOptions?.map(option => option.key)).toEqual(['1', '2'])
  })
})
