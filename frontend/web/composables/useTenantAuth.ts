/**
 * SaaS 租户管理员认证状态管理
 * 根据路由自动按租户隔离 localStorage 中的 saas_token / saas_admin / saas_tenant：
 * - /portal 路由使用 portal_token / portal_admin / portal_tenant
 * - /t/:tenant_id 路由使用 saas_token_{tenant_id} / saas_admin_{tenant_id} / saas_tenant_{tenant_id}
 *   （避免平台管理员同时打开多个租户 tab 时的 token 串号）
 * - 演示模式 / 保持原 saas_token / saas_admin / saas_tenant
 */

import { ref, computed } from 'vue'
import { adminLogout as apiLogout } from '@/api/saasTenant'
import { getTenantScopedKey, type SaasBaseKey } from '@/api/tenantStorage'
import { credentialGet, credentialRemove, credentialSet } from '@/platform/credentialStore'

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
  status: string
  expire_at?: string
  logo_file_id?: string | null  // 租户 Logo 文件 ID（无 Logo 时为 null）
}

const admin = ref<TenantAdmin | null>(null)
const tenant = ref<TenantInfo | null>(null)
const saasToken = ref<string | null>(null)
const isLoading = ref(false)
const isInitialized = ref(false)

// 三个 key 统一按当前路由解析：租户前台按 tenant_id 隔离，portal 共用，演示模式保持
function getTokenKey(): string {
  return getTenantScopedKey('saas_token')
}

function getAdminKey(): string {
  return getTenantScopedKey('saas_admin')
}

function getTenantKey(): string {
  return getTenantScopedKey('saas_tenant')
}

/**
 * 登录时根据 tenant_id 解析目标 key（而非当前路由）。
 *
 * 背景：UniversalLogin 在路由 / 完成登录，但跳转目标是 /t/{tenant_id} 或 /portal。
 * 若用当前路由解析 key，token 会存到 saas_token（无后缀），跳转后页面按
 * saas_token_{tenant_id} 读取，找不到 token，表现为"未登录"。
 *
 * 规则：
 * - tenant_id 非空 -> saas_token_{tenant_id}（跳 /t/{tenant_id}）
 * - tenant_id 为空 -> portal_token（跳 /portal，平台管理员回退场景）
 */
function resolveLoginKey(base: SaasBaseKey, tenantId: string | undefined): string {
  if (tenantId) {
    const safeId = tenantId.replace(/[^a-zA-Z0-9_-]/g, '_')
    return `${base}_${safeId}`
  }
  return base.replace('saas_', 'portal_')
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

      const savedToken = credentialGet(tokenKey)
      const savedAdmin = credentialGet(adminKey)
      const savedTenant = credentialGet(tenantKey)

      if (savedToken) {
        saasToken.value = savedToken

        // 从 URL 路由中提取 tenant_id（平台管理员访问租户前台时需要）
        const path = window.location.pathname
        const routeMatch = path.match(/^\/t\/([^/]+)/)
        const routeTenantId = routeMatch ? routeMatch[1] : undefined

        // 直接调用 API 验证 token，区分 401 和其他错误
        const apiBase = import.meta.env.VITE_API_BASE_URL || '/api'
        const url = routeTenantId
          ? `${apiBase}/saas/auth/me?tenant_id=${encodeURIComponent(routeTenantId)}`
          : `${apiBase}/saas/auth/me`
        const response = await fetch(url, {
          headers: getAuthHeader()
        })

        if (response.ok) {
          const info = await response.json()
          // 修复: 后端返回 user 而非 admin
          // 平台管理员的 tenant 可能是 null，需要分开判断
          if (info?.user) {
            admin.value = info.user
            tenant.value = info.tenant ? { ...info.tenant, status: info.tenant.status } : null

            // 检查租户状态和到期日期（仅对租户管理员和普通用户）
            if (tenant.value && admin.value!.role !== 'platform_admin') {
              // 检查租户状态
              if (tenant.value.status !== 'active') {
                console.warn(`Tenant ${tenant.value.tenant_id} is ${tenant.value.status}, forcing logout`)
                await clearStorage()
                saasToken.value = null
                admin.value = null
                tenant.value = null
                return
              }

              // 检查租户到期日期
              if (tenant.value.expire_at) {
                const now = new Date()
                const expireDate = new Date(tenant.value.expire_at)
                if (now > expireDate) {
                  console.warn(`Tenant ${tenant.value.tenant_id} has expired, forcing logout`)
                  await clearStorage()
                  saasToken.value = null
                  admin.value = null
                  tenant.value = null
                }
              }
            }
          } else {
            // 响应格式异常，视为 token 无效
            await clearStorage()
            saasToken.value = null
          }
        } else if (response.status === 401) {
          // token 无效，清除
          await clearStorage()
          saasToken.value = null
        } else {
          // 其他错误（如 500、网络错误），保留 token，不清除存储
          // 标记为未验证状态，但允许后续重试
          saasToken.value = savedToken
          admin.value = null
          tenant.value = null
        }
      } else if (savedAdmin && savedTenant) {
        // 无 token 但有缓存信息，清除
        await clearStorage()
      }
    } catch (e: any) {
      console.error('TenantAuth init error:', e)
      // 网络错误或异常，保留 token 不立即清除
      // 不清除 localStorage，允许重试
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
  async function setLogin(token: string, adminInfo: TenantAdmin, tenantInfo: TenantInfo) {
    // 按 tenant_id 解析目标 key（兼容登录页路由与目标路由不一致的场景）
    const tokenKey = resolveLoginKey('saas_token', tenantInfo.tenant_id)
    const adminKey = resolveLoginKey('saas_admin', tenantInfo.tenant_id)
    const tenantKey = resolveLoginKey('saas_tenant', tenantInfo.tenant_id)

    // 仅存储非敏感字段
    await credentialSet(adminKey, JSON.stringify({
      user_id: adminInfo.user_id,
      username: adminInfo.username,
      role: adminInfo.role,
    }))
    await credentialSet(tenantKey, JSON.stringify(tenantInfo))
    await credentialSet(tokenKey, token)
    saasToken.value = token
    admin.value = adminInfo
    tenant.value = tenantInfo

    // 登录成功后触发余额检查（仅提醒不阻断）
    // 平台管理员无租户属性，由 useCreditCheck 内部跳过（reason=platform_admin_skipped）
    // 异步触发，不阻塞登录主流程
    import('./useCreditCheck').then(({ useCreditCheck }) => {
      try {
        const { checkCreditBeforeAction } = useCreditCheck()
        checkCreditBeforeAction('login').catch((e) => {
          console.warn('[useTenantAuth] 登录后余额检查失败:', e)
        })
      } catch (e) {
        console.warn('[useTenantAuth] 余额检查初始化失败:', e)
      }
    })
  }

  /**
   * 登出
   */
  async function logout() {
    await apiLogout()
    saasToken.value = null
    admin.value = null
    tenant.value = null
  }

  /**
   * 获取 Authorization header + X-Tenant-Id
   */
  function getCurrentTenantId(): string | null {
    const path = window.location.pathname
    const match = path.match(/^\/t\/([^/]+)/)
    return match ? match[1] : null
  }

  /**
   * 获取认证请求头（包含 Authorization 和 X-Tenant-Id）
   */
  function getAuthHeader(): Record<string, string> {
    const tokenKey = getTokenKey()
    const t = saasToken.value || credentialGet(tokenKey)
    const headers: Record<string, string> = {}
    if (t) {
      headers['Authorization'] = `Bearer ${t}`
    }
    // 添加 X-Tenant-Id header
    const tenantId = getCurrentTenantId()
    if (tenantId) {
      headers['X-Tenant-Id'] = tenantId
    }
    return headers
  }

  async function clearStorage() {
    const tokenKey = getTokenKey()
    const adminKey = getAdminKey()
    const tenantKey = getTenantKey()
    await credentialRemove(adminKey)
    await credentialRemove(tenantKey)
    await credentialRemove(tokenKey)
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
    getCurrentTenantId,
    getAuthHeader
  }
}
