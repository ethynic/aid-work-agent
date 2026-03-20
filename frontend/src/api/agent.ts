import axios from 'axios'
import type { SendMessageRequest, SendMessageResponse, MessageStreamEvent } from '@/types'

const api = axios.create({
  baseURL: '/api',
  timeout: 300000 // 5分钟超时，Agent任务可能很长
})

export const agentApi = {
  /**
   * 发送消息并通过SSE接收流式响应
   */
  sendMessage(request: SendMessageRequest): EventSource {
    const params = new URLSearchParams({
      message: request.message,
      session_id: request.session_id
    })
    
    // 注意：实际部署时需要配置代理将SSE请求转发到后端
    const eventSource = new EventSource(`/api/chat/stream?${params.toString()}`)
    
    return eventSource
  },

  /**
   * 发送消息（非流式，用于测试）
   */
  async sendMessageSync(request: SendMessageRequest): Promise<SendMessageResponse> {
    const formData = new FormData()
    formData.append('message', request.message)
    formData.append('session_id', request.session_id)
    
    if (request.files) {
      request.files.forEach(file => {
        formData.append('files', file)
      })
    }

    const response = await api.post<SendMessageResponse>('/chat', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    })
    
    return response.data
  },

  /**
   * 获取会话历史
   */
  async getHistory(sessionId: string): Promise<{ history: Array<{ role: string; content: string }> }> {
    const response = await api.get(`/chat/history/${sessionId}`)
    return response.data
  },

  /**
   * 清除会话
   */
  async clearSession(sessionId: string): Promise<void> {
    await api.delete(`/chat/session/${sessionId}`)
  }
}

/**
 * 创建自定义的SSE连接处理类
 * 适用于后端不完全支持标准SSE的情况
 */
export class SSEManager {
  private controller: ReadableStreamDefaultController | null = null
  private reader: ReadableStreamDefaultReader | null = null
  private abortController: AbortController | null = null

  /**
   * 使用fetch创建SSE连接（更现代的方式）
   */
  async connect(
    message: string,
    sessionId: string,
    onProgress: (data: string) => void,
    onResponse: (data: string) => void,
    onComplete: () => void,
    onError: (error: Error) => void
  ): Promise<void> {
    this.abortController = new AbortController()

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message, session_id: sessionId }),
        signal: this.abortController.signal,
      })

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('Response body is not readable')
      }

      this.reader = reader
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        
        if (done) {
          onComplete()
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            
            if (data === '[DONE]') {
              onComplete()
              return
            }

            try {
              const event = JSON.parse(data) as MessageStreamEvent
              
              switch (event.type) {
                case 'progress':
                  onProgress(event.data)
                  break
                case 'response':
                  onResponse(event.data)
                  break
                case 'complete':
                  onComplete()
                  break
                case 'error':
                  onError(new Error(event.data))
                  break
              }
            } catch {
              // 如果不是JSON，当作纯文本处理
              onResponse(data)
            }
          }
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
   * 断开连接
   */
  disconnect(): void {
    this.abortController?.abort()
    this.reader?.cancel()
    this.controller?.close()
  }
}

export default agentApi
