import type { MessageStreamEvent } from '@/types'
import { getAuthHeader } from './auth'

// 上传文件接口
export interface UploadedFile {
  file_id: string
  name: string
  size: number
  mime_type: string
  type: 'image' | 'file'
}

/**
 * 上传文件到服务器
 */
export async function uploadFile(file: File, authHeaders?: Record<string, string>): Promise<UploadedFile> {
  try {
    const formData = new FormData()
    formData.append('file', file)

    const headers: Record<string, string> = {
      ...authHeaders
    }
    // Don't set Content-Type for FormData - browser sets it automatically with boundary

    const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
    console.log('前端日志：开始上传文件', file.name, '大小:', file.size, '字节')
    const response = await fetch(`${apiBase}/upload`, {
      method: 'POST',
      headers,
      body: formData,
    })

    console.log('前端日志：upload 响应状态码', response.status)

    if (!response.ok) {
      const errorData = await response.json().catch(() => null)
      console.log('前端日志：upload 错误响应', errorData)
      if (errorData?.error) {
        throw new Error(errorData.error)
      }
      if (response.status === 413) {
        throw new Error('文件过大，服务器拒绝接收')
      }
      throw new Error(`上传失败: HTTP ${response.status}`)
    }

    let result: any
    try {
      result = await response.json()
    } catch (e) {
      console.error('前端日志：upload 响应解析失败，响应内容不是有效的 JSON')
      throw new Error('服务器响应异常，请刷新页面后重试')
    }
    console.log('前端日志：upload 响应数据', result)
    if (!result.success) {
      throw new Error(result.error || '上传失败')
    }

    console.log('前端日志：文件上传成功', result)
    return result
  } catch (error: any) {
    console.error('前端日志：upload 异常捕获', error)
    if (error instanceof Error) {
      throw error
    }
    throw new Error(error?.message || error?.toString?.() || '文件上传失败')
  }
}

/**
 * 删除已上传的文件
 */
export async function deleteFile(file_id: string): Promise<void> {
  const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
  const response = await fetch(`${apiBase}/upload/${file_id}`, {
    method: 'DELETE',
    headers: { ...getAuthHeader() },
  })

  if (!response.ok) {
    throw new Error(`删除失败: ${response.status}`)
  }
}

/**
 * 获取文件内联预览 URL（用于图片、PDF 等浏览器可预览的文件）
 */
export function getFileUrl(fileId: string): string {
  const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
  return `${apiBase}/files/${fileId}`
}

/**
 * 获取文件下载 URL
 */
export function getFileDownloadUrl(fileId: string): string {
  const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
  return `${apiBase}/files/${fileId}/download`
}

/**
 * SSE连接管理器
 * 处理Server-Sent Events的解析和事件分发
 */
export class SSEManager {
  private abortController: AbortController | null = null

  async connect(
    message: string,
    sessionId: string,
    files: UploadedFile[] | undefined,
    authHeaders: Record<string, string>,
    onProgress: (data: string) => void,
    onResponse: (data: string) => void,
    onComplete: () => void,
    onError: (error: Error) => void,
    onToolStart?: (toolName: string, toolArgs: object) => void,
    onToolResult?: (toolName: string, result: any, success: boolean) => void,
    onThinking?: (data: string) => void,
    onClarification?: (subagentName: string, question: string) => void,
    subagent?: string | null
  ): Promise<void> {
    this.abortController = new AbortController()

    try {
      const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
      const response = await fetch(`${apiBase}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders
        },
        body: JSON.stringify({
          message,
          session_id: sessionId,
          files: files?.map(f => ({
            type: f.type,
            name: f.name,
            file_id: f.file_id,
            mime_type: f.mime_type,
            size: f.size,
          })),
          ...(subagent ? { subagent } : {}),
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
            this.parseSSELine(buffer, { onProgress, onResponse, onComplete, onError, onToolStart, onToolResult, onThinking, onClarification })
          }
          break
        }

        buffer += decoder.decode(value, { stream: true })

        // 按 SSE 格式分割：每条消息以空行分隔
        // 完整事件格式: "data: {...}\n\n"
        const messages = buffer.split(/\n\n/)
        buffer = messages.pop() || '' // 保留最后一条不完整的消息

        for (const msg of messages) {
          this.parseSSELine(msg, { onProgress, onResponse, onComplete, onError, onToolStart, onToolResult, onThinking, onClarification })
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
      onComplete: () => void
      onError: (error: Error) => void
      onToolStart?: (toolName: string, toolArgs: object) => void
      onToolResult?: (toolName: string, result: any, success: boolean) => void
      onThinking?: (data: string) => void
      onClarification?: (subagentName: string, question: string) => void
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
            callbacks.onComplete?.()
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
          case 'clarification':
            callbacks.onClarification?.(event.subagentName, event.question)
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
