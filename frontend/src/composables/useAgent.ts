import { ref, onUnmounted } from 'vue'
import type { ChatMessage, ProgressMessage } from '@/types'
import { SSEManager, uploadFile, type UploadedFile } from '@/api/agent'
import { getSessionMessages } from '@/api/session'
import { useDemoAuth } from './useDemoAuth'
import { useTenantAuth } from './useTenantAuth'

// 全局共享状态 - 整个应用只维护一份会话状态
const messages = ref<ChatMessage[]>([])
const progressMessages = ref<ProgressMessage[]>([])
const isProcessing = ref(false)
const currentResponse = ref('')
const error = ref<string | null>(null)
const sessionId = ref<string>(generateSessionId())

// 排队相关状态
const isBusy = ref(false)
const busyMessage = ref('')
const busyInstanceId = ref('')
const isSameUser = ref(false)
const pendingMessage = ref('') // 等待排队发送的消息

// 当前附件列表
const currentFiles = ref<UploadedFile[]>([])

// 全局唯一的 SSE 管理器
const sseManager = new SSEManager()

// 用户主动取消标记（区分 abort 和正常完成）
let _cancelledByUser = false

// Per-session 消息缓存：切换会话时保存当前会话的实时消息快照
const sessionMessagesCache = new Map<string, ChatMessage[]>()

function generateSessionId(): string {
  return 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9)
}

export function useAgent() {
  // 根据路由判断使用 demo 还是 tenant 认证头
  function getEffectiveAuthHeader(): Record<string, string> {
    if (window.location.pathname.startsWith('/t/')) {
      const { getAuthHeader } = useTenantAuth()
      return getAuthHeader()
    }
    const { getAuthHeader } = useDemoAuth()
    return getAuthHeader()
  }

  function now(): string {
    const d = new Date()
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(2, '0')}.${d.getMilliseconds().toString().padStart(3, '0')}`
  }

  /**
   * 切换会话：保存当前会话的消息快照到缓存，恢复目标会话的消息
   */
  async function switchSession(newSessionId: string): Promise<void> {
    // 保存当前会话的消息到缓存（仅当有消息且正在处理时，避免覆盖已完成的干净状态）
    const currentSid = sessionId.value
    if (currentSid && messages.value.length > 0) {
      sessionMessagesCache.set(currentSid, JSON.parse(JSON.stringify(messages.value)))
    }

    // 同步会话ID
    sessionId.value = newSessionId

    // 清空当前状态
    currentResponse.value = ''
    error.value = null
    progressMessages.value = []
    isProcessing.value = false

    // 优先使用缓存（包含实时消息和进行中的内容），否则从数据库加载
    const cached = sessionMessagesCache.get(newSessionId)
    if (cached) {
      messages.value = cached
    } else {
      // 检查缓存中是否标记为"空会话"（新建的会话），直接返回空数组，跳过DB请求
      if (sessionMessagesCache.has(newSessionId) && sessionMessagesCache.get(newSessionId)!.length === 0) {
        messages.value = []
        sessionMessagesCache.delete(newSessionId)
      } else {
        const result = await getSessionMessages(newSessionId)
        messages.value = result.messages?.map(m => ({
          role: m.role as 'user' | 'assistant',
          content: m.content,
          timestamp: new Date(m.created_at).getTime(),
          progressMessages: m.metadata?.progressMessages || [],
          attachments: m.metadata?.attachments || undefined,
          downloadableFiles: m.metadata?.downloadableFiles || undefined
        })) || []
        // 如果数据库也没有消息，确保缓存中也没有，避免下次误读
        if (!result.messages || result.messages.length === 0) {
          sessionMessagesCache.delete(newSessionId)
        }
      }
    }
  }

  /**
   * 上传单个文件
   */
  async function uploadAttachment(file: File): Promise<UploadedFile> {
    const uploaded = await uploadFile(file, getEffectiveAuthHeader())
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

  async function sendMessage(content: string, subagent?: string | null, overrideSessionId?: string, instanceId?: string | null) {
    if (!content.trim() || isProcessing.value) return

    // 优先使用外部传入的 sessionId（来自 DB 的真实会话 ID），避免与本地生成的 sessionId 产生竞态
    const effectiveSessionId = overrideSessionId || sessionId.value

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
      timestamp: Date.now(),
      attachments: currentFiles.value.length > 0 ? [...currentFiles.value] : undefined
    })

    // 重置状态
    isProcessing.value = true
    error.value = null
    currentResponse.value = ''
    progressMessages.value = []

    // 添加空的助手消息占位
    const assistantMessageIndex = messages.value.length
    const assistantMessage = {
      role: 'assistant' as const,
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
        effectiveSessionId,
        currentFiles.value.length > 0 ? [...currentFiles.value] : undefined,
        getEffectiveAuthHeader(), // 传递认证头
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
          if (_cancelledByUser) {
            addProgress('⚠️ 已停止', 'error')
            _cancelledByUser = false
          } else {
            addProgress('✅ 任务完成', 'complete')
          }
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
          addProgress(`🔧 需要调用工具【${toolDisplayName}】`, 'tool_start', toolName, toolArgs)
        },
        // onToolResult - 工具执行结果
        (toolName, result, success) => {
          const toolDisplayName = getToolDisplayName(toolName, {})
          if (success) {
            // 提取下载文件信息到助手消息
            const downloadToolNames = ['register_download_file', 'file_write']
            if (downloadToolNames.includes(toolName) && result?.file_id) {
              const lastMsg = messages.value[messages.value.length - 1]
              if (lastMsg && lastMsg.role === 'assistant') {
                if (!lastMsg.downloadableFiles) {
                  lastMsg.downloadableFiles = []
                }
                // 按 file_id 去重
                if (!lastMsg.downloadableFiles.some(f => f.file_id === result.file_id)) {
                  lastMsg.downloadableFiles.push({
                    file_id: result.file_id,
                    file_name: result.download_file_name || result.file_name || '未命名文件',
                    file_size: result.file_size || 0,
                    download_url: result.download_url || `/api/files/${result.file_id}/download`,
                    mime_type: result.mime_type || '',
                  })
                }
              }
            }
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
            } else if (toolName === 'register_download_file') {
              const fileName = result?.file_name || '文件'
              addProgress(`✅ 已生成文件「${fileName}」，可在下方下载`, 'tool_result', toolName, undefined, result)
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
        },
        // onClarification - 子智能体需要用户补充信息
        (subagentName, question) => {
          addProgress(`❓ ${subagentName}需要补充信息: ${question}`, 'tool_start', 'clarification')
        },
        // onBusy - 实例繁忙，显示排队选项
        (instance_id, message, is_same_user) => {
          isBusy.value = true
          busyMessage.value = message
          busyInstanceId.value = instance_id
          isSameUser.value = is_same_user
          pendingMessage.value = content // 保存用户消息用于排队成功后发送
          // 不需要 isProcessing = false，因为这是正常流程，用户可以选择排队
        },
        subagent,
        instanceId
      )
    } catch (err) {
      error.value = (err as Error).message
      addProgress(`❌ 连接错误: ${(err as Error).message}`, 'error')
      isProcessing.value = false
    }
  }

  function getToolDisplayName(toolName: string, toolArgs: object): string {
    switch (toolName) {
      case 'web_search': {
        const keyword = (toolArgs as any)?.keyword || ''
        return `网络搜索「${keyword.slice(0, 20)}...」`
      }
      case 'email_send': {
        const to = (toolArgs as any)?.to || ''
        return `发送邮件至「${to}」`
      }
      case 'email_read': {
        const folder = (toolArgs as any)?.folder || 'INBOX'
        const limit = (toolArgs as any)?.limit || 10
        return `读取邮件（${folder}，${limit}封）`
      }
      case 'content_generate': {
        const contentType = (toolArgs as any)?.content_type || ''
        return `生成内容（${contentType}）`
      }
      case 'browser_open': {
        const url = (toolArgs as any)?.url || ''
        return `打开网页「${url.slice(0, 30)}...」`
      }
      case 'delegate_to_subagent': {
        const subagentName = (toolArgs as any)?.subagent_name || ''
        return `调用${subagentName}子智能体`
      }
      case 'skill_execute': {
        const skill = (toolArgs as any)?.skill || ''
        return `执行技能「${skill}」`
      }
      case 'use_skill': {
        const skillName = (toolArgs as any)?.skill || ''
        return `加载技能「${skillName}」`
      }
      case 'file_read': {
        const filePath = (toolArgs as any)?.file_path || ''
        return `读取文件「${filePath}」`
      }
      case 'doc_summarize':
        return '总结文档'
      case 'doc_translate': {
        const target = (toolArgs as any)?.target_lang || ''
        return `翻译文档为${target}`
      }
      case 'ocr_image': {
        const imagePath = (toolArgs as any)?.image_path || ''
        return `识别图片文字「${imagePath}」`
      }
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

  /**
   * 预先缓存一个新建的空会话（跳过后续 DB 请求）
   */
  function precacheNewSession(sessionId: string) {
    console.log(`[${now()}] [precacheNewSession] precache empty session:`, sessionId)
    sessionMessagesCache.set(sessionId, [])
  }

  /**
   * 清除指定会话的缓存（在 SSE 完成后由 watcher 调用，确保下次切回加载最新数据）
   */
  function clearSessionCache(sid?: string) {
    const targetSid = sid || sessionId.value
    if (targetSid) {
      sessionMessagesCache.delete(targetSid)
    }
  }

  /**
   * 中止当前正在进行的流式响应
   */
  async function abortStreaming() {
    console.log(`[${now()}] [abortStreaming] called, isProcessing=`, isProcessing.value, 'sessionId=', sessionId.value)
    if (isProcessing.value && sessionId.value) {
      console.log(`[${now()}] [abortStreaming] aborting current connection and notify backend`)
      // 标记为用户主动取消，让 onComplete 显示取消状态
      _cancelledByUser = true
      // 先断开前端连接
      sseManager.disconnect()
      // 通知后端取消生成，避免继续消耗token
      try {
        const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
        await fetch(`${apiBase}/chat/${encodeURIComponent(sessionId.value)}/cancel`, {
          method: 'POST',
          headers: getEffectiveAuthHeader()
        })
        console.log(`[${now()}] [abortStreaming] backend cancel request sent`)
      } catch (err) {
        console.warn('[abortStreaming] failed to notify backend:', err)
        // 即使后端通知失败，前端仍然中止
      }
      isProcessing.value = false
      console.log(`[${now()}] [abortStreaming] done, isProcessing=`, isProcessing.value)
    }
  }

  /**
   * 加入排队队列
   */
  async function joinQueue(instanceId: string): Promise<boolean> {
    try {
      const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
      const response = await fetch(`${apiBase}/chat/instances/${instanceId}/lock`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getEffectiveAuthHeader()
        },
        body: JSON.stringify({ session_id: sessionId.value })
      })
      const result = await response.json()
      if (result.is_queued) {
        return true
      }
      return false
    } catch (err) {
      console.error('joinQueue error:', err)
      return false
    }
  }

  /**
   * 取消繁忙状态（不排队）
   */
  function cancelBusy() {
    isBusy.value = false
    busyMessage.value = ''
    busyInstanceId.value = ''
    pendingMessage.value = ''
    isProcessing.value = false
  }

  /**
   * 结束当前会话，释放实例锁
   */
  async function endSession(instanceId: string): Promise<boolean> {
    try {
      const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
      const response = await fetch(`${apiBase}/chat/instances/${instanceId}/release`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...getEffectiveAuthHeader()
        },
        body: JSON.stringify({ session_id: sessionId.value })
      })
      const result = await response.json()
      if (result.success) {
        // 清理状态
        isProcessing.value = false
        return true
      }
      return false
    } catch (err) {
      console.error('endSession error:', err)
      return false
    }
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
    switchSession,
    clearSessionCache,
    precacheNewSession,
    uploadAttachment,
    removeAttachment,
    clearAttachments,
    abortStreaming,
    // 排队相关
    isBusy,
    busyMessage,
    busyInstanceId,
    isSameUser,
    pendingMessage,
    joinQueue,
    cancelBusy,
    endSession
  }
}
