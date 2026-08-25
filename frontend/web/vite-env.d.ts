/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

declare module 'benz-amr-recorder' {
  export default class BenzAMRRecorder {
    constructor()
    initWithUrl(url: string): Promise<void>
    play(): void
    pause(): void
    stop(): void
    on(event: string, callback: () => void): void
  }
}

interface AgentDesktopRuntime {
  readonly target: 'desktop'
  readonly platform: string
  readonly schemeOrigin: 'aidagent://app'
  readonly apiBaseUrl: string
  readonly apiOrigin: string
  readonly smokeMode: boolean
  readonly versions: Readonly<{ electron: string; chrome: string }>
}

type AgentDesktopUpdateState = Readonly<{
  status: 'disabled' | 'idle' | 'checking' | 'available' | 'downloading' | 'downloaded' | 'up-to-date' | 'error'
  currentVersion: string
  availableVersion?: string
  percent?: number
  message?: string
}>

interface Window {
  readonly agentDesktop?: Readonly<{
    readonly version: 1 | 2 | 3
    readonly runtime: AgentDesktopRuntime
    readonly credentials: Readonly<{
      hydrate(): Promise<Record<string, string>>
      set(key: string, value: string): Promise<void>
      delete(key: string): Promise<void>
    }>
    readonly startup?: Readonly<{
      getState(): Promise<Readonly<{ secureStorageAvailable: boolean; online: boolean }>>
    }>
    readonly system: Readonly<{
      openExternal(url: string): Promise<void>
      saveDownload(input: Readonly<{ url: string; suggestedName: string; authorization?: string; tenantId?: string }>): Promise<Readonly<{ saved: boolean }>>
    }>
    readonly updates?: Readonly<{
      getState(): Promise<AgentDesktopUpdateState>
      onState(callback: (state: AgentDesktopUpdateState) => void): () => void
      check(): Promise<void>
      download(): Promise<void>
      restartAndInstall(): Promise<void>
    }>
  }>
}
