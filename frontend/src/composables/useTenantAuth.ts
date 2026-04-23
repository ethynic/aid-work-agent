/**
 * SaaS 租户管理员认证状态管理
 * 根据路由判断使用不同的 token key：
 * - /portal 路由使用 portal_token
 * - /t/:tenant_id 路由使用 saas_token
 */

import { ref, computed } from 'vue'
import { getAdminInfo, adminLogout as apiLogout } from '@/api/saasTenant'

export interface TenantAdmin {
  user_id: string
  phone: string
  username: string
  role: string
}

export interface TenantInfo {
  tenant_id: string
  company_name: string
  plan: string
  status: number
}

const admin = ref<TenantAdmin | null>(null)
const tenant = ref<TenantInfo | null>(null)
const saasToken = ref<string | null>(null)
const isLoading = ref(false)
const isInitialized = ref(false)

// 根据当前路由获取对应的 token key
function getTokenKey(): string {
  const path = window.location.pathname
  if (path.startsWith('/portal')) {
    return 'portal_token'
  } else if (path.startsWith('/t/')) {
    return 'saas_token'
  }
  return 'saas_token' // 默认
}

function getAdminKey(): string {
  const path = window.location.pathname
  if (path.startsWith('/portal')) {
    return 'portal_admin'
  } else if (path.startsWith('/t/')) {
    return 'saas_admin'
  }
  return 'saas_admin'
}

function getTenantKey(): string {
  const path = window.location.pathname
  if (path.startsWith('/portal')) {
    return 'portal_tenant'
  } else if (path.startsWith('/t/')) {
    return 'saas_tenant'
  }
  return 'saas_tenant'
}

export function useTenantAuth() {
  const isLoggedIn = computed(() => !!saasToken.value && !!admin.value)

  /**
   * 从 localStorage 恢复认证状态
   */
  async function init() {
    if (isInitialized.value) return

    isLoading.value = true
    try {
      const tokenKey = getTokenKey()
      const adminKey = getAdminKey()
      const tenantKey = getTenantKey()

      const savedToken = localStorage.getItem(tokenKey)
      const savedAdmin = localStorage.getItem(adminKey)
      const savedTenant = localStorage.getItem(tenantKey)

      if (savedToken) {
        saasToken.value = savedToken

        // 验证 token 有效性
        const info = await getAdminInfo()
        // 修复: 后端返回 user 而非 admin
        // 平台管理员的 tenant 可能是 null，需要分开判断
        if (info?.user) {
          admin.value = info.user
          tenant.value = info.tenant ? { ...info.tenant, status: Number(info.tenant.status) } : null
        } else {
          // token 无效，清除
          clearStorage()
          saasToken.value = null
        }
      } else if (savedAdmin && savedTenant) {
        // 无 token 但有缓存信息，清除
        clearStorage()
      }
    } catch (e) {
      console.error('TenantAuth init error:', e)
      clearStorage()
      saasToken.value = null
      admin.value = null
      tenant.value = null
    } finally {
      isLoading.value = false
      isInitialized.value = true
    }
  }

  /**
   * 设置登录状态
   * localStorage 仅存储 token 和最小化管理员标识（user_id、username、role），
   * 敏感信息（手机号等）通过 API 获取，不持久化到 localStorage。
   */
  function setLogin(token: string, adminInfo: TenantAdmin, tenantInfo: TenantInfo) {
    const tokenKey = getTokenKey()
    const adminKey = getAdminKey()
    const tenantKey = getTenantKey()

    saasToken.value = token
    admin.value = adminInfo
    tenant.value = tenantInfo
    localStorage.setItem(tokenKey, token)
    // 仅存储非敏感字段
    localStorage.setItem(adminKey, JSON.stringify({
      user_id: adminInfo.user_id,
      username: adminInfo.username,
      role: adminInfo.role,
    }))
    localStorage.setItem(tenantKey, JSON.stringify(tenantInfo))
  }

  /**
   * 登出
   */
  async function logout() {
    try {
      await apiLogout()
    } catch (e) {
      console.error('Tenant logout error:', e)
    } finally {
      saasToken.value = null
      admin.value = null
      tenant.value = null
      clearStorage()
    }
  }

  /**
   * 获取 Authorization header
   */
  function getAuthHeader(): Record<string, string> {
    const tokenKey = getTokenKey()
    const t = saasToken.value || localStorage.getItem(tokenKey)
    if (t) {
      return { 'Authorization': `Bearer ${t}` }
    }
    return {}
  }

  function clearStorage() {
    const tokenKey = getTokenKey()
    const adminKey = getAdminKey()
    const tenantKey = getTenantKey()
    localStorage.removeItem(tokenKey)
    localStorage.removeItem(adminKey)
    localStorage.removeItem(tenantKey)
  }

  return {
    admin,
    tenant,
    saasToken,
    isLoggedIn,
    isLoading,
    isInitialized,
    init,
    setLogin,
    logout,
    getAuthHeader
  }
}
