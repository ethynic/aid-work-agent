import { ref, onUnmounted } from 'vue'
import type { ChatMessage, ProgressMessage } from '@/types'
import { SSEManager } from '@/api/agent'

export function useAgent() {
  const messages = ref<ChatMessage[]>([])
  const progressMessages = ref<ProgressMessage[]>([])
  const isProcessing = ref(false)
  const currentResponse = ref('')
  const error = ref<string | null>(null)
  const sessionId = ref<string>(generateSessionId())

  const sseManager = new SSEManager()

  function generateSessionId(): string {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9)
  }

  async function sendMessage(content: string) {
    if (!content.trim() || isProcessing.value) return

    // 添加用户消息
    messages.value.push({
      role: 'user',
      content: content.trim(),
      timestamp: Date.now()
    })

    // 重置状态
    isProcessing.value = true
    error.value = null
    currentResponse.value = ''
    progressMessages.value = []

    // 添加初始进度消息
    addProgress('🚀 正在发送请求...', 'progress')

    // 添加空的助手消息占位
    const assistantMessageIndex = messages.value.length
    messages.value.push({
      role: 'assistant',
      content: '',
      timestamp: Date.now()
    })

    try {
      await sseManager.connect(
        content,
        sessionId.value,
        // onProgress - 工具执行进度，同时追加到助手消息内容
        (data) => {
          addProgress(data, 'progress')
          // 将进度追加到助手消息内容
          currentResponse.value += `\n${data}`
          if (assistantMessageIndex < messages.value.length) {
            messages.value[assistantMessageIndex].content = currentResponse.value
          }
        },
        // onResponse - AI响应内容
        (data) => {
          currentResponse.value += data
          if (assistantMessageIndex < messages.value.length) {
            messages.value[assistantMessageIndex].content = currentResponse.value
          }
        },
        // onComplete
        () => {
          addProgress('✅ 任务完成', 'complete')
          isProcessing.value = false
        },
        // onError
        (err) => {
          error.value = err.message
          addProgress(`❌ 错误: ${err.message}`, 'error')
          isProcessing.value = false
        }
      )
    } catch (err) {
      error.value = (err as Error).message
      addProgress(`❌ 连接错误: ${(err as Error).message}`, 'error')
      isProcessing.value = false
    }
  }

  function addProgress(content: string, type: ProgressMessage['type']) {
    progressMessages.value.push({
      type,
      content,
      timestamp: Date.now()
    })
  }

  function clearMessages() {
    messages.value = []
    progressMessages.value = []
    currentResponse.value = ''
    error.value = null
  }

  function clearSession() {
    sessionId.value = generateSessionId()
    clearMessages()
  }

  // 组件卸载时断开连接
  onUnmounted(() => {
    sseManager.disconnect()
  })

  return {
    messages,
    progressMessages,
    isProcessing,
    currentResponse,
    error,
    sessionId,
    sendMessage,
    clearMessages,
    clearSession
  }
}
