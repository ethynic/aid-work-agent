const { contextBridge, ipcRenderer } = require('electron') as typeof import('electron')

/**
 * Preload —— 暴露给 renderer 的窄 IPC 桥。
 * 不暴露原始 ipcRenderer，只暴露预定义的方法。
 */

const config = Object.freeze({
  load: () => ipcRenderer.invoke('client:config:load'),
  save: (cfg: unknown) => ipcRenderer.invoke('client:config:save', cfg),
  clear: () => ipcRenderer.invoke('client:config:clear'),
})

const cli = Object.freeze({
  activate: (code: string, serverUrl: string, clientName?: string) =>
    ipcRenderer.invoke('client:cli:activate', code, serverUrl, clientName),
  getCredits: (serverUrl: string, accessToken: string) =>
    ipcRenderer.invoke('client:cli:getCredits', serverUrl, accessToken),
  collect: (associations: string[], outputPath: string, serverUrl: string, accessToken: string) =>
    ipcRenderer.invoke('client:cli:collect', associations, outputPath, serverUrl, accessToken),
  onEvent: (callback: (event: unknown) => void) => {
    const listener = (_event: unknown, evt: unknown) => callback(evt)
    ipcRenderer.on('client:cli:event', listener)
    return () => ipcRenderer.removeListener('client:cli:event', listener)
  },
  onClose: (callback: (code: number) => void) => {
    const listener = (_event: unknown, code: number) => callback(code)
    ipcRenderer.on('client:cli:close', listener)
    return () => ipcRenderer.removeListener('client:cli:close', listener)
  },
  kill: () => ipcRenderer.invoke('client:cli:kill'),
})

const system = Object.freeze({
  openPath: (filePath: string) => ipcRenderer.invoke('client:system:openPath', filePath),
  selectOutputFile: (defaultName: string) =>
    ipcRenderer.invoke('client:system:selectOutputFile', defaultName),
  openExternal: (url: string) => ipcRenderer.invoke('client:system:openExternal', url),
})

const runtime = Object.freeze({
  platform: process.platform,
  versions: Object.freeze({
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  }),
})

contextBridge.exposeInMainWorld(
  'associationClient',
  Object.freeze({ version: 1 as const, runtime, config, cli, system }),
)
