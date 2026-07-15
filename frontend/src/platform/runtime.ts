export type RuntimeTarget = 'web' | 'desktop'

export interface PlatformRuntime {
  target: RuntimeTarget
  apiBaseUrl: string
}

let runtime: PlatformRuntime = {
  target: 'web',
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL || '/api'
}

export function configureRuntime(nextRuntime: PlatformRuntime): void {
  runtime = { ...nextRuntime }
}

export function getRuntime(): Readonly<PlatformRuntime> {
  return runtime
}
