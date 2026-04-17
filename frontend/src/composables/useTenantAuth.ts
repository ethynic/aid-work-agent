/**
 * SaaS 租户管理员认证状态管理
 * 独立于普通用户认证（useAuth），使用 saas_token
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

export function useTenantAuth() {
  const isLoggedIn = computed(() => !!saasToken.value && !!admin.value)

  /**
   * 从 localStorage 恢复认证状态
   */
  async function init() {
    if (isInitialized.value) return

    isLoading.value = true
    try {
      const savedToken = localStorage.getItem('saas_token')
      const savedAdmin = localStorage.getItem('saas_admin')
      const savedTenant = localStorage.getItem('saas_tenant')

      if (savedToken) {
        saasToken.value = savedToken

        // 验证 token 有效性
        const info = await getAdminInfo()
        if (info?.admin && info?.tenant) {
          admin.value = info.admin
          tenant.value = info.tenant
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
   */
  function setLogin(token: string, adminInfo: TenantAdmin, tenantInfo: TenantInfo) {
    saasToken.value = token
    admin.value = adminInfo
    tenant.value = tenantInfo
    localStorage.setItem('saas_token', token)
    localStorage.setItem('saas_admin', JSON.stringify(adminInfo))
    localStorage.setItem('saas_tenant', JSON.stringify(tenantInfo))
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
    const t = saasToken.value || localStorage.getItem('saas_token')
    if (t) {
      return { 'Authorization': `Bearer ${t}` }
    }
    return {}
  }

  function clearStorage() {
    localStorage.removeItem('saas_token')
    localStorage.removeItem('saas_admin')
    localStorage.removeItem('saas_tenant')
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
