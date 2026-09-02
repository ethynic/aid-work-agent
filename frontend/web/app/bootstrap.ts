import { createApp } from 'vue'
import type { RouterHistory, RouteRecordRaw } from 'vue-router'
import { createRouter } from 'vue-router'
import Toast from 'vue-toastification'
import 'vue-toastification/dist/index.css'

import App from '@/App.vue'
import { useTenantAuth } from '@/composables/useTenantAuth'
import { useTheme } from '@/composables/useTheme'
import '@/style.css'

interface BootstrapOptions {
  history: RouterHistory
  routes: RouteRecordRaw[]
  rejectRoute?: (path: string) => boolean
}

export function createApplicationRouter(options: BootstrapOptions) {
  const router = createRouter({
    history: options.history,
    routes: options.routes
  })

  router.beforeEach(async (to) => {
    const path = to.path
    if (options.rejectRoute?.(path)) {
      return '/'
    }
    if (path.endsWith('/login') || path.endsWith('/reset-password')) {
      return
    }
    if (path.startsWith('/t/') || path.startsWith('/portal')) {
      const { init, isInitialized } = useTenantAuth()
      if (!isInitialized.value) {
        await init()
      }
    }
  })

  return router
}

export function bootstrapApplication(options: BootstrapOptions): void {
  const { initTheme } = useTheme()
  initTheme()

  const router = createApplicationRouter(options)
  createApp(App).use(router).use(Toast, {
    position: 'top-center',
    timeout: 5000,
    maxToasts: 3,
    closeOnClick: true,
    pauseOnFocusLoss: true,
    pauseOnHover: true,
    draggable: true,
    draggablePercent: 0.6,
    showCloseButtonOnHover: false,
    hideProgressBar: false,
    closeButton: 'button',
    icon: true,
    rtl: false,
    transition: {
      enter: 'fade-enter',
      exit: 'fade-exit',
      move: 'fade-move',
    }
  }).mount('#app')
}
