import { afterEach, describe, expect, it, vi } from 'vitest'

import { mountDesktopConnectivity, readDesktopRuntime, renderSecureStartupFailure, resolveHealthUrl } from '@/platform/desktopRuntime'

afterEach(() => {
  Reflect.deleteProperty(window, 'agentDesktop')
  document.querySelector('#desktop-connectivity')?.remove()
  document.querySelector('#app')?.remove()
  vi.unstubAllGlobals()
})

describe('desktop runtime bridge', () => {
  it('shows a safe local failure instead of leaving a blank renderer when secure hydrate fails', () => {
    const app = document.createElement('div')
    app.id = 'app'
    document.body.append(app)
    renderSecureStartupFailure()
    expect(app.querySelector('[role="alert"]')?.textContent).toContain('安全凭证无法加载')
  })

  it('uses the validated preload runtime so Desktop does not fall back to a build-time API address', () => {
    Object.defineProperty(window, 'agentDesktop', {
      configurable: true,
      value: Object.freeze({
        version: 1,
        runtime: Object.freeze({
          target: 'desktop',
          platform: 'win32',
          schemeOrigin: 'aidagent://app',
          apiBaseUrl: 'https://api.example.test/api',
          smokeMode: false,
          versions: Object.freeze({ electron: '43', chrome: '144' })
        })
      })
    })

    expect(readDesktopRuntime()).toEqual({
      target: 'desktop',
      apiBaseUrl: 'https://api.example.test/api',
      smokeMode: false
    })
    expect(resolveHealthUrl('https://api.example.test/api')).toBe('https://api.example.test/health')
  })

  it('fails loud when preload is unavailable instead of silently using an unsafe fallback', () => {
    expect(() => readDesktopRuntime()).toThrow(/runtime bridge is unavailable/)
  })

  it('keeps the local Agent UI mounted when API is offline and lets the user retry', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce({ ok: true })
    vi.stubGlobal('fetch', fetchMock)
    const app = document.createElement('div')
    app.id = 'app'
    document.body.append(app)

    mountDesktopConnectivity('https://api.example.test/api')
    await vi.waitFor(() => {
      expect(document.querySelector<HTMLElement>('#desktop-connectivity')?.style.display).toBe('flex')
    })
    document.querySelector<HTMLButtonElement>('#desktop-connectivity button')?.click()
    await vi.waitFor(() => {
      expect(document.querySelector<HTMLElement>('#desktop-connectivity')?.style.display).toBe('none')
    })
    expect(document.querySelector('#app')).not.toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
