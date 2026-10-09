import { createApp } from 'vue'
import DesktopApp from './DesktopApp.vue'
import RuntimeApp from './RuntimeApp.vue'
import { decodeResponse, isDescription, isHostState, isPluginList } from './features/runtime/contracts'
import { desktopControllerKey } from './app/context'
import { createDesktopController } from './app/controller'
import { createDesktopRouter } from './router'
import { runDesktopSmoke } from './smoke'
import './styles/tokens.css'
import './styles/desktop.css'

const bridge = window.agentDesktop
if (!bridge || bridge.version !== 3 || bridge.runtime.target !== 'desktop') throw new Error('Agent Desktop bridge is unavailable or incompatible')
document.documentElement.dataset.platform = bridge.runtime.platform
const root = document.querySelector('#app')
if (root) root.setAttribute('data-desktop-app', '')
if (bridge.runtime.productKind === 'runtime') {
  createApp(RuntimeApp).mount('#app')
  if (bridge.runtime.smokeMode) void (async () => {
    try {
      if (!bridge.runtimeHost) throw new Error('runtime-port-missing')
      for (const [method, valid] of [['describe', isDescription], ['getState', isHostState], ['plugins.list', isPluginList]] as const) {
        const request = { request_id: crypto.randomUUID(), method, params: {} }
        const response = decodeResponse(await bridge.runtimeHost.request(request), request)
        if (response.code !== 0 || !valid(response.result)) throw new Error('runtime-management-failed')
      }
      document.title = 'AGENT_DESKTOP_SMOKE_PASS'
    } catch { document.title = 'AGENT_DESKTOP_SMOKE_FAIL:runtime-management' }
  })()
} else {
  const controller = createDesktopController(bridge as Parameters<typeof createDesktopController>[0])
  createApp(DesktopApp).provide(desktopControllerKey, controller).use(createDesktopRouter()).mount('#app')
  void controller.start()
  if (bridge.runtime.smokeMode) void runDesktopSmoke(bridge.runtime.apiBaseUrl)
}
