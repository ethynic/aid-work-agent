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

interface Window {
  readonly agentDesktop?: Readonly<{
    readonly version: 1
    readonly runtime: AgentDesktopRuntime
    readonly credentials: Readonly<{
      hydrate(): Promise<Record<string, string>>
      set(key: string, value: string): Promise<void>
      delete(key: string): Promise<void>
    }>
    readonly system: Readonly<{
      openExternal(url: string): Promise<void>
      saveDownload(input: Readonly<{ url: string; suggestedName: string; authorization?: string; tenantId?: string }>): Promise<Readonly<{ saved: boolean }>>
    }>
  }>
}
