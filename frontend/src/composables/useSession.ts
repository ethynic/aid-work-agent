/**
 * 会话状态管理
 */

import { ref, computed } from 'vue'
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
import { useDemoAuth } from './useDemoAuth'
import { useTenantAuth } from './useTenantAuth'

const sessions = ref<ChatSession[]>([])
const currentSessionId = ref<string | null>(null)
const isLoading = ref(false)
const currentPage = ref(1)
const totalSessions = ref(0)
const pageSize = ref(20)

// 检查是否已登录（考虑租户模式）
function checkIsLoggedIn(): boolean {
  const { isLoggedIn: normalLoggedIn } = useDemoAuth()
  const { isLoggedIn: tenantLoggedIn, saasToken: _saasToken } = useTenantAuth()

  // 租户模式：检查 saas_token
  if (window.location.pathname.startsWith('/t/')) {
    return tenantLoggedIn.value
  }
  // 演示模式：检查 demo_token
  return normalLoggedIn.value
}

export function useSession() {

  /**
   * 加载会话列表
   */
  async function loadSessions(page: number = 1) {
    if (!checkIsLoggedIn()) {
      return
    }

    isLoading.value = true
    try {
      const result = await listSessions(page, pageSize.value)
      let loadedSessions = result.sessions || []

      // 过滤掉创建超过5分钟仍然是默认标题的空会话
      const now = new Date().getTime()
      loadedSessions = loadedSessions.filter(s => {
        const createdTime = new Date(s.created_at).getTime()
        const isEmptyTitle = s.title === '新会话' || !s.title
        const isOldEmpty = isEmptyTitle && (now - createdTime) > 5 * 60000 // 超过5分钟

        return !isOldEmpty
      })

      sessions.value = loadedSessions
      totalSessions.value = result.total
      currentPage.value = result.page
      pageSize.value = result.page_size
    } catch (e) {
      console.error('Failed to load sessions:', e)
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 创建新会话
   * 在创建前自动清理空会话（标题为"新会话"且未发送任何消息的会话）
   */
  async function createNewSession(title?: string, subagent?: string | null): Promise<ChatSession | null> {
    if (!checkIsLoggedIn()) return null

    try {
      // 自动清理：创建新会话前，删除已有的空会话（标题为默认"新会话"且没有消息）
      // 这些会话是用户点击"新会话"后又立即点击"新会话"产生的，没有实际内容
      const emptySessions = sessions.value.filter(s =>
        (s.title === '新会话' || !s.title) &&
        // 如果是默认标题且是最新创建的，认为是空会话
        new Date().getTime() - new Date(s.created_at).getTime() < 60000 // 1分钟内创建的
      )

      // 先从列表中移除，再在后台并行删除（不阻塞创建新会话）
      for (const empty of emptySessions) {
        console.log(`[Cleanup] 计划删除空会话 ${empty.session_id} - 未发送任何消息`)
        sessions.value = sessions.value.filter(s => s.session_id !== empty.session_id)
      }
      // 后台并行删除，不需要阻塞创建新会话
      if (emptySessions.length > 0) {
        Promise.all(emptySessions.map(empty =>
          removeSession(empty.session_id).catch(err =>
            console.error(`[Cleanup] 删除空会话 ${empty.session_id} 失败:`, err)
          )
        )).then(() => {
          console.log(`[Cleanup] 完成批量删除，共 ${emptySessions.length} 个空会话`)
        })
      }

      // 创建新会话
      const newSession = await createSession({
        title,
        ...(subagent ? { context_data: { subagent }, subagent_id: subagent } : {})
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
    console.log('[selectSession] called, sessionId=', sessionId, 'previous currentSessionId=', currentSessionId.value)
    currentSessionId.value = sessionId
    console.log('[selectSession] done, currentSessionId now=', currentSessionId.value)
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
   * 跳转到指定页
   */
  async function goToPage(page: number) {
    if (page < 1 || page > totalPages.value) return
    await loadSessions(page)
  }

  /**
   * 加载并自动选择最近会话
   */
  async function loadLatestSession(): Promise<boolean> {
    if (!checkIsLoggedIn()) {
      return false
    }

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

  // 计算总页数
  const totalPages = computed(() => Math.ceil(totalSessions.value / pageSize.value))

  return {
    sessions,
    currentSessionId,
    isLoading,
    currentPage,
    totalSessions,
    pageSize,
    totalPages,
    loadSessions,
    goToPage,
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
