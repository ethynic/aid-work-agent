import { reactive, readonly, type DeepReadonly } from 'vue'
import type { DesktopAuthSession } from '@shared/auth/contracts'
import { createApiResolver, createFetchTransport, HttpStatusError } from '@shared/platform/contracts'
import { createDesktopAuthService, type DesktopAuthService } from '@desktop/services/auth'
import { compareVersions, initialStartupState, reduceStartup, type StartupEvent, type StartupState } from '@desktop/state/startup'

interface BootstrapResponse {
  minimum_supported_version?: string
}

const BOOTSTRAP_TIMEOUT_MS = 5000

export interface DesktopController {
  state: DeepReadonly<StartupState>
  auth: DesktopAuthService
  start(): Promise<void>
  retryConnectivity(): Promise<void>
  authenticated(session: DesktopAuthSession): void
  signOut(): Promise<void>
}

async function fetchWithTimeout(url: string, timeoutMs = 5000): Promise<Response> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { cache: 'no-store', credentials: 'omit', signal: controller.signal })
  } finally {
    window.clearTimeout(timeout)
  }
}

type DesktopBridge = NonNullable<Window['agentDesktop']>
type DesktopBridgeV3 = DesktopBridge & {
  readonly version: 3
  readonly startup: NonNullable<DesktopBridge['startup']>
  readonly updates: NonNullable<DesktopBridge['updates']>
}

export function createDesktopController(bridge: DesktopBridgeV3): DesktopController {
  const mutableState = reactive(initialStartupState())
  const resolver = createApiResolver(bridge.runtime.apiBaseUrl)
  const auth = createDesktopAuthService(bridge.credentials, resolver, createFetchTransport())
  const dispatch = (event: StartupEvent) => Object.assign(mutableState, reduceStartup(mutableState, event))
  let connectivityProbe = 0

  const retryConnectivity = async () => {
    const probe = ++connectivityProbe
    try {
      const response = await fetchWithTimeout(new URL('/health', bridge.runtime.apiBaseUrl).toString())
      if (probe !== connectivityProbe) return
      if (!response.ok) {
        dispatch({ type: 'CONNECTIVITY_CHANGED', connectivity: 'offline' })
        return
      }
      try {
          const bootstrapController = new AbortController()
          const timeout = window.setTimeout(() => bootstrapController.abort(), BOOTSTRAP_TIMEOUT_MS)
          let bootstrap: BootstrapResponse
          try {
            bootstrap = await createFetchTransport().request<BootstrapResponse>(resolver.resolve('/desktop/bootstrap'), { signal: bootstrapController.signal })
          } finally {
            window.clearTimeout(timeout)
          }
          if (!bootstrap || typeof bootstrap !== 'object') throw new Error('版本策略响应格式无效，无法安全启动。')
          const minimum = bootstrap.minimum_supported_version
          if (minimum !== undefined) {
            if (typeof minimum !== 'string') throw new Error('版本策略响应格式无效，无法安全启动。')
            const currentVersion = (await bridge.updates.getState()).currentVersion
            if (probe !== connectivityProbe) return
            if (compareVersions(currentVersion, minimum) < 0) {
              dispatch({ type: 'UPDATE_REQUIRED', currentVersion, minimumVersion: minimum })
            }
          }
        } catch (error) {
          // Phase C 服务端允许尚未提供可选 bootstrap；不可达性已由 health 状态明确展示。
          if (error instanceof HttpStatusError) {
            // HTTP 响应证明服务可达；404 表示可选端点尚未部署，其他状态也不能伪装成网络离线。
          } else if (error instanceof TypeError || (error instanceof DOMException && error.name === 'AbortError')) {
            if (probe === connectivityProbe) dispatch({ type: 'CONNECTIVITY_CHANGED', connectivity: 'offline' })
            return
          } else {
            if (probe === connectivityProbe) dispatch({ type: 'FATAL_LOCAL', message: error instanceof Error ? error.message : '版本策略校验失败。' })
            return
          }
        }
      if (probe === connectivityProbe) dispatch({ type: 'CONNECTIVITY_CHANGED', connectivity: 'online' })
    } catch {
      if (probe === connectivityProbe) dispatch({ type: 'CONNECTIVITY_CHANGED', connectivity: 'offline' })
    }
  }

  return {
    state: readonly(mutableState),
    auth,
    async start() {
      try {
        const startup = await bridge.startup.getState()
        if (!startup.secureStorageAvailable) throw new Error('系统安全凭证存储不可用。请解锁系统密钥存储后重启应用。')
        dispatch({ type: 'SECURE_STORE_READY' })
        const session = await auth.restore()
        dispatch(session ? { type: 'AUTHENTICATED', session } : { type: 'AUTH_REQUIRED' })
        await retryConnectivity()
      } catch (error) {
        dispatch({ type: 'FATAL_LOCAL', message: error instanceof Error ? error.message : '本地安全启动失败。' })
      }
    },
    retryConnectivity,
    authenticated(session) { dispatch({ type: 'AUTHENTICATED', session }) },
    async signOut() {
      await auth.logout()
      dispatch({ type: 'SIGNED_OUT' })
    },
  }
}
