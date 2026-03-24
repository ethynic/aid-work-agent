/**
 * 会话状态管理
 */

import { ref } from 'vue'
import {
  listSessions,
  createSession,
  deleteSession,
  updateSession,
  getSessionMessages,
  addSessionMessage,
  type ChatSession,
  type ChatMessage
} from '@/api/session'
import { useAuth } from './useAuth'

const sessions = ref<ChatSession[]>([])
const currentSessionId = ref<string | null>(null)
const isLoading = ref(false)

export function useSession() {
  const { isLoggedIn } = useAuth()

  /**
   * 加载会话列表
   */
  async function loadSessions() {
    if (!isLoggedIn.value) return

    isLoading.value = true
    try {
      const result = await listSessions()
      sessions.value = result.sessions || []
      // 按更新时间倒序
      sessions.value.sort((a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      )
    } catch (e) {
      console.error('Failed to load sessions:', e)
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 创建新会话
   */
  async function createNewSession(title?: string): Promise<ChatSession | null> {
    if (!isLoggedIn.value) return null

    try {
      const newSession = await createSession({ title })
      sessions.value.unshift(newSession)
      return newSession
    } catch (e) {
      console.error('Failed to create session:', e)
      return null
    }
  }

  /**
   * 删除会话
   */
  async function removeSession(sessionId: string) {
    try {
      await deleteSession(sessionId)
      sessions.value = sessions.value.filter(s => s.session_id !== sessionId)
      // 如果删除的是当前会话，清除当前会话
      if (currentSessionId.value === sessionId) {
        currentSessionId.value = null
      }
    } catch (e) {
      console.error('Failed to delete session:', e)
    }
  }

  /**
   * 更新会话标题
   */
  async function renameSession(sessionId: string, title: string) {
    try {
      const updated = await updateSession(sessionId, { title })
      const index = sessions.value.findIndex(s => s.session_id === sessionId)
      if (index !== -1) {
        sessions.value[index] = updated
      }
    } catch (e) {
      console.error('Failed to rename session:', e)
    }
  }

  /**
   * 选择会话
   */
  function selectSession(sessionId: string | null) {
    currentSessionId.value = sessionId
  }

  /**
   * 获取会话消息历史
   */
  async function loadSessionMessages(sessionId: string): Promise<ChatMessage[]> {
    try {
      const result = await getSessionMessages(sessionId)
      return result.messages || []
    } catch (e) {
      console.error('Failed to load messages:', e)
      return []
    }
  }

  /**
   * 保存消息到会话
   */
  async function saveMessage(sessionId: string, role: string, content: string) {
    try {
      await addSessionMessage(sessionId, role, content)
      // 更新会话的更新时间
      const index = sessions.value.findIndex(s => s.session_id === sessionId)
      if (index !== -1) {
        sessions.value[index].updated_at = new Date().toISOString()
      }
    } catch (e) {
      console.error('Failed to save message:', e)
    }
  }

  /**
   * 清除当前会话
   */
  function clearCurrentSession() {
    currentSessionId.value = null
  }

  return {
    sessions,
    currentSessionId,
    isLoading,
    loadSessions,
    createNewSession,
    removeSession,
    renameSession,
    selectSession,
    loadSessionMessages,
    saveMessage,
    clearCurrentSession
  }
}
