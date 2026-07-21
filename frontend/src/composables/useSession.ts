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
  type ChatMessageRecord,
  type ChatRecord,
  type TokenUsage
} from '@/api/session'
import { useDemoAuth } from './useDemoAuth'
import { useTenantAuth } from './useTenantAuth'

const sessions = ref<ChatSession[]>([])
const currentSessionId = ref<string | null>(null)
const isLoading = ref(false)
const isLoaded = ref(false)    // 是否已加载完成（避免重复请求）
const hasAuthError = ref(false) // 是否发生认证错误（401），用于防止重复请求
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
   * 加载会话列表（带缓存，避免重复请求）
   * @param page 页码
   * @param forceRefresh 是否强制刷新，默认 false
   */
  async function loadSessions(page?: number, forceRefresh = false) {
    // 如果 page 未指定，使用当前已保存的页码（避免 route 变化时覆盖用户选择的页码）
    const targetPage = page !== undefined ? page : currentPage.value

    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      return
    }

    if (!checkIsLoggedIn()) {
      return
    }

    // 已有数据且不强制刷新，且请求的页码和每页条数都没变，直接返回
    if (isLoaded.value && !forceRefresh && !isLoading.value && targetPage === currentPage.value) {
      return
    }

    // 避免并发重复请求
    if (isLoading.value) {
      return
    }

    isLoading.value = true
    try {
      const result = await listSessions(targetPage, pageSize.value)
      const loadedSessions = result.sessions || []

      sessions.value = loadedSessions
      totalSessions.value = result.total
      currentPage.value = result.page
      pageSize.value = result.page_size
      isLoaded.value = true
      // 成功加载后重置认证错误标志
      hasAuthError.value = false
    } catch (e: any) {
      console.error('Failed to load sessions:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected, stopping further session requests')
        // 触发登出或重新认证流程
        triggerAuthError()
      }
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 创建新会话
   * 在创建前自动清理空会话（标题为"新会话"且未发送任何消息的会话）
   */
  async function createNewSession(title?: string, subagent?: string | null): Promise<ChatSession | null> {
    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      console.warn('Skipping createNewSession due to previous auth error')
      return null
    }

    if (!checkIsLoggedIn()) return null

    // 余额检查：仅在租户前台模式下生效，余额 ≤ 0 阻断创建会话
    if (window.location.pathname.startsWith('/t/')) {
      try {
        const { useCreditCheck } = await import('./useCreditCheck')
        const { checkCreditBeforeAction } = useCreditCheck()
        const creditCheck = await checkCreditBeforeAction('newSession')
        if (!creditCheck.allowed) {
          return null
        }
      } catch (e) {
        // 余额检查异常不阻断创建会话主流程
        console.warn('[useSession] 创建会话前余额检查失败:', e)
      }
    }

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
      // 成功创建后重置认证错误标志
      hasAuthError.value = false
      return newSession
    } catch (e: any) {
      console.error('Failed to create session:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected in createNewSession')
        triggerAuthError()
      }
      return null
    }
  }

  /**
   * 删除会话
   * @returns 是否删除成功
   */
  async function removeSession(sessionId: string): Promise<boolean> {
    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      console.warn('Skipping removeSession due to previous auth error')
      return false
    }

    try {
      await deleteSession(sessionId)
      sessions.value = sessions.value.filter(s => s.session_id !== sessionId)
      // 如果删除的是当前会话，清除当前会话
      if (currentSessionId.value === sessionId) {
        currentSessionId.value = null
      }
      // 成功删除后重置认证错误标志
      hasAuthError.value = false
      return true
    } catch (e: any) {
      console.error('Failed to delete session:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected in removeSession')
        triggerAuthError()
      } else if (e.status === 404) {
        // 会话不存在，从本地列表中移除（可能是缓存或并发删除）
        sessions.value = sessions.value.filter(s => s.session_id !== sessionId)
        console.warn('Session not found, removed from local list:', sessionId)
      }
      return false
    }
  }

  /**
   * 更新会话标题
   */
  async function renameSession(sessionId: string, title: string) {
    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      console.warn('Skipping renameSession due to previous auth error')
      return
    }

    try {
      const updated = await updateSession(sessionId, { title })
      const index = sessions.value.findIndex(s => s.session_id === sessionId)
      if (index !== -1) {
        sessions.value[index] = updated
      }
      // 成功后重置认证错误标志
      hasAuthError.value = false
    } catch (e: any) {
      console.error('Failed to rename session:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected in renameSession')
        triggerAuthError()
      }
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
  async function loadSessionMessages(sessionId: string): Promise<ChatMessageRecord[]> {
    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      console.warn('Skipping loadSessionMessages due to previous auth error')
      return []
    }

    try {
      const result = await getSessionMessages(sessionId)
      // 成功后重置认证错误标志
      hasAuthError.value = false
      return result.messages || []
    } catch (e: any) {
      console.error('Failed to load messages:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected in loadSessionMessages')
        triggerAuthError()
      }
      return []
    }
  }

  /**
   * 保存消息到会话
   */
  async function saveMessage(sessionId: string, role: string, content: string) {
    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      console.warn('Skipping saveMessage due to previous auth error')
      return
    }

    try {
      await addSessionMessage(sessionId, role, content)
      // 同步更新会话的 updated_at 时间到后端
      const index = sessions.value.findIndex(s => s.session_id === sessionId)
      if (index !== -1) {
        sessions.value[index].updated_at = new Date().toISOString()
        await updateSession(sessionId, {})
      }
      // 成功后重置认证错误标志
      hasAuthError.value = false
    } catch (e: any) {
      console.error('Failed to save message:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected in saveMessage')
        triggerAuthError()
      }
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
    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      console.warn('Skipping loadLatestSession due to previous auth error')
      return false
    }

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
        // 成功后重置认证错误标志
        hasAuthError.value = false
        return true
      }
      return false
    } catch (e: any) {
      console.error('Failed to load latest session:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected in loadLatestSession')
        triggerAuthError()
      }
      return false
    }
  }

  /**
   * 获取会话的所有记录
   */
  async function loadSessionRecords(sessionId: string): Promise<ChatRecord[]> {
    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      console.warn('Skipping loadSessionRecords due to previous auth error')
      return []
    }

    try {
      const result = await getSessionRecords(sessionId)
      // 成功后重置认证错误标志
      hasAuthError.value = false
      return result.records || []
    } catch (e: any) {
      console.error('Failed to load records:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected in loadSessionRecords')
        triggerAuthError()
      }
      return []
    }
  }

  /**
   * 获取会话的Token消耗
   */
  async function loadSessionTokenUsage(sessionId: string): Promise<TokenUsage | null> {
    // 如果已经发生认证错误，不再尝试请求
    if (hasAuthError.value) {
      console.warn('Skipping loadSessionTokenUsage due to previous auth error')
      return null
    }

    try {
      const result = await getSessionTokenUsage(sessionId)
      // 成功后重置认证错误标志
      hasAuthError.value = false
      return result
    } catch (e: any) {
      console.error('Failed to load token usage:', e)
      // 检查是否为401认证错误
      if (e.status === 401 || e.message?.includes('401')) {
        hasAuthError.value = true
        console.warn('Authentication error (401) detected in loadSessionTokenUsage')
        triggerAuthError()
      }
      return null
    }
  }

  // 计算总页数
  const totalPages = computed(() => Math.ceil(totalSessions.value / pageSize.value))

  /**
   * 触发认证错误处理
   */
  function triggerAuthError() {
    // 清除缓存
    clearSessionCache()
    // 重置认证错误标志，以便下次登录后可重新尝试
    hasAuthError.value = false

    // 根据当前模式触发相应的登出逻辑
    if (window.location.pathname.startsWith('/t/')) {
      // 租户模式：尝试清除租户认证
      try {
        const { logout: tenantLogout } = useTenantAuth()
        tenantLogout()
      } catch (e) {
        console.error('Failed to trigger tenant logout:', e)
      }
    } else {
      // 演示模式：尝试清除演示认证
      try {
        const { logout: demoLogout } = useDemoAuth()
        demoLogout()
      } catch (e) {
        console.error('Failed to trigger demo logout:', e)
      }
    }
  }

  /**
   * 清空缓存（重新登录或切换租户时调用）
   */
  function clearSessionCache() {
    sessions.value = []
    currentSessionId.value = null
    isLoaded.value = false
    isLoading.value = false
    hasAuthError.value = false
    currentPage.value = 1
    totalSessions.value = 0
  }

  return {
    sessions,
    currentSessionId,
    isLoading,
    isLoaded,
    hasAuthError,
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
    loadSessionTokenUsage,
    clearSessionCache,
    triggerAuthError,
  }
}
