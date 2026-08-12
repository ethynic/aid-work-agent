import { createWebHistory } from 'vue-router'

import { bootstrapApplication } from '@/app/bootstrap'
import { mountDesktopConnectivity, readDesktopRuntime, renderSecureStartupFailure, runDesktopSmoke } from '@/platform/desktopRuntime'
import { clearCredentialMemory, hydrateCredentialStore } from '@/platform/credentialStore'
import { configureRuntime } from '@/platform/runtime'
import { desktopRoutes, isDesktopRouteRejected } from '@/router/desktopRoutes'

const desktopRuntime = readDesktopRuntime()
configureRuntime(desktopRuntime)

async function startDesktopApplication(): Promise<void> {
  await hydrateCredentialStore()
  bootstrapApplication({
    history: createWebHistory(),
    routes: desktopRoutes,
    rejectRoute: isDesktopRouteRejected
  })

  const disposeConnectivity = mountDesktopConnectivity(desktopRuntime.apiBaseUrl)
  window.addEventListener('beforeunload', () => {
    disposeConnectivity()
    clearCredentialMemory()
  }, { once: true })
  if (desktopRuntime.smokeMode) void runDesktopSmoke(desktopRuntime.apiBaseUrl)
}

void startDesktopApplication().catch(() => {
  console.error('Agent Desktop secure startup failed')
  renderSecureStartupFailure()
  if (desktopRuntime.smokeMode) document.title = 'AGENT_DESKTOP_SMOKE_FAIL:secure-startup'
})
