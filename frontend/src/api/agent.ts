import type { MessageStreamEvent } from '@/types'

/**
 * SSE连接管理器
 * 处理Server-Sent Events的解析和事件分发
 */
export class SSEManager {
  private abortController: AbortController | null = null

  async connect(
    message: string,
    sessionId: string,
    onProgress: (data: string) => void,
    onResponse: (data: string) => void,
    onComplete: () => void,
    onError: (error: Error) => void,
    onToolStart?: (toolName: string, toolArgs: object) => void,
    onToolResult?: (toolName: string, result: any, success: boolean) => void,
    onThinking?: (data: string) => void
  ): Promise<void> {
    this.abortController = new AbortController()

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message,
          session_id: sessionId
        }),
        signal: this.abortController.signal,
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      if (!response.body) {
        throw new Error('Response body is null')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()

        if (done) {
          // 处理缓冲区中剩余的数据
          if (buffer.trim()) {
            this.parseSSELine(buffer, { onProgress, onResponse, onError, onToolStart, onToolResult, onThinking })
          }
          onComplete()
          break
        }

        buffer += decoder.decode(value, { stream: true })

        // 按 SSE 格式分割：每条消息以空行分隔
        // 完整事件格式: "data: {...}\n\n"
        const messages = buffer.split(/\n\n/)
        buffer = messages.pop() || '' // 保留最后一条不完整的消息

        for (const msg of messages) {
          this.parseSSELine(msg, { onProgress, onResponse, onError, onToolStart, onToolResult, onThinking })
        }
      }
    } catch (error) {
      if ((error as Error).name === 'AbortError') {
        onComplete()
      } else {
        onError(error as Error)
      }
    }
  }

  /**
   * 解析单条 SSE 行
   */
  private parseSSELine(
    line: string,
    callbacks: {
      onProgress: (data: string) => void
      onResponse: (data: string) => void
      onError: (error: Error) => void
      onToolStart?: (toolName: string, toolArgs: object) => void
      onToolResult?: (toolName: string, result: any, success: boolean) => void
      onThinking?: (data: string) => void
    }
  ) {
    // 处理多行数据
    const dataLines = line.split('\n')

    for (const l of dataLines) {
      if (!l.startsWith('data: ')) continue

      const data = l.slice(6).trim()

      if (data === '[DONE]') {
        callbacks.onComplete?.()
        return
      }

      try {
        const event = JSON.parse(data) as MessageStreamEvent

        switch (event.type) {
          case 'connected':
            // 连接成功，不需要特殊处理
            break
          case 'progress':
            callbacks.onProgress(event.data)
            break
          case 'response':
            callbacks.onResponse(event.data)
            break
          case 'complete':
            // 由外层循环处理
            break
          case 'error':
            callbacks.onError(new Error(event.data))
            break
          case 'tool_start':
            callbacks.onToolStart?.(event.toolName, event.toolArgs)
            break
          case 'tool_result':
            callbacks.onToolResult?.(event.toolName, event.result, event.success)
            break
          case 'thinking':
            callbacks.onThinking?.(event.data)
            break
        }
      } catch {
        // 非JSON数据，当作响应处理
        if (data) {
          callbacks.onResponse(data)
        }
      }
    }
  }

  disconnect(): void {
    this.abortController?.abort()
  }
}

export default SSEManager
