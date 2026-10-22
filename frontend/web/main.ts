import { createWebHistory } from 'vue-router'

import { bootstrapApplication } from '@/app/bootstrap'
import { configureRuntime } from '@/platform/runtime'
import { agentRoutes } from '@/router/agentRoutes'
import { shareRoutes } from '@/router/shareRoutes'
import { portalRoutes } from '@/router/portalRoutes'
import { notificationRoutes } from '@/notifications/routes'
import { initializeWebNotifications } from '@/notifications/client'

configureRuntime({
  target: 'web',
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '/api'
})

initializeWebNotifications()

bootstrapApplication({
  history: createWebHistory(),
  routes: [...agentRoutes, ...portalRoutes, ...notificationRoutes, ...shareRoutes]
})
