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

// 重新部署后旧页面持有旧 hash chunk，懒加载路由会 404，整页重载加载新 index.html 即恢复
const STALE_CHUNK_RELOAD_KEY = 'router:stale_chunk_reload_at'
const STALE_CHUNK_RELOAD_COOLDOWN_MS = 5000

export function isStaleChunkImportError(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    msg.includes('Failed to fetch dynamically imported module') ||
    // Firefox 的报错文案
    msg.includes('error loading dynamically imported module') ||
    // Safari 的报错文案
    msg.includes('Importing a module script failed')
  )
}

export function createApplicationRouter(options: BootstrapOptions) {
  const router = createRouter({
    history: options.history,
    routes: options.routes
  })

  router.onError((error, to) => {
    if (!isStaleChunkImportError(error)) {
      return
    }
    // 冷却期内不重复重载，防止新资源也缺失时陷入 reload 循环
    const lastReloadAt = Number(sessionStorage.getItem(STALE_CHUNK_RELOAD_KEY) || 0)
    if (Date.now() - lastReloadAt < STALE_CHUNK_RELOAD_COOLDOWN_MS) {
      return
    }
    sessionStorage.setItem(STALE_CHUNK_RELOAD_KEY, String(Date.now()))
    window.location.assign(to.fullPath)
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
