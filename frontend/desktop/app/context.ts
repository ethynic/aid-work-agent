import type { InjectionKey } from 'vue'
import type { DesktopController } from './controller'

export const desktopControllerKey: InjectionKey<DesktopController> = Symbol('desktop-controller')
