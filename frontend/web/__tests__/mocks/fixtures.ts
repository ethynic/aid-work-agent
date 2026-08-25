/**
 * 测试数据工厂
 *
 * 提供创建测试数据的工具函数
 */
import type { ChatMessage, ProgressMessage } from '@/types'

export function createChatMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    role: 'user',
    content: 'Test message',
    timestamp: Date.now(),
    ...overrides
  }
}

export function createProgressMessage(overrides: Partial<ProgressMessage> = {}): ProgressMessage {
  return {
    type: 'progress',
    content: 'Test progress',
    timestamp: Date.now(),
    ...overrides
  }
}

export function createUser(overrides: Record<string, any> = {}) {
  return {
    user_id: 'u1',
    username: 'TestUser',
    phone: '13800138000',
    ...overrides
  }
}

export function createSession(overrides: Record<string, any> = {}) {
  return {
    session_id: 's1',
    user_id: 'u1',
    title: 'Test Session',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides
  }
}
