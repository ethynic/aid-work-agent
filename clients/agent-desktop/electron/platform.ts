import type { MenuItemConstructorOptions } from 'electron'

export function resolvePlatformWindowOptions(platform: NodeJS.Platform): Readonly<Record<string, unknown>> {
  return platform === 'darwin'
    ? { titleBarStyle: 'hiddenInset', trafficLightPosition: { x: 16, y: 15 } }
    : {}
}

export function applicationMenuTemplate(platform: NodeJS.Platform): MenuItemConstructorOptions[] {
  const common: MenuItemConstructorOptions[] = [{ role: 'fileMenu' }, { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' }]
  return platform === 'darwin' ? [{ role: 'appMenu' }, ...common] : common
}

export function shouldQuitWhenAllWindowsClosed(platform: NodeJS.Platform): boolean {
  return platform !== 'darwin'
}
