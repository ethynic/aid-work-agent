import { createWebHistory } from 'vue-router'

import { bootstrapApplication } from '@/app/bootstrap'
import { configureRuntime } from '@/platform/runtime'
import { agentRoutes } from '@/router/agentRoutes'
import { portalRoutes } from '@/router/portalRoutes'

configureRuntime({
  target: 'web',
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '/api'
})

bootstrapApplication({
  history: createWebHistory(),
  routes: [...agentRoutes, ...portalRoutes]
})
