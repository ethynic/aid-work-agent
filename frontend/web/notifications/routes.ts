import type { RouteRecordRaw } from 'vue-router'
export const notificationRoutes: RouteRecordRaw[] = [
  { path: '/notification', name: 'notification-entry', component: () => import('./NotificationEntry.vue') },
  // 企微绑定 OAuth 回调落地页（wecom-app-task-notification-design §4.1，Phase 4）
  { path: '/notification/wecom-bind', name: 'wecom-bind-callback', component: () => import('./WecomBindCallback.vue') },
  { path: '/t/:tenant_id/notifications', name: 'tenant-notifications', component: () => import('@/components/saas/TenantLayout.vue'), children: [
    { path: '', component: () => import('./NotificationSettings.vue') },
  ] },
  { path: '/portal/notification-session', component: () => import('@/components/saas/PortalLayout.vue'), children: [
    { path: '', name: 'global-notification-session', component: () => import('./GlobalNotificationSession.vue') },
  ] },
  { path: '/portal/notifications', component: () => import('@/components/saas/PortalLayout.vue'), children: [
    { path: '', name: 'portal-notifications', component: () => import('./NotificationSettings.vue') },
  ] },
]
