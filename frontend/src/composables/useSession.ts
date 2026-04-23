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
  getLatestSession,
  getSessionRecords,
  getSessionTokenUsage,
  type ChatSession,
  type ChatMessage,
  type ChatRecord,
  type TokenUsage
} from '@/api/session'
import { useAuth } from './useAuth'
import { useTenantAuth } from './useTenantAuth'

const sessions = ref<ChatSession[]>([])
const currentSessionId = ref<string | null>(null)
const isLoading = ref(false)

// 检查是否已登录（考虑租户模式）
function checkIsLoggedIn(): boolean {
  const { isLoggedIn: normalLoggedIn } = useAuth()
  const { isLoggedIn: tenantLoggedIn, saasToken } = useTenantAuth()

  // 租户模式：检查 saas_token
  if (window.location.pathname.startsWith('/t/')) {
    return tenantLoggedIn.value
  }
  // 普通模式：检查 demo_token
  return normalLoggedIn.value
}

export function useSession() {
  const { isLoggedIn } = useAuth()

  /**
   * 加载会话列表
   */
  async function loadSessions() {
    if (!checkIsLoggedIn()) return

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
  async function createNewSession(title?: string, subagent?: string | null): Promise<ChatSession | null> {
    if (!checkIsLoggedIn()) return null

    try {
      const newSession = await createSession({
        title,
        ...(subagent ? { context_data: { subagent } } : {})
      })
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
      // 同步更新会话的 updated_at 时间到后端
      const index = sessions.value.findIndex(s => s.session_id === sessionId)
      if (index !== -1) {
        sessions.value[index].updated_at = new Date().toISOString()
        await updateSession(sessionId, {})
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

  /**
   * 加载并自动选择最近会话
   */
  async function loadLatestSession(): Promise<boolean> {
    if (!isLoggedIn.value) return false

    try {
      const result = await getLatestSession()
      if (result.session) {
        currentSessionId.value = result.session.session_id
        // 确保sessions列表中包含该会话
        const exists = sessions.value.find(s => s.session_id === result.session!.session_id)
        if (!exists) {
          sessions.value.unshift(result.session)
        }
        return true
      }
      return false
    } catch (e) {
      console.error('Failed to load latest session:', e)
      return false
    }
  }

  /**
   * 获取会话的所有记录
   */
  async function loadSessionRecords(sessionId: string): Promise<ChatRecord[]> {
    try {
      const result = await getSessionRecords(sessionId)
      return result.records || []
    } catch (e) {
      console.error('Failed to load records:', e)
      return []
    }
  }

  /**
   * 获取会话的Token消耗
   */
  async function loadSessionTokenUsage(sessionId: string): Promise<TokenUsage | null> {
    try {
      return await getSessionTokenUsage(sessionId)
    } catch (e) {
      console.error('Failed to load token usage:', e)
      return null
    }
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
    clearCurrentSession,
    loadLatestSession,
    loadSessionRecords,
    loadSessionTokenUsage
  }
}
