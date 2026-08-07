// @ts-check
const { contextBridge, ipcRenderer } = require('electron')

/**
 * Preload —— 暴露给 renderer 的窄 IPC 桥。
 * 不暴露原始 ipcRenderer，只暴露预定义的方法。
 *
 * 注意：本文件复制为 preload.cjs 后由 Node.js 直接执行，
 * 必须是纯 CommonJS + 纯 JS（不能有 TS 语法）。
 */

const config = Object.freeze({
  load: () => ipcRenderer.invoke('client:config:load'),
  save: (cfg) => ipcRenderer.invoke('client:config:save', cfg),
  clear: () => ipcRenderer.invoke('client:config:clear'),
})

const cli = Object.freeze({
  activate: (code, serverUrl, clientName) =>
    ipcRenderer.invoke('client:cli:activate', code, serverUrl, clientName),
  getCredits: (serverUrl, accessToken) =>
    ipcRenderer.invoke('client:cli:getCredits', serverUrl, accessToken),
  getCreditsDetail: (serverUrl, accessToken) =>
    ipcRenderer.invoke('client:cli:getCreditsDetail', serverUrl, accessToken),
  collect: (associations, outputPath, serverUrl, accessToken, inputPath) =>
    ipcRenderer.invoke('client:cli:collect', associations, outputPath, serverUrl, accessToken, inputPath),
  onEvent: (callback) => {
    const listener = (_event, evt) => {
      console.log('[preload] event received:', evt.event)
      callback(evt)
    }
    ipcRenderer.on('client:cli:event', listener)
    return () => ipcRenderer.removeListener('client:cli:event', listener)
  },
  onClose: (callback) => {
    const listener = (_event, code) => callback(code)
    ipcRenderer.on('client:cli:close', listener)
    return () => ipcRenderer.removeListener('client:cli:close', listener)
  },
  kill: () => ipcRenderer.invoke('client:cli:kill'),
})

const system = Object.freeze({
  openPath: (filePath) => ipcRenderer.invoke('client:system:openPath', filePath),
  openFolder: (filePath) => ipcRenderer.invoke('client:system:openFolder', filePath),
  selectOutputFile: (defaultName) =>
    ipcRenderer.invoke('client:system:selectOutputFile', defaultName),
  selectInputFile: () => ipcRenderer.invoke('client:system:selectInputFile'),
  getDesktopPath: () => ipcRenderer.invoke('client:system:getDesktopPath'),
  openExternal: (url) => ipcRenderer.invoke('client:system:openExternal', url),
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
  Object.freeze({ version: 1, runtime, config, cli, system }),
)
