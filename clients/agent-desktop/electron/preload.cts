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
  versions: Object.freeze({ electron: process.versions.electron, chrome: process.versions.chrome }),
})

const bridgeVersion = Number(readArgument('aidagent-bridge-version'))
if (bridgeVersion !== 1) throw new Error('unsupported Agent Desktop bridge version')

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

contextBridge.exposeInMainWorld('agentDesktop', Object.freeze({ version: 1 as const, runtime, credentials, system }))
