export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: number
}

export interface ProgressMessage {
  type: 'progress' | 'complete' | 'error' | 'thinking'
  content: string
  timestamp: number
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
