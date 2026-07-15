import { describe, expect, it } from 'vitest'

// @ts-expect-error The build verifier is an executable ESM script without TypeScript declarations.
import { findForbiddenDesktopBundleText, findForbiddenDesktopModules } from '../../../scripts/verify-desktop-build.mjs'

describe('desktop artifact verifier', () => {
  it.each([
    'src/router/portalRoutes.ts?vue&type=script',
    'SRC\\COMPONENTS\\SAAS\\PORTALLAYOUT.VUE',
    'C:\\repo\\frontend\\src\\components\\DigitalEmployeeManager.vue?import',
    '/workspace/frontend/src/api/adminSubagent.ts#virtual',
  ])('rejects forbidden module path form %s', (modulePath) => {
    expect(findForbiddenDesktopModules({}, [modulePath])).not.toEqual([])
  })

  it('allows tenant-only modules', () => {
    expect(findForbiddenDesktopModules({}, ['src/components/saas/TenantLayout.vue'])).toEqual([])
  })

  it('rejects Portal credentials and direct sensitive localStorage persistence in built JavaScript', () => {
    expect(findForbiddenDesktopBundleText('const token = "portal_token"')).toContain('portal_token')
    expect(findForbiddenDesktopBundleText("localStorage.getItem('demo_token')")).toContain('sensitive-localStorage')
    expect(findForbiddenDesktopBundleText("localStorage.getItem(key)")).toEqual([])
  })
})
