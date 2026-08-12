/**
 * 演示模式认证状态管理
 * 用于 / 路由下的演示用户身份验证，使用 demo_token
 */

import { ref, computed } from 'vue'
import { getCurrentUser, logout as apiLogout } from '@/api/auth'
import type { User as BaseUser } from '@/types'
import { credentialGet, credentialRemove, credentialSet } from '@/platform/credentialStore'

export interface User extends BaseUser {
  is_admin?: boolean
}

const user = ref<User | null>(null)
const token = ref<string | null>(null)
const isLoading = ref(false)
const isInitialized = ref(false)

export function useDemoAuth() {
  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const isAdmin = computed(() => !!user.value?.is_admin)

  /**
   * 初始化认证状态（从 localStorage 恢复）
   */
  async function init() {
    if (isInitialized.value) return

    isLoading.value = true
    try {
      const savedToken = credentialGet('demo_token')
      const savedUser = credentialGet('user_info')

      if (savedToken) {
        token.value = savedToken

        // 验证 token 是否有效
        const userInfo = await getCurrentUser()
        if (userInfo) {
          user.value = userInfo
        } else {
          // token 无效，清除
          await credentialRemove('user_info')
          await credentialRemove('demo_token')
          token.value = null
        }
      } else if (savedUser) {
        await credentialRemove('user_info')
      }
    } catch (e) {
      console.error('DemoAuth init error:', e)
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
  async function setLogin(newToken: string, userInfo: User) {
    // 仅存储非敏感字段
    await credentialSet('user_info', JSON.stringify({
      user_id: userInfo.user_id,
      username: userInfo.username,
      is_admin: userInfo.is_admin,
      avatar_url: userInfo.avatar_url,
    }))
    await credentialSet('demo_token', newToken)
    token.value = newToken
    user.value = userInfo
  }

  /**
   * 登出
   */
  async function logout() {
    await apiLogout()
    token.value = null
    user.value = null
  }

  /**
   * 获取 Authorization header
   */
  function getAuthHeader(): Record<string, string> {
    const t = token.value || credentialGet('demo_token')
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
