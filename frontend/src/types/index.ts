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

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: number
  progressMessages?: ProgressMessage[]  // 执行详情（不传给模型，只用于显示）
  attachments?: AttachmentInfo[]  // 附件列表
  downloadableFiles?: DownloadableFile[]  // 可下载文件列表
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
  | { type: 'cancelled'; timestamp: number }

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
