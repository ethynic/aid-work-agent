import { ref, onUnmounted } from 'vue'
import type { ChatMessage, ProgressMessage } from '@/types'
import { SSEManager, uploadFile, type UploadedFile } from '@/api/agent'
import { useAuth } from './useAuth'

export function useAgent() {
  const { getAuthHeader } = useAuth()
  const messages = ref<ChatMessage[]>([])
  const progressMessages = ref<ProgressMessage[]>([])
  const isProcessing = ref(false)
  const currentResponse = ref('')
  const error = ref<string | null>(null)
  const sessionId = ref<string>(generateSessionId())

  // 当前附件列表
  const currentFiles = ref<UploadedFile[]>([])

  const sseManager = new SSEManager()

  function generateSessionId(): string {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9)
  }

  /**
   * 上传单个文件
   */
  async function uploadAttachment(file: File): Promise<UploadedFile> {
    const uploaded = await uploadFile(file)
    currentFiles.value.push(uploaded)
    return uploaded
  }

  /**
   * 移除已上传的附件
   */
  function removeAttachment(file_id: string) {
    currentFiles.value = currentFiles.value.filter(f => f.file_id !== file_id)
  }

  /**
   * 清空所有附件
   */
  function clearAttachments() {
    currentFiles.value = []
  }

  async function sendMessage(content: string) {
    if (!content.trim() || isProcessing.value) return

    // 构建用户消息内容（含附件信息）
    let userContent = content.trim()
    if (currentFiles.value.length > 0) {
      const fileNames = currentFiles.value.map(f => f.name).join(', ')
      userContent += `\n\n[附件: ${fileNames}]`
    }

    // 添加用户消息
    messages.value.push({
      role: 'user',
      content: userContent,
      timestamp: Date.now()
    })

    // 重置状态
    isProcessing.value = true
    error.value = null
    currentResponse.value = ''
    progressMessages.value = []

    // 添加空的助手消息占位
    const assistantMessageIndex = messages.value.length
    const assistantMessage = {
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
      progressMessages: [] as ProgressMessage[]  // 初始化空数组
    }
    messages.value.push(assistantMessage)

    // 添加初始进度消息（在 AI 消息创建之后）
    addProgress('🚀 正在发送请求...', 'progress')

    try {
      await sseManager.connect(
        content,
        sessionId.value,
        currentFiles.value.length > 0 ? [...currentFiles.value] : undefined,
        getAuthHeader(), // 传递认证头
        // onProgress - 工具执行进度，仅添加到执行详情
        (data) => {
          addProgress(data, 'progress')
          // 不再将进度追加到助手消息内容
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
        },
        // onToolStart - 工具开始执行
        (toolName, toolArgs) => {
          const toolDisplayName = getToolDisplayName(toolName, toolArgs)
          addProgress(`🔧 开始执行 ${toolDisplayName}...`, 'tool_start', toolName, toolArgs)
        },
        // onToolResult - 工具执行结果
        (toolName, result, success) => {
          const toolDisplayName = getToolDisplayName(toolName, {})
          if (success) {
            // 根据不同工具显示不同结果预览
            if (toolName === 'web_search') {
              const results = result?.results || []
              addProgress(`✅ ${toolDisplayName}完成，找到${results.length}条结果`, 'tool_result', toolName, undefined, result)
            } else if (toolName === 'email_send') {
              addProgress(`✅ ${toolDisplayName}成功`, 'tool_result', toolName, undefined, result)
            } else if (toolName === 'content_generate') {
              const content = result?.content || ''
              const preview = content.length > 100 ? content.slice(0, 100) + '...' : content
              addProgress(`✅ ${toolDisplayName}完成\n📝 ${preview}`, 'tool_result', toolName, undefined, result)
            } else if (toolName === 'file_read') {
              const content = result?.content || ''
              const preview = content.length > 100 ? content.slice(0, 100) + '...' : content
              addProgress(`✅ ${toolDisplayName}完成\n📄 ${preview}`, 'tool_result', toolName, undefined, result)
            } else if (toolName === 'browser_open') {
              addProgress(`✅ ${toolDisplayName}成功`, 'tool_result', toolName, undefined, result)
            } else {
              addProgress(`✅ ${toolDisplayName}执行完成`, 'tool_result', toolName, undefined, result)
            }
          } else {
            const errorMsg = result?.error || '未知错误'
            addProgress(`❌ ${toolDisplayName}失败: ${errorMsg}`, 'tool_result', toolName, undefined, result)
          }
        },
        // onThinking - LLM思考中
        (data) => {
          addProgress(`🤔 ${data}`, 'thinking')
        }
      )
    } catch (err) {
      error.value = (err as Error).message
      addProgress(`❌ 连接错误: ${(err as Error).message}`, 'error')
      isProcessing.value = false
    }
  }

  function getToolDisplayName(toolName: string, toolArgs: object): string {
    switch (toolName) {
      case 'web_search':
        const keyword = (toolArgs as any)?.keyword || ''
        return `网络搜索「${keyword.slice(0, 20)}...」`
      case 'email_send':
        const to = (toolArgs as any)?.to || ''
        return `发送邮件至「${to}」`
      case 'email_read':
        const folder = (toolArgs as any)?.folder || 'INBOX'
        const limit = (toolArgs as any)?.limit || 10
        return `读取邮件（${folder}，${limit}封）`
      case 'content_generate':
        const contentType = (toolArgs as any)?.content_type || ''
        return `生成内容（${contentType}）`
      case 'browser_open':
        const url = (toolArgs as any)?.url || ''
        return `打开网页「${url.slice(0, 30)}...」`
      case 'delegate_to_subagent':
        const subagentName = (toolArgs as any)?.subagent_name || ''
        return `调用${subagentName}子智能体`
      case 'skill_execute':
        const skill = (toolArgs as any)?.skill || ''
        return `执行技能「${skill}」`
      case 'use_skill':
        const skillName = (toolArgs as any)?.skill || ''
        return `加载技能「${skillName}」`
      case 'file_read':
        const filePath = (toolArgs as any)?.file_path || ''
        return `读取文件「${filePath}」`
      case 'doc_summarize':
        return '总结文档'
      case 'doc_translate':
        const target = (toolArgs as any)?.target_lang || ''
        return `翻译文档为${target}`
      case 'ocr_image':
        const imagePath = (toolArgs as any)?.image_path || ''
        return `识别图片文字「${imagePath}」`
      case 'create_plan':
        return '创建执行计划'
      default:
        return toolName
    }
  }

  function addProgress(
    content: string,
    type: ProgressMessage['type'],
    toolName?: string,
    toolArgs?: object,
    result?: any
  ) {
    const newMsg: ProgressMessage = {
      type,
      content,
      timestamp: Date.now(),
      toolName,
      toolArgs,
      result
    }
    progressMessages.value.push(newMsg)

    // 同时更新 AI 消息占位中的 progressMessages（用于 MessageItem 显示）
    const lastMsg = messages.value[messages.value.length - 1]
    if (lastMsg && lastMsg.role === 'assistant' && lastMsg.progressMessages) {
      lastMsg.progressMessages.push(newMsg)
    }
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
    currentFiles,
    sendMessage,
    clearMessages,
    clearSession,
    uploadAttachment,
    removeAttachment,
    clearAttachments
  }
}
