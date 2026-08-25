/// <reference types="vite/client" />

type DesktopUpdateStatus = 'disabled' | 'idle' | 'checking' | 'available' | 'downloading' | 'downloaded' | 'up-to-date' | 'error'

interface DesktopUpdateState {
  status: DesktopUpdateStatus
  currentVersion: string
  availableVersion?: string
  percent?: number
  message?: string
}

interface Window {
  readonly agentDesktop?: Readonly<{
    readonly version: 3
    readonly runtime: Readonly<{
      target: 'desktop'
      platform: string
      schemeOrigin: 'aidagent://app'
      apiBaseUrl: string
      apiOrigin: string
      smokeMode: boolean
      versions: Readonly<{ electron: string; chrome: string }>
    }>
    readonly startup: Readonly<{
      getState(): Promise<Readonly<{ secureStorageAvailable: boolean; online: boolean }>>
    }>
    readonly credentials: import('@shared/platform/contracts').CredentialStore
    readonly system: Readonly<{
      openExternal(url: string): Promise<void>
      saveDownload(input: Readonly<{ url: string; suggestedName: string; authorization?: string; tenantId?: string }>): Promise<Readonly<{ saved: boolean }>>
    }>
    readonly updates: Readonly<{
      getState(): Promise<DesktopUpdateState>
      onState(callback: (state: DesktopUpdateState) => void): () => void
      check(): Promise<void>
      download(): Promise<void>
      restartAndInstall(): Promise<void>
    }>
  }>
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}
