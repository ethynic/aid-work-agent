import type { RouteRecordRaw } from 'vue-router'

/** Public Web reader; deliberately outside tenant layouts and the Desktop entry. */
export const shareRoutes: RouteRecordRaw[] = [
  { path: '/share/reply/:share_key', name: 'shared-reply', component: () => import('@/components/share/SharedReplyPage.vue') },
]
