/**
 * 租户作用域 localStorage key 解析
 *
 * 背景：平台管理员可同时打开多个租户前台 tab，同源 localStorage 共享会导致
 * 多个租户共用同一份 saas_token / saas_admin / saas_tenant，造成身份串号。
 *
 * 解决：按当前路由把 saas_* 三个 key 解析为 per-tenant 形式，避免冲突。
 *
 * 路由 → key 映射：
 *   /portal/*                → portal_token / portal_admin / portal_tenant（平台管理后台共用）
 *   /t/:tenant_id/...        → saas_token_{tid} / saas_admin_{tid} / saas_tenant_{tid}
 *   其他（演示模式 /）        → saas_token / saas_admin / saas_tenant（保持原 key）
 */

export type SaasBaseKey = 'saas_token' | 'saas_admin' | 'saas_tenant'

/**
 * 根据当前路由返回 localStorage 使用的 key。
 * 租户前台路由下，三个 key 会带 `_{tenant_id}` 后缀，互不冲突。
 */
export function getTenantScopedKey(base: SaasBaseKey): string {
  const path = window.location.pathname
  if (path.startsWith('/portal')) {
    // 平台管理后台共用 portal_token，不区分租户
    return base.replace('saas_', 'portal_')
  }
  const m = path.match(/^\/t\/([^/]+)/)
  if (!m) {
    // 演示模式 /，保持原 saas_* key
    return base
  }
  // URL 解码 + 安全字符白名单，防止路径注入污染 localStorage key
  const safeId = decodeURIComponent(m[1]).replace(/[^a-zA-Z0-9_-]/g, '_')
  return `${base}_${safeId}`
}

/**
 * 取出当前路由下的 token。若无则返回 null。
 * 供不依赖 useTenantAuth 单例的纯 fetch 工具使用。
 */
export function readTenantToken(): string | null {
  return localStorage.getItem(getTenantScopedKey('saas_token'))
}
