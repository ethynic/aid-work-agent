import type { UploadedFile } from '@/api/agent'

export interface AttachmentInfo {
  file_id: string
  name: string
  size: number
  mime_type: string
  type: 'image' | 'file'
}

export interface DownloadableFile {
  file_id: string
  file_name: string
  file_size: number
  download_url: string
  mime_type?: string
}

/** 图片资产引用（与后端 src/core/image_asset.py 的 ImageRef 对齐） */
export interface ImageRef {
  file_id: string
  download_url: string
  display_name: string
  width?: number
  height?: number
  mime_type: string
  size_bytes: number
  source: 'knowledge_base' | 'tool_generated' | 'user_upload' | 'web_fetch' | 'screenshot'
  source_ref?: string
  usage: 'inline' | 'attachment' | 'embedded' | 'thumbnail'
  /** 渲染位置：after_text（默认）/ before_text / inline */
  placement: 'after_text' | 'before_text' | 'inline'
  linked_doc_id?: number
  linked_chunk_id?: number
}

/** 提交失败态（纯内存，不持久化；失败轮次本就未落库，刷新即失）。
 *  POST /api/chat/runners 失败时挂到乐观 assistant 占位消息上，
 *  提交被接受后随乐观组一起被 runner 视图替换而消失。 */
export interface SubmitFailureState {
  /** definitive：4xx 已放弃自动重试；retrying：网络/5xx 仍在指数退避自动重试 */
  kind: 'definitive' | 'retrying'
  /** 展示给用户的错误文案（与 runnerError 同一归约） */
  message: string
  /** 已自动重试次数（仅 retrying 态有意义） */
  attempts: number
  /** 手动重试：复用原 client_request_id 与原 body 幂等重放（函数字段，不进序列化路径） */
  retry: () => void
}

export interface ChatMessage {
  messageId?: string
  runnerId?: string
  clientRequestId?: string
  queueOrder?: number
  role: 'user' | 'assistant'
  content: string
  timestamp?: number
  progressMessages?: ProgressMessage[]  // 执行详情（不传给模型，只用于显示）
  attachments?: AttachmentInfo[]  // 附件列表
  downloadableFiles?: DownloadableFile[]  // 可下载文件列表
  images?: ImageRef[]  // Agent 推送的图片列表（Phase 2 P2.4）
  /** 用户可见中间消息（设计 §8.2/§10，后端持久化于 assistant metadata.verboseMessages；
   *  Phase 2 裁决：前端默认不消费/不渲染，仅声明形状供历史恢复使用） */
  verboseMessages?: VerboseMessage[]
  browserAssistance?: BrowserHumanAssistance
  /** Safe verification notice on the original assistant message. */
  waitingNotice?: string
  quickOptions?: QuickOption[]  // 编号选择按钮（§5.1 选择交互；纯前端增强，不持久化到历史）
  /** 用户主动取消的轮次（后端持久化于 assistant metadata.cancelled，历史加载时显示"用户取消"标记） */
  cancelled?: boolean
  /** 所属 runner 仍在执行（queued/running/finalizing）：执行中在气泡下展示最近
   *  工具活动作为过程反馈，终态后收起（完整执行详情仍仅 debug 模式展开） */
  runnerActive?: boolean
  /** 提交失败态（纯内存，不持久化）：runner POST 失败时挂乐观 assistant 消息 */
  submitFailure?: SubmitFailureState
}

/** 编号选择元数据（设计 §5.1 选择交互）：工具结果 data.options，前端渲染编号按钮 */
export interface QuickOption {
  key: string
  label: string
  description?: string
}

export interface BrowserHumanAssistance {
  assistance_id: string
  run_id: string
  continuation_id: string
  reason_code: string
  surface: 'server_web'
  title: string
  steps: string[]
  completion_mode: 'auto_or_confirm' | 'confirm_only'
  completion_status: string
  expires_at: string
  state?: 'pending' | 'controlling' | 'resume_queued' | 'resumed' | 'cancelled' | 'failed'
  missing_conditions?: string[]
  /** Proven availability of this exact native wait's observation endpoint. */
  view_available?: boolean
}

export interface ProgressMessage {
  type: 'progress' | 'complete' | 'error' | 'thinking' | 'tool_start' | 'tool_result'
  content: string
  timestamp: number
  toolName?: string      // 工具名称
  displayName?: string   // 后端提供的用户友好名称
  toolCallId?: string    // 同一轮并行工具调用的唯一标识
  toolArgs?: object      // 工具参数
  result?: any           // 工具执行结果（仅 tool_result 类型）
  success?: boolean      // 是否成功（仅 tool_result 类型）
}

/** 用户可见中间消息（verbose，设计 §4 最小事件结构；每轮最多一条，与技术 progress 隔离） */
export interface VerboseMessage {
  eventId: string
  data: string
  source: 'policy' | 'system'
  timestamp: number
}

export interface SendMessageRequest {
  message: string
  session_id: string
  files?: UploadedFile[]
}

export interface SendMessageResponse {
  session_id: string
  success: boolean
  message?: string
}

export type MessageStreamEvent =
  | { type: 'connected'; session_id: string; timestamp: number }
  | { type: 'progress'; data: string; timestamp: number }
  | { type: 'response'; data: string; timestamp: number }
  | { type: 'complete'; timestamp: number }
  | { type: 'error'; data: string; timestamp: number }
  | { type: 'tool_start'; toolName: string; displayName?: string; toolCallId?: string; toolArgs: object; timestamp: number }
  | { type: 'tool_result'; toolName: string; displayName?: string; toolCallId?: string; result: any; success: boolean; timestamp: number }
  | { type: 'thinking'; data: string; timestamp: number }
  | { type: 'verbose'; eventId: string; data: string; source: 'policy' | 'system'; timestamp: number }
  | { type: 'clarification'; subagentName: string; question: string; timestamp: number }
  | { type: 'busy'; flag: string; message: string; instance_id: string; is_same_user: boolean; current_user_name: string }
  | { type: 'images'; images: ImageRef[]; placement: 'after_text' | 'before_text' | 'inline'; timestamp: number }
  | { type: 'cancelled'; timestamp: number }
  | ({ type: 'browser_human_required'; timestamp?: number } & BrowserHumanAssistance)
  | { type: 'browser_resume_started'; run_id: string; seq?: number }
  | { type: 'agent_continuation_started'; continuation_id: string; seq?: number }
  | { type: 'agent_continuation_completed'; continuation_id: string; seq?: number }
  | { type: 'agent_continuation_available'; continuation_id: string }

// "正在输入"提示状态
export type InputHintState = 'idle' | 'thinking' | 'working' | 'responding'

// 用户相关类型
export interface User {
  user_id: string
  username: string
  phone?: string
  avatar_url?: string
}

// 会话相关类型
export interface Session {
  session_id: string
  user_id: string
  tenant_id?: string
  subagent_id?: string
  title: string
  context_data?: Record<string, any>
  created_at: string
  updated_at: string
}
