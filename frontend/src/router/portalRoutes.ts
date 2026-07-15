import type { RouteRecordRaw } from 'vue-router'

export const portalRoutes: RouteRecordRaw[] = [
  { path: '/subagents', name: 'subagents', component: () => import('@/components/DigitalEmployeeManager.vue') },
  { path: '/portal/login', name: 'portal-login', component: () => import('@/components/saas/TenantLogin.vue') },
  { path: '/portal/reset-password', name: 'portal-reset-password', component: () => import('@/components/saas/ResetPassword.vue') },
  {
    path: '/portal',
    component: () => import('@/components/saas/PortalLayout.vue'),
    children: [
      { path: '', name: 'portal-dashboard', component: () => import('@/components/saas/TenantDashboard.vue') },
      { path: 'tenants', name: 'portal-tenants', component: () => import('@/components/saas/TenantMgmt.vue') },
      { path: 'subagents', name: 'portal-subagents', component: () => import('@/components/DigitalEmployeeManager.vue') },
      { path: 'agent-definitions', name: 'portal-agent-definitions', component: () => import('@/components/AgentDefinitionManager.vue') },
      { path: 'token-usage', name: 'portal-token-usage', component: () => import('@/components/saas/PlatformTokenUsage.vue') },
      { path: 'error-logs', name: 'portal-error-logs', component: () => import('@/components/saas/ErrorLogs.vue') },
      { path: 'reply-styles', name: 'portal-reply-styles', component: () => import('@/components/saas/SystemReplyStyleManager.vue') },
      { path: 'monitoring', name: 'portal-monitoring', component: () => import('@/components/saas/TraceBrowser.vue') },
      { path: 'monitoring/:session_id', name: 'portal-session-traces', component: () => import('@/components/saas/SessionTraces.vue') },
      { path: 'monitoring/trace/:trace_id', name: 'portal-trace-detail', component: () => import('@/components/saas/TraceDetail.vue') },
      { path: 'context-compression', name: 'portal-context-compression', component: () => import('@/components/saas/ContextCompressionManager.vue') },
      { path: 'rpa-bindings', name: 'portal-rpa-bindings', component: () => import('@/components/saas/RpaBindingPanel.vue') },
      { path: 'redis-cache', name: 'portal-redis-cache', component: () => import('@/components/saas/RedisCacheManager.vue') },
    ]
  }
]
