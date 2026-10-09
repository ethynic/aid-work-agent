const { contextBridge, ipcRenderer } = require('electron') as typeof import('electron')

function readArgument(name: string): string | undefined {
  const prefix = `--${name}=`
  return process.argv.find((argument) => argument.startsWith(prefix))?.slice(prefix.length)
}

const apiBaseUrl = readArgument('aidagent-api-base-url') ?? ''
const runtime = Object.freeze({
  target: 'desktop' as const,
  platform: process.platform,
  schemeOrigin: 'aidagent://app',
  apiBaseUrl,
  apiOrigin: new URL(apiBaseUrl).origin,
  smokeMode: readArgument('aidagent-smoke-mode') === '1',
  productKind: readArgument('aidagent-product-kind') === 'runtime' ? 'runtime' as const : 'desktop' as const,
  productProfile: readArgument('aidagent-product-profile') === 'acceptance' ? 'acceptance' as const : 'production' as const,
  versions: Object.freeze({ electron: process.versions.electron, chrome: process.versions.chrome }),
})

const bridgeVersion = Number(readArgument('aidagent-bridge-version'))
if (bridgeVersion !== 3) throw new Error('unsupported Agent Desktop bridge version')

const startup = Object.freeze({
  getState: () => ipcRenderer.invoke('desktop:startup:get-state') as Promise<Readonly<{ secureStorageAvailable: boolean; online: boolean }>>,
})

const credentials = Object.freeze({
  hydrate: () => ipcRenderer.invoke('desktop:credentials:hydrate') as Promise<Record<string, string>>,
  set: (key: string, value: string) => ipcRenderer.invoke('desktop:credentials:set', key, value) as Promise<void>,
  delete: (key: string) => ipcRenderer.invoke('desktop:credentials:delete', key) as Promise<void>,
})
const system = Object.freeze({
  openExternal: (url: string) => ipcRenderer.invoke('desktop:open-external', url) as Promise<void>,
  saveDownload: (input: Readonly<{ url: string; suggestedName: string; authorization?: string; tenantId?: string }>) =>
    ipcRenderer.invoke('desktop:save-download', input) as Promise<Readonly<{ saved: boolean }>>,
})
type DesktopUpdateState = Readonly<{
  status: 'disabled' | 'idle' | 'checking' | 'available' | 'downloading' | 'downloaded' | 'up-to-date' | 'error'
  currentVersion: string
  availableVersion?: string
  percent?: number
  message?: string
}>
const updates = Object.freeze({
  getState: () => ipcRenderer.invoke('desktop:update:get-state') as Promise<DesktopUpdateState>,
  onState: (callback: (state: DesktopUpdateState) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, state: DesktopUpdateState) => callback(state)
    ipcRenderer.on('desktop:update:state', listener)
    return () => ipcRenderer.removeListener('desktop:update:state', listener)
  },
  check: () => ipcRenderer.invoke('desktop:update:check') as Promise<void>,
  download: () => ipcRenderer.invoke('desktop:update:download') as Promise<void>,
  restartAndInstall: () => ipcRenderer.invoke('desktop:update:restart-and-install') as Promise<void>,
})

const runtimeHost = readArgument('aidagent-runtime-enabled') === '1' ? Object.freeze({
  request: (request: unknown) => ipcRenderer.invoke('desktop:runtime:request', request) as Promise<unknown>,
  choosePackage: (input: unknown) => ipcRenderer.invoke('desktop:runtime:choose-package', input) as Promise<unknown>,
  observe: (callback: (event: unknown) => void) => {
    const listener = (_event: Electron.IpcRendererEvent, event: unknown) => callback(event)
    ipcRenderer.on('desktop:runtime:event', listener)
    return () => ipcRenderer.removeListener('desktop:runtime:event', listener)
  },
  onDisconnect: (callback: () => void) => {
    const listener = () => callback()
    ipcRenderer.on('desktop:runtime:disconnected', listener)
    return () => ipcRenderer.removeListener('desktop:runtime:disconnected', listener)
  },
}) : undefined
contextBridge.exposeInMainWorld('agentDesktop', Object.freeze({ version: 3 as const, runtime, startup, credentials, system, updates, runtimeHost }))
