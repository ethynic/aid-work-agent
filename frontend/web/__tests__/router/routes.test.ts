import { createMemoryHistory, createRouter, type RouteRecordRaw } from 'vue-router'
import { describe, expect, it } from 'vitest'

import { agentRoutes } from '@/router/agentRoutes'
import { desktopRoutes, isDesktopRouteRejected } from '@/router/desktopRoutes'
import { portalRoutes } from '@/router/portalRoutes'
import { configureRuntime, getRuntime } from '@/platform/runtime'

function childPaths(routes: RouteRecordRaw[], parentPath: string): string[] {
  return routes.find((route) => route.path === parentPath)?.children?.map((route) => route.path) ?? []
}

describe('entry route responsibilities', () => {
  it('keeps the Web top-level route contract while removing the ineffective duplicate matcher', () => {
    const webRoutes = [...agentRoutes, ...portalRoutes]

    // demo 顶层路由已随 demo 模式移除删除，agentRoutes 只剩 / 与 /t/:tenant_id，
    // /subagents（数字员工管理）收进 portalRoutes
    expect(webRoutes.map((route) => route.path)).toMatchInlineSnapshot(`
      [
        "/",
        "/t/:tenant_id",
        "/subagents",
        "/portal/login",
        "/portal/reset-password",
        "/portal",
      ]
    `)

    const router = createRouter({ history: createMemoryHistory(), routes: webRoutes })
    const portalSubagents = router.resolve('/portal/subagents')
    expect(portalSubagents.name).toBe('portal-subagents')
    expect(portalSubagents.matched.map((record) => record.path)).toEqual(['/portal', '/portal/subagents'])
    expect(router.getRoutes().filter((route) => route.name === 'portal-subagents')).toHaveLength(1)
    expect(router.resolve('/subagents').name).toBe('subagents')
  })

  it('keeps the Web Portal and tenant nested path contracts', () => {
    expect(childPaths(portalRoutes, '/portal')).toMatchInlineSnapshot(`
      [
        "",
        "tenants",
        "recharge",
        "subagents",
        "agent-definitions",
        "token-usage",
        "client-logs",
        "error-logs",
        "behavior-logs",
        "reply-styles",
        "monitoring",
        "monitoring/:session_id",
        "monitoring/trace/:trace_id",
        "context-compression",
        "rpa-bindings",
        "redis-cache",
      ]
    `)
    expect(childPaths(agentRoutes, '/t/:tenant_id')).toMatchInlineSnapshot(`
      [
        "",
        "login",
        "reset-password",
        "users",
        "knowledge",
        "connections",
        "channels",
        "wecom-personal-rpa",
        "settings",
        "local-tools",
        "daily-report",
        "work-outcomes",
        "my-agents",
        "extras",
        "agent/:subagent_name/prompt",
        "chat",
        "chat/:subagent",
        "all-sessions",
        "behavior-logs",
        "scheduled-tasks",
        "token-usage",
        "recharge-records",
        "reply-styles",
        "external-customers",
        "data-sources",
        "social-media",
        "assets",
        "videos",
        "prompts",
        "trade-specialist",
        "travel-consultant",
        "recruiting-operator",
        "customer-followup",
        "complaint",
        "after-sales",
      ]
    `)
  })

  it('keeps recruiting operator list pages and routed detail pages for tenant', () => {
    // 职位/简历均为「纯列表页 + 路由化详情页」结构：详情子路由跟在列表子路由后
    // （demo 顶层路由已随 demo 模式移除删除，仅保留租户前台路由）
    const tenantChildren = agentRoutes.find((route) => route.path === '/t/:tenant_id')?.children ?? []
    const recruiting = tenantChildren.find((route) => route.path === 'recruiting-operator')
    expect(recruiting?.children?.map((route) => route.path)).toEqual([
      'resumes',
      'resumes/:resumeId',
      'jobs',
      'jobs/:jobId',
    ])
    expect(recruiting?.children?.map((route) => route.name)).toEqual([
      'tenant-recruiting-operator-resumes',
      'tenant-recruiting-operator-resume-detail',
      'tenant-recruiting-operator-jobs',
      'tenant-recruiting-operator-job-detail',
    ])

    // 详情路径可被路由正确解析（页面跳转用 path 拼接）
    const router = createRouter({ history: createMemoryHistory(), routes: agentRoutes })
    expect(router.resolve('/t/acme/recruiting-operator/jobs/job-1').name).toBe('tenant-recruiting-operator-job-detail')
    expect(router.resolve('/t/acme/recruiting-operator/resumes/12').name).toBe('tenant-recruiting-operator-resume-detail')
  })

  // 该用例含动态 import TenantLayout 与完整 router 构建，全量并发下会超过默认 5s，显式放宽
  it('keeps Agent and tenant routes in Desktop without importing a Portal route', { timeout: 20_000 }, async () => {
    // demo 顶层路由（/chat/:subagent、/knowledge-base 等）已随 demo 模式移除删除，
    // desktopRoutes = agentRoutes（/ 与 /t/:tenant_id）+ not-found 兜底
    const desktopTopLevelPaths = desktopRoutes.map((route) => route.path)
    expect(desktopTopLevelPaths).toContain('/')
    expect(desktopTopLevelPaths).toContain('/t/:tenant_id')
    expect(desktopTopLevelPaths).toContain('/:pathMatch(.*)*')
    expect(desktopTopLevelPaths.some((path) => path === '/portal' || path.startsWith('/portal/'))).toBe(false)
    expect(desktopTopLevelPaths).not.toContain('/subagents')
    expect(childPaths(desktopRoutes, '/t/:tenant_id')).toEqual(childPaths(agentRoutes, '/t/:tenant_id'))
    expect(isDesktopRouteRejected('/portal')).toBe(true)
    expect(isDesktopRouteRejected('/portal/tenants')).toBe(true)
    expect(isDesktopRouteRejected('/t/portal')).toBe(false)

    const router = createRouter({ history: createMemoryHistory(), routes: desktopRoutes })
    expect(router.resolve('/portal').matched.map((record) => record.name)).toEqual(['desktop-not-found'])
    expect(router.resolve('/portal/tenants').matched.map((record) => record.name)).toEqual(['desktop-not-found'])
    expect(router.resolve('/subagents').matched.map((record) => record.name)).toEqual(['desktop-not-found'])
    expect(router.resolve('/t/acme/chat').name).toBe('tenant-chat-explicit')

    const tenantLayoutLoader = agentRoutes.find((route) => route.path === '/t/:tenant_id')?.component
    expect(typeof tenantLayoutLoader).toBe('function')
    const tenantLayout = await (tenantLayoutLoader as () => Promise<{ default: { __name?: string } }>)()
    expect(tenantLayout.default.__name).toBe('TenantLayout')

    await router.push('/unknown-desktop-path')
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('keeps the runtime contract explicit without migrating existing API modules', () => {
    configureRuntime({ target: 'desktop', apiBaseUrl: 'https://agent.example.test/api' })
    expect(getRuntime()).toEqual({ target: 'desktop', apiBaseUrl: 'https://agent.example.test/api' })

    configureRuntime({ target: 'web', apiBaseUrl: '/api' })
    expect(getRuntime()).toEqual({ target: 'web', apiBaseUrl: '/api' })
  })
})
