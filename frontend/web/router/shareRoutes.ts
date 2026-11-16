import type { RouteRecordRaw } from 'vue-router'

/** Public Web reader; deliberately outside tenant layouts and the Desktop entry. */
export const shareRoutes: RouteRecordRaw[] = [
  { path: '/share/reply/:share_key', name: 'shared-reply', component: () => import('@/components/share/SharedReplyPage.vue') },
  { path: '/share/task-result/:share_key', name: 'shared-task-result', component: () => import('@/components/share/TaskResultSharePage.vue') },
  // 官网客服 C 端聊天页（免登录，ext 签名 / 匿名两种身份，设计 docs/system/web-c-channel-design.md §4.5）
  { path: '/c/:tenant_id/:config_id', name: 'web-c-chat', component: () => import('@/components/webC/WebCChat.vue') },
]
