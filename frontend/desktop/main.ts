import { createApp } from 'vue'
import DesktopApp from './DesktopApp.vue'
import { desktopControllerKey } from './app/context'
import { createDesktopController } from './app/controller'
import { createDesktopRouter } from './router'
import { runDesktopSmoke } from './smoke'
import './styles/tokens.css'
import './styles/desktop.css'

const bridge = window.agentDesktop
if (!bridge || bridge.version !== 3 || bridge.runtime.target !== 'desktop') throw new Error('Agent Desktop bridge is unavailable or incompatible')
document.documentElement.dataset.platform = bridge.runtime.platform
const controller = createDesktopController(bridge as Parameters<typeof createDesktopController>[0])
const root = document.querySelector('#app')
if (root) root.setAttribute('data-desktop-app', '')
createApp(DesktopApp).provide(desktopControllerKey, controller).use(createDesktopRouter()).mount('#app')
void controller.start()
if (bridge.runtime.smokeMode) void runDesktopSmoke(bridge.runtime.apiBaseUrl)
