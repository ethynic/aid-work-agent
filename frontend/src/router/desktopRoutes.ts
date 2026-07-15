import type { RouteRecordRaw } from 'vue-router'

import { agentRoutes } from './agentRoutes'

export function isDesktopRouteRejected(path: string): boolean {
  return path === '/portal' || path.startsWith('/portal/')
}

export const desktopRoutes: RouteRecordRaw[] = [
  ...agentRoutes,
  { path: '/:pathMatch(.*)*', name: 'desktop-not-found', redirect: '/' }
]
