import { ref, computed, shallowReactive, type Ref } from 'vue'
import type { ChatMessage, InputHintState, ProgressMessage, VerboseMessage } from '@/types'
import { SSEManager, uploadFile, type UploadedFile } from '@/api/agent'
import { getSessionMessages } from '@/api/session'
import { extractQuickOptions } from '@/utils/quickOptions'
import { useToast } from 'vue-toastification'
import { useTenantAuth } from './useTenantAuth'
import { useCreditCheck } from './useCreditCheck'
import { useVideoGenParams } from './useVideoGenParams'

/**
 * 多会话后台流式架构
 *
 * 每个会话拥有独立的 SessionStreamState（独立 SSEManager + 消息缓冲），
 * 切换会话只是改变 sessionId（当前查看的会话），后台会话的 SSE 连接保持存活继续收流。
 * 导出的 messages/isProcessing 等是指向「当前查看会话状态」的 computed 代理，
 * 消费组件无需感知状态池的存在。
 *
 * 设计文档：docs/system/multi-session-background-streaming-design.md
 */
interface SessionStreamState {
  sseManager: SSEManager
  messages: Ref<ChatMessage[]>
  progressMessages: Ref<ProgressMessage[]>
  isProcessing: Ref<boolean>
  currentResponse: Ref<string>
  inputHintState: Ref<InputHintState>
  /** 本轮 live verbose 中间提示（Phase 2，设计 §8.2；每轮最多一条，按 eventId 覆盖） */
  liveVerbose: Ref<VerboseMessage | null>
  /** 后台完成且用户尚未查看 → 会话列表显示完成小点 */
  hasUnreadCompletion: Ref<boolean>
  /** 是否已从 DB 加载过历史消息（或已有实时消息），避免重复请求 */
  dbLoaded: boolean
  /** 用户主动取消标记（区分 abort 和正常完成） */
  cancelledByUser: boolean
}

// 会话状态池。必须用 shallowReactive：reactive 会深层包装状态对象并 unwrap 内部 ref，
// 导致 state.messages.value 访问失效；shallowReactive 只追踪 Map 的增删，值保持原样。
const sessionStreams = shallowReactive(new Map<string, SessionStreamState>())

// 当前查看的会话 ID
const sessionId = ref<string>(generateSessionId())

// 当前查看会话的错误（仅活动会话的错误才写入，避免后台会话错误污染当前视图）
const error = ref<string | null>(null)

// 当前附件列表（输入框草稿，跨会话共享，与现状一致）
const currentFiles = ref<UploadedFile[]>([])

function generateSessionId(): string {
  return 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9)
}

/**
 * 获取（或懒创建）指定会话的流式状态
 */
function getStreamState(sid: string): SessionStreamState {
  let state = sessionStreams.get(sid)
  if (!state) {
    state = {
      sseManager: new SSEManager(),
      messages: ref<ChatMessage[]>([]),
      progressMessages: ref<ProgressMessage[]>([]),
      isProcessing: ref(false),
      currentResponse: ref(''),
      inputHintState: ref<InputHintState>('idle'),
      liveVerbose: ref<VerboseMessage | null>(null),
      hasUnreadCompletion: ref(false),
      dbLoaded: false,
      cancelledByUser: false,
    }
    sessionStreams.set(sid, state)
  }
  return state
}

// ---- 指向当前查看会话状态的 computed 代理（保持原有导出 API 不变） ----

const messages = computed<ChatMessage[]>({
  get: () => getStreamState(sessionId.value).messages.value,
  set: (v) => { getStreamState(sessionId.value).messages.value = v }
})
const progressMessages = computed<ProgressMessage[]>({
  get: () => getStreamState(sessionId.value).progressMessages.value,
  set: (v) => { getStreamState(sessionId.value).progressMessages.value = v }
})
const isProcessing = computed<boolean>(() => getStreamState(sessionId.value).isProcessing.value)
const currentResponse = computed<string>(() => getStreamState(sessionId.value).currentResponse.value)
// 当前查看会话的 live verbose 中间提示（每会话独立，多会话互不串扰）
const liveVerbose = computed<VerboseMessage | null>(() => getStreamState(sessionId.value).liveVerbose.value)
const inputHintState = computed<InputHintState>({
  get: () => getStreamState(sessionId.value).inputHintState.value,
  set: (v) => { getStreamState(sessionId.value).inputHintState.value = v }
})

export function useAgent() {
  const isWaitingHuman = computed(() => messages.value.some(message => {
    const state = message.browserAssistance?.state
    return state === 'pending' || state === 'controlling' || state === 'resume_queued'
  }))
  // 根据路由获取认证头（租户前台自动带 X-Tenant-Id）
  function getEffectiveAuthHeader(): Record<string, string> {
    const { getAuthHeader } = useTenantAuth()
    return getAuthHeader()
  }

  function now(): string {
    const d = new Date()
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}:${d.getSeconds().toString().padStart(3, '0')}.${d.getMilliseconds().toString().padStart(3, '0')}`
  }

  /**
   * 切换会话：不再中断任何流式连接，仅改变当前查看的会话。
   * 目标会话若已有内存状态（进行中/已完成/已加载），computed 代理自动显示其实时内容；
   * 否则从数据库加载历史消息到该会话的状态中。
   */
  async function switchSession(newSessionId: string): Promise<void> {
    sessionId.value = newSessionId
    // 清除上一会话的错误提示，避免残留到目标会话视图（保持旧版 switchSession 行为）
    error.value = null

    const state = getStreamState(newSessionId)
    // 用户查看了该会话，清除完成小点
    state.hasUnreadCompletion.value = false

    // 内存已有状态（进行中的实时消息或已加载的历史），无需请求 DB
    if (state.dbLoaded || state.messages.value.length > 0) {
      return
    }

    const result = await getSessionMessages(newSessionId)

    // 等待 DB 期间该会话可能已开始流式（用户快速发消息），不得覆盖实时状态
    if (state.isProcessing.value || state.messages.value.length > 0) {
      state.dbLoaded = true
      return
    }

    state.messages.value = result.messages
      ?.filter(m => {
        // 过滤过程消息：tool 角色、带 tool_calls 的 assistant 不在聊天框显示
        if (m.role === 'tool') return false
        if (m.role === 'assistant' && m.metadata?.tool_calls) return false
        return true
      })
      .map(m => ({
        role: m.role as 'user' | 'assistant',
        content: m.content,
        timestamp: new Date(m.created_at).getTime(),
        progressMessages: m.metadata?.progressMessages || [],
        attachments: m.metadata?.attachments || undefined,
        downloadableFiles: m.metadata?.downloadableFiles || undefined
      })) || []
    state.dbLoaded = true
  }

  /**
   * 上传单个文件
   * 先插入无 file_id 的占位（ChatInput 显示"上传中"转圈），成功后替换为真实结果，失败则移除
   */
  async function uploadAttachment(file: File): Promise<UploadedFile> {
    const placeholder: UploadedFile = {
      file_id: '',
      name: file.name,
      size: file.size,
      mime_type: file.type,
      type: file.type.startsWith('image/') ? 'image' : 'file'
    }
    currentFiles.value.push(placeholder)
    try {
      const uploaded = await uploadFile(file, getEffectiveAuthHeader())
      const idx = currentFiles.value.indexOf(placeholder)
      if (idx !== -1) {
        currentFiles.value.splice(idx, 1, uploaded)
      }
      return uploaded
    } catch (error) {
      currentFiles.value = currentFiles.value.filter(f => f !== placeholder)
      throw error
    }
  }

  /**
   * 移除已上传的附件
   * 空 file_id 是上传中的占位（无法取消进行中的请求），忽略
   */
  function removeAttachment(file_id: string) {
    if (!file_id) return
    currentFiles.value = currentFiles.value.filter(f => f.file_id !== file_id)
  }

  /**
   * 清空所有附件
   */
  function clearAttachments() {
    currentFiles.value = []
  }

  async function sendMessage(content: string, subagent?: string | null, overrideSessionId?: string, instanceId?: string | null) {
    if (!content.trim()) return

    // 余额检查：余额 ≤ 0 阻断发送（仅在租户前台模式下生效）
    if (window.location.pathname.startsWith('/t/')) {
      const { checkCreditBeforeAction } = useCreditCheck()
      const creditCheck = await checkCreditBeforeAction('sendMessage')
      if (!creditCheck.allowed) {
        return
      }
    }

    // 优先使用外部传入的 sessionId（来自 DB 的真实会话 ID），避免与本地生成的 sessionId 产生竞态
    const effectiveSessionId = overrideSessionId || sessionId.value
    const state = getStreamState(effectiveSessionId)

    // 按会话 guard：仅当目标会话正在处理时才拒绝，不阻塞其他会话发消息
    if (state.isProcessing.value) return

    state.dbLoaded = true

    // 构建用户消息内容（含附件信息）；防御性过滤上传中的占位（正常情况下发送按钮已禁用）
    const readyFiles = currentFiles.value.filter(f => f.file_id)
    let userContent = content.trim()
    if (readyFiles.length > 0) {
      const fileNames = readyFiles.map(f => f.name).join(', ')
      userContent += `\n\n[附件: ${fileNames}]`
    }

    // 添加用户消息
    state.messages.value.push({
      role: 'user',
      content: userContent,
      timestamp: Date.now(),
      attachments: readyFiles.length > 0 ? [...readyFiles] : undefined
    })

    // 重置状态
    state.isProcessing.value = true
    if (effectiveSessionId === sessionId.value) {
      error.value = null
    }
    state.currentResponse.value = ''
    state.progressMessages.value = []
    state.liveVerbose.value = null
    state.inputHintState.value = 'thinking'

    // 添加空的助手消息占位
    const assistantMessageIndex = state.messages.value.length
    const assistantMessage = {
      role: 'assistant' as const,
      content: '',
      timestamp: Date.now(),
      progressMessages: [] as ProgressMessage[]  // 初始化空数组
    }
    state.messages.value.push(assistantMessage)

    // 添加初始进度消息（在 AI 消息创建之后）
    addProgress(state, '🚀 正在发送请求...', 'progress')

    // 附件数据已浅拷贝到用户消息与 SSE 请求参数（下方 connect 调用），
    // 在这里清空附件输入区，确保用户看到自己消息出现的同时附件框立即清空，
    // 避免 ChatContainer.handleSend 在某些 return 分支下漏清空导致附件残留。
    const filesToSend = readyFiles.length > 0 ? [...readyFiles] : undefined
    currentFiles.value = []

    // 流结束时若是后台会话（用户正在查看其他会话），标记完成小点
    const markUnreadIfBackground = () => {
      if (effectiveSessionId !== sessionId.value) {
        state.hasUnreadCompletion.value = true
      }
    }

    try {
      await state.sseManager.connect(
        content,
        effectiveSessionId,
        filesToSend,
        getEffectiveAuthHeader(), // 传递认证头
        // onProgress - 工具执行进度，仅添加到执行详情
        (data) => {
          // 过滤冗余进度：工具开始/完成已有专门事件（tool_start/tool_result），
          // 后端额外推送的「正在执行...」和「...执行完成」会重复刷屏，这里静默丢弃。
          if (/^(🔧\s*正在执行|✅\s*.+执行完成$)/.test(data.trim())) {
            return
          }
          addProgress(state, data, 'progress')
          // 不再将进度追加到助手消息内容
        },
        // onResponse - AI响应内容
        (data) => {
          // response 开始：关闭 live verbose 占位（设计 §8.2，response 后隐藏）
          state.liveVerbose.value = null
          state.currentResponse.value += data
          if (assistantMessageIndex < state.messages.value.length) {
            state.messages.value[assistantMessageIndex].content = state.currentResponse.value
          }
          if (state.inputHintState.value !== 'responding') {
            state.inputHintState.value = 'responding'
          }
        },
        // onComplete
        () => {
          state.liveVerbose.value = null
          if (state.cancelledByUser) {
            addProgress(state, '⚠️ 已停止', 'error')
            state.cancelledByUser = false
          } else {
            addProgress(state, '✅ 任务完成', 'complete')
          }
          // 助手回复完成，把占位消息的 timestamp 更新为完成时刻，
          // 对齐后端 assistant 消息的 created_at（避免始终显示发送时刻）
          if (assistantMessageIndex < state.messages.value.length) {
            state.messages.value[assistantMessageIndex].timestamp = Date.now()
          }
          state.isProcessing.value = false
          state.inputHintState.value = 'idle'
          markUnreadIfBackground()
        },
        // onError
        (err) => {
          state.liveVerbose.value = null
          // 识别 SSE 403 NO_CREDIT 错误，显示友好 toast 提示
          const errWithCode = err as Error & { code?: string; status?: number }
          if (errWithCode.code === 'NO_CREDIT' || errWithCode.status === 403) {
            try {
              const toast = useToast()
              toast.error(errWithCode.message || '积分余额已耗尽，数字员工无法工作')
            } catch {
              // toast 不可用时降级到进度消息
            }
          }
          if (effectiveSessionId === sessionId.value) {
            error.value = err.message
          }
          addProgress(state, `❌ 错误: ${err.message}`, 'error')
          state.isProcessing.value = false
          state.inputHintState.value = 'idle'
          markUnreadIfBackground()
        },
        // onToolStart - 工具开始执行
        (toolName, toolArgs, displayName, toolCallId) => {
          const toolDisplayName = displayName || toolName
          addProgress(
            state,
            `🔧 需要调用工具【${toolDisplayName}】`,
            'tool_start',
            toolName,
            toolArgs,
            undefined,
            displayName,
            toolCallId,
          )
          state.inputHintState.value = 'working'
        },
        // onToolResult - 工具执行结果
        (toolName, result, success, displayName, toolCallId) => {
          const toolDisplayName = displayName || toolName
          if (success) {
            // 结构化消费者只依赖结果字段，不依赖生产该结果的工具名称。
            if (result?.file_id && result.visible !== false) {
              const lastMsg = state.messages.value[state.messages.value.length - 1]
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
            // 编号选择元数据：成功结果带 data.options 时挂到助手消息渲染选项按钮；
            // 纯前端增强不进历史持久化，用户手动回复数字同样有效）
            const quickOptions = extractQuickOptions(result)
            if (quickOptions.length) {
              const lastMsg = state.messages.value[state.messages.value.length - 1]
              if (lastMsg && lastMsg.role === 'assistant') {
                lastMsg.quickOptions = quickOptions
              }
            }
            addProgress(
              state, `✅ 【${toolDisplayName}】执行完成`, 'tool_result',
              toolName, undefined, result, displayName, toolCallId, true,
            )
          } else {
            const errorMsg = result?.error || '未知错误'
            addProgress(
              state, `❌ ${toolDisplayName}失败: ${errorMsg}`, 'tool_result',
              toolName, undefined, result, displayName, toolCallId, false,
            )
          }
        },
        // onThinking - LLM思考中
        (data) => {
          addProgress(state, `🤔 ${data}`, 'thinking')
          state.inputHintState.value = 'thinking'
        },
        // onClarification - 子智能体需要用户补充信息
        (subagentName, question) => {
          addProgress(state, `❓ ${subagentName}需要补充信息: ${question}`, 'tool_start', 'clarification')
        },
        // onImages - Agent 推送的图片资产（Phase 2 P2.5）
        (images, placement) => {
          if (!images || !images.length) return
          // 找到当前助手消息（最后一条）
          const lastMsg = state.messages.value[state.messages.value.length - 1]
          if (!lastMsg || lastMsg.role !== 'assistant') return
          if (!lastMsg.images) lastMsg.images = []
          // 按 file_id 去重合并（一次回复可能多次推送 images 事件）
          for (const img of images) {
            if (!img?.file_id) continue
            // 兜底 placement：后端理论上已 set，但前端容错
            const normalized = { ...img, placement: img.placement || placement || 'after_text' }
            if (!lastMsg.images.some(existing => existing.file_id === normalized.file_id)) {
              lastMsg.images.push(normalized)
            }
          }
        },
        // onBrowserHumanRequired - 结构化卡片独立于 LLM 文本渲染
        (event) => {
          state.liveVerbose.value = null
          const lastMsg = state.messages.value[state.messages.value.length - 1]
          if (lastMsg?.role === 'assistant') {
            lastMsg.browserAssistance = { ...event, state: 'pending' }
          }
          state.isProcessing.value = false
          state.inputHintState.value = 'idle'
          addProgress(state, '等待你在浏览器中完成操作', 'progress')
        },
        // onVerbose - 用户可见中间消息（Phase 2，设计 §8.2）
        // 后端每轮最多一条；这里按 eventId 覆盖去重（后来者覆盖）纯属防御，
        // 只写当前 session 状态，不进 progressMessages / debug 执行详情
        (message) => {
          if (!message?.eventId) return
          state.liveVerbose.value = message
        },
        subagent,
        instanceId,
        // 视频创作参数：仅 video-agent 子智能体使用，其他智能体忽略
        subagent === 'video-agent' ? { ...useVideoGenParams().params.value } : null
      )
    } catch (err) {
      state.liveVerbose.value = null
      if (effectiveSessionId === sessionId.value) {
        error.value = (err as Error).message
      }
      addProgress(state, `❌ 连接错误: ${(err as Error).message}`, 'error')
      state.isProcessing.value = false
      state.inputHintState.value = 'idle'
      markUnreadIfBackground()
    }
  }

  function addProgress(
    state: SessionStreamState,
    content: string,
    type: ProgressMessage['type'],
    toolName?: string,
    toolArgs?: object,
    result?: any,
    displayName?: string,
    toolCallId?: string,
    success?: boolean,
  ) {
    const newMsg: ProgressMessage = {
      type,
      content,
      timestamp: Date.now(),
      toolName,
      displayName,
      toolCallId,
      toolArgs,
      result,
      success,
    }
    state.progressMessages.value.push(newMsg)

    // 输出到前端 console，方便调试
    const ts = new Date().toLocaleTimeString()
    const label = `[Agent ${type}]`
    if (type === 'error') {
      console.error(`${ts} ${label}`, content, { toolName, toolArgs, result })
    } else if (type === 'tool_result' || type === 'tool_start') {
      console.info(`${ts} ${label}`, content, { toolName, toolArgs, result })
    } else {
      console.log(`${ts} ${label}`, content)
    }

    // 同时更新 AI 消息占位中的 progressMessages（用于 MessageItem 显示）
    const lastMsg = state.messages.value[state.messages.value.length - 1]
    if (lastMsg && lastMsg.role === 'assistant' && lastMsg.progressMessages) {
      lastMsg.progressMessages.push(newMsg)
    }
  }

  function clearMessages() {
    const state = getStreamState(sessionId.value)
    state.messages.value = []
    state.progressMessages.value = []
    state.currentResponse.value = ''
    error.value = null
  }

  function clearSession() {
    sessionId.value = generateSessionId()
    error.value = null
  }

  /**
   * 预先标记一个新建的空会话（跳过后续 DB 请求）
   */
  function precacheNewSession(sid: string) {
    console.log(`[${now()}] [precacheNewSession] precache empty session:`, sid)
    const state = getStreamState(sid)
    state.messages.value = []
    state.dbLoaded = true
  }

  /**
   * 清除会话状态缓存
   * - 传入 sid：仅清除该会话的内存状态（进行中的会话不清除），下次切回时从 DB 重新加载
   * - 不传 sid：清空全部会话状态并断开所有流式连接（用于登出/重新登录）
   */
  function clearSessionCache(sid?: string) {
    if (sid) {
      const state = sessionStreams.get(sid)
      if (state && !state.isProcessing.value) {
        sessionStreams.delete(sid)
      }
      return
    }
    for (const [, state] of sessionStreams) {
      state.sseManager.disconnect()
    }
    sessionStreams.clear()
  }

  /**
   * 删除会话时同步清理其流式状态（断开连接并从状态池移除）
   */
  function removeStreamState(sid: string) {
    const state = sessionStreams.get(sid)
    if (state) {
      state.sseManager.disconnect()
      sessionStreams.delete(sid)
    }
  }

  /**
   * 中止指定会话（默认当前查看会话）正在进行的流式响应
   */
  async function abortStreaming(targetSid?: string) {
    const sid = targetSid || sessionId.value
    const state = sessionStreams.get(sid)
    console.log(`[${now()}] [abortStreaming] called, sid=`, sid, 'isProcessing=', state?.isProcessing.value)
    if (state?.isProcessing.value) {
      // 标记为用户主动取消，让 onComplete 显示取消状态
      state.cancelledByUser = true
      // 先断开前端连接
      state.sseManager.disconnect()
      // 通知后端取消生成，避免继续消耗token
      try {
        const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
        await fetch(`${apiBase}/chat/${encodeURIComponent(sid)}/cancel`, {
          method: 'POST',
          headers: getEffectiveAuthHeader()
        })
        console.log(`[${now()}] [abortStreaming] backend cancel request sent`)
      } catch (err) {
        console.warn('[abortStreaming] failed to notify backend:', err)
        // 即使后端通知失败，前端仍然中止
      }
      state.isProcessing.value = false
      state.inputHintState.value = 'idle'
    }
  }

  /**
   * 会话列表指示器：该会话是否正在流式生成（显示旋转 loading）
   */
  function isSessionRunning(sid: string): boolean {
    return sessionStreams.get(sid)?.isProcessing.value ?? false
  }

  /**
   * 会话列表指示器：该会话是否在后台完成且用户尚未查看（显示完成小点）
   */
  function hasSessionUnreadCompletion(sid: string): boolean {
    return sessionStreams.get(sid)?.hasUnreadCompletion.value ?? false
  }

  return {
    messages,
    progressMessages,
    isProcessing,
    currentResponse,
    liveVerbose,
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
    removeStreamState,
    isSessionRunning,
    hasSessionUnreadCompletion,
    // "正在输入"提示
    inputHintState,
    isWaitingHuman
  }
}
