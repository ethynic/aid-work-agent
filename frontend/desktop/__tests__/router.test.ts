import { describe, expect, it } from 'vitest'
import { createDesktopRouter } from '@desktop/router'

describe('Desktop router', () => {
  it('contains only implemented Desktop routes and redirects legacy deep links', async () => {
    const router = createDesktopRouter()
    expect(router.getRoutes().filter((route) => route.name).map((route) => route.name)).toEqual(['desktop-home'])
    await router.push('/t/acme/chat')
    await router.isReady()
    expect(router.currentRoute.value.path).toBe('/')
  })
})
