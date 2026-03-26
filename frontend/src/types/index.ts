export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: number
  progressMessages?: ProgressMessage[]  // 执行详情（不传给模型，只用于显示）
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
  files?: File[]
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
  title: string
  context_data?: Record<string, any>
  created_at: string
  updated_at: string
}
