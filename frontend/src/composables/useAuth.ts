/**
 * 认证状态管理
 */

import { ref, computed } from 'vue'
import { getCurrentUser, logout as apiLogout } from '@/api/auth'

export interface User {
  user_id: string
  username: string
  phone?: string
  avatar_url?: string
  is_admin?: boolean
}

const user = ref<User | null>(null)
const token = ref<string | null>(null)
const isLoading = ref(false)
const isInitialized = ref(false)

export function useAuth() {
  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => !!user.value?.is_admin)

  /**
   * 初始化认证状态（从 localStorage 恢复）
   */
  async function init() {
    if (isInitialized.value) return

    isLoading.value = true
    try {
      const savedToken = localStorage.getItem('demo_token')
      const savedUser = localStorage.getItem('user_info')

      if (savedToken && savedUser) {
        token.value = savedToken
        
        // 验证 token 是否有效
        const userInfo = await getCurrentUser()
        if (userInfo) {
          user.value = userInfo
        } else {
          // token 无效，清除
          localStorage.removeItem('demo_token')
          localStorage.removeItem('user_info')
          token.value = null
        }
      }
    } catch (e) {
      console.error('Auth init error:', e)
    } finally {
      isLoading.value = false
      isInitialized.value = true
    }
  }

  /**
   * 设置登录状态
   * localStorage 仅存储 token 和最小化用户标识（user_id、is_admin），
   * 敏感信息（手机号等）通过 API 获取，不持久化到 localStorage。
   */
  function setLogin(newToken: string, userInfo: User) {
    token.value = newToken
    user.value = userInfo
    localStorage.setItem('demo_token', newToken)
    // 仅存储非敏感字段
    localStorage.setItem('user_info', JSON.stringify({
      user_id: userInfo.user_id,
      username: userInfo.username,
      is_admin: userInfo.is_admin,
      avatar_url: userInfo.avatar_url,
    }))
  }

  /**
   * 登出
   */
  async function logout() {
    try {
      await apiLogout()
    } catch (e) {
      console.error('Logout error:', e)
    } finally {
      token.value = null
      user.value = null
      localStorage.removeItem('demo_token')
      localStorage.removeItem('user_info')
    }
  }

  /**
   * 获取 Authorization header
   */
  function getAuthHeader(): Record<string, string> {
    const t = token.value || localStorage.getItem('demo_token')
    if (t) {
      return { 'Authorization': `Bearer ${t}` }
    }
    return {}
  }

  return {
    user,
    token,
    isLoggedIn,
    isAdmin,
    isLoading,
    isInitialized,
    init,
    setLogin,
    logout,
    getAuthHeader
  }
}
