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
