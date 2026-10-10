import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'
import DesktopShell from '@desktop/components/DesktopShell.vue'

describe('Desktop Shell', () => {
  it('keeps shell status and accessible navigation visible at minimum viewport', async () => {
    Object.defineProperty(window, 'innerWidth', { value: 720, configurable: true })
    Object.defineProperty(window, 'innerHeight', { value: 500, configurable: true })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<section><h1>Workspace</h1></section>' } }] })
    await router.push('/'); await router.isReady()
    const wrapper = mount(DesktopShell, { props: { username: 'Ada', tenantCode: 'ACME', connectivity: 'offline', platformLabel: 'Windows' }, global: { plugins: [router] } })
    expect(wrapper.get('aside[aria-label="主导航"]')).toBeTruthy()
    expect(wrapper.get('[role="status"]').text()).toContain('只读')
    expect(wrapper.get('#desktop-workspace').attributes('tabindex')).toBe('-1')
  })

})
