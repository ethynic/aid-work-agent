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

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: number
  progressMessages?: ProgressMessage[]  // 执行详情（不传给模型，只用于显示）
  attachments?: AttachmentInfo[]  // 附件列表
  downloadableFiles?: DownloadableFile[]  // 可下载文件列表
  images?: ImageRef[]  // Agent 推送的图片列表（Phase 2 P2.4）
  browserAssistance?: BrowserHumanAssistance
  quickOptions?: QuickOption[]  // 编号选择按钮（§5.1 选择交互；纯前端增强，不持久化到历史）
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
}

export interface ProgressMessage {
  type: 'progress' | 'complete' | 'error' | 'thinking' | 'tool_start' | 'tool_result'
  content: string
  timestamp: number
  toolName?: string      // 工具名称
  toolArgs?: object      // 工具参数
  result?: any           // 工具执行结果（仅 tool_result 类型）
  success?: boolean      // 是否成功（仅 tool_result 类型）
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
  | { type: 'tool_start'; toolName: string; toolArgs: object; timestamp: number }
  | { type: 'tool_result'; toolName: string; result: any; success: boolean; timestamp: number }
  | { type: 'thinking'; data: string; timestamp: number }
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
