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

    expect(webRoutes.map((route) => route.path)).toMatchInlineSnapshot(`
      [
        "/",
        "/chat/:subagent",
        "/customer-info",
        "/scheduled-tasks",
        "/knowledge-base",
        "/my-agents",
        "/all-sessions",
        "/data-sources",
        "/social-media",
        "/trade-specialist",
        "/travel-consultant",
        "/customer-followup",
        "/complaint",
        "/after-sales",
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
        "subagents",
        "agent-definitions",
        "token-usage",
        "error-logs",
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
        "channels",
        "wecom-personal-rpa",
        "settings",
        "my-agents",
        "agent/:subagent_name/prompt",
        "chat",
        "chat/:subagent",
        "all-sessions",
        "token-usage",
        "reply-styles",
        "external-customers",
        "data-sources",
        "social-media",
        "trade-specialist",
        "travel-consultant",
        "customer-followup",
        "complaint",
        "after-sales",
      ]
    `)
  })

  it('keeps Agent and tenant routes in Desktop without importing a Portal route', async () => {
    const desktopTopLevelPaths = desktopRoutes.map((route) => route.path)
    expect(desktopTopLevelPaths).toContain('/')
    expect(desktopTopLevelPaths).toContain('/chat/:subagent')
    expect(desktopTopLevelPaths).toContain('/knowledge-base')
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
    expect(router.resolve('/chat/customer-service').name).toBe('chat-subagent')
    expect(router.resolve('/trade-specialist/customers').name).toBe('trade-specialist-customers')
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
