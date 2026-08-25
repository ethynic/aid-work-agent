import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDesktopController } from '@desktop/app/controller'

function makeBridge(overrides: { secureStorageAvailable?: boolean; online?: boolean; hydrate?: () => Promise<Record<string, string>>; currentVersion?: string } = {}) {
  return {
    version: 3,
    runtime: { target: 'desktop', platform: 'win32', schemeOrigin: 'aidagent://app', apiBaseUrl: 'https://api.example/api', apiOrigin: 'https://api.example', smokeMode: false, versions: { electron: '43', chrome: '1' } },
    startup: { getState: vi.fn(async () => ({ secureStorageAvailable: overrides.secureStorageAvailable ?? true, online: overrides.online ?? true })) },
    credentials: { hydrate: overrides.hydrate ?? vi.fn(async () => ({})), set: vi.fn(async () => undefined), delete: vi.fn(async () => undefined) },
    system: { openExternal: vi.fn(), saveDownload: vi.fn() },
    updates: { getState: vi.fn(async () => ({ status: 'idle', currentVersion: overrides.currentVersion ?? '1.0.0' })), onState: vi.fn(), check: vi.fn(), download: vi.fn(), restartAndInstall: vi.fn() },
  } as never
}

afterEach(() => vi.unstubAllGlobals())

describe('Desktop controller startup', () => {
  it('fails locally before credential access when secure storage is unavailable', async () => {
    const hydrate = vi.fn(async () => ({}))
    const controller = createDesktopController(makeBridge({ secureStorageAvailable: false, hydrate }))
    await controller.start()
    expect(controller.state).toMatchObject({ phase: 'fatal-local', connectivity: 'offline' })
    expect(hydrate).not.toHaveBeenCalled()
  })

  it('fails locally when encrypted credential hydration fails', async () => {
    const controller = createDesktopController(makeBridge({ hydrate: vi.fn(async () => { throw new Error('credential decrypt failed') }) }))
    await controller.start()
    expect(controller.state).toMatchObject({ phase: 'fatal-local', message: 'credential decrypt failed' })
  })

  it('keeps authentication available with an explicit offline state when the API is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
    const controller = createDesktopController(makeBridge({ online: false }))
    await controller.start()
    expect(controller.state).toMatchObject({ phase: 'auth-required', connectivity: 'offline' })
  })

  it('blocks an authenticated renderer when the server minimum version is newer', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = input.toString()
      if (url.endsWith('/health')) return new Response('{}', { status: 200 })
      return new Response(JSON.stringify({ minimum_supported_version: '2.0.0' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const controller = createDesktopController(makeBridge({ currentVersion: '1.5.0' }))
    await controller.start()
    expect(controller.state).toMatchObject({ phase: 'update-required', connectivity: 'online', currentVersion: '1.5.0', minimumVersion: '2.0.0' })
  })

  it('keeps HTTP service failures distinct from network offline state', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      if (input.toString().endsWith('/health')) return new Response('{}', { status: 200 })
      return new Response('{}', { status: 503 })
    }))
    const controller = createDesktopController(makeBridge())
    await controller.start()
    expect(controller.state).toMatchObject({ phase: 'auth-required', connectivity: 'online' })
  })

  it('rechecks the minimum-version gate when an offline login recovers', async () => {
    let online = false
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      if (input.toString().endsWith('/health')) {
        if (!online) throw new TypeError('offline')
        return new Response('{}', { status: 200 })
      }
      return new Response(JSON.stringify({ minimum_supported_version: '2.0.0' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const controller = createDesktopController(makeBridge({ currentVersion: '1.0.0' }))
    await controller.start()
    expect(controller.state).toMatchObject({ phase: 'auth-required', connectivity: 'offline' })
    online = true
    await controller.retryConnectivity()
    expect(controller.state).toMatchObject({ phase: 'update-required', connectivity: 'online' })
  })

  it('fails closed when the minimum-version policy is malformed', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      if (input.toString().endsWith('/health')) return new Response('{}', { status: 200 })
      return new Response(JSON.stringify({ minimum_supported_version: 'release-2' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const controller = createDesktopController(makeBridge())
    await controller.start()
    expect(controller.state).toMatchObject({ phase: 'fatal-local', connectivity: 'offline' })
  })
})
