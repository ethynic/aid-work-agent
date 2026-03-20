export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: number
}

export interface ProgressMessage {
  type: 'progress' | 'complete' | 'error'
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
}

export type MessageStreamEvent = 
  | { type: 'progress'; data: string; timestamp: number }
  | { type: 'response'; data: string; timestamp: number }
  | { type: 'complete'; timestamp: number }
  | { type: 'error'; data: string; timestamp: number }
